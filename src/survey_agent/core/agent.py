"""
Main SurveyAgent orchestrator — ties Perception, Transformation, Decision,
and Execution layers together into a coherent automation pipeline.

This is the heart of the system. It runs the main loop:
  1. Perceive the page (inject data-ui-id tags, extract layout)
  2. Transform the layout into a compact LLM-friendly format
  3. Decision: ask DeepSeek V4 Pro what actions to take
  4. Execute those actions via Playwright
  5. Feedback loop: detect errors, retry if needed
  6. Navigate to next page, or finish
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from survey_agent.core.state_machine import PageState, SurveyStateMachine
from survey_agent.perception.browser_manager import BrowserManager
from survey_agent.perception.injector import (
    CHECK_CAPTCHA_RESOLVED_SCRIPT,
    DETECT_CAPTCHA_SCRIPT,
    INJECT_AND_EXTRACT_SCRIPT,
    build_compact_layout,
    parse_layout_json,
)
from survey_agent.transformation.html_cleaner import HTMLCleaner
from survey_agent.transformation.layout_extractor import LayoutExtractor
from survey_agent.decision.llm_client import LLMClient
from survey_agent.decision.prompt_manager import PromptManager
from survey_agent.decision.response_parser import ResponseParser, ActionDict
from survey_agent.execution.action_executor import ActionExecutor
from survey_agent.execution.feedback_loop import FeedbackLoop
from survey_agent.execution.human_interface import HumanInterface
from survey_agent.utils.logger import cleanup_artifacts, get_logger, log_decision
from survey_agent.utils.screenshot import ScreenshotManager

if TYPE_CHECKING:
    from survey_agent.config import Config

logger = get_logger(__name__)


class SurveyAgent:
    """
    Intelligent web survey automation agent.

    Orchestrates the four-layer architecture:
      Perception → Transformation → Decision → Execution

    Usage:
        from survey_agent.config import config
        agent = SurveyAgent(config)
        result = await agent.run(
            url="https://example.com/survey",
            requirements="Select 'Agree' for all, fill 'N/A' for text"
        )
    """

    def __init__(self, config: Config) -> None:
        self._config = config

        # Initialize subsystems
        self._browser_manager = BrowserManager(config.browser)
        self._html_cleaner = HTMLCleaner()
        self._layout_extractor = LayoutExtractor()
        self._llm_client = LLMClient(config.llm)
        self._prompt_manager = PromptManager()
        self._response_parser = ResponseParser()
        self._action_executor: ActionExecutor | None = None
        self._feedback_loop: FeedbackLoop | None = None
        self._human_interface = HumanInterface(config.agent.human_interface_webhook)
        self._screenshot_manager = ScreenshotManager(
            enabled=config.agent.record,
            log_dir=Path(config.agent.log_dir),
        )
        self._state_machine = SurveyStateMachine(
            max_pages=config.agent.max_pages
        )

    async def run(
        self,
        url: str | None = None,
        requirements: str = "",
        *,
        interactive: bool = False,
    ) -> dict:
        """
        Run the survey automation against the target URL.

        Args:
            url: Target survey URL. Optional in interactive mode.
            requirements: Natural language filling instructions.
            interactive: If True, open browser and wait for user to manually
                         navigate to the survey before starting automation.

        Returns:
            Summary dict with status, pages filled, and statistics.
        """
        start_time = time.monotonic()
        logger.info(f"🚀 SurveyAgent starting: url={url}, interactive={interactive}")
        logger.info(f"📋 Requirements: {requirements[:200]}")

        try:
            async with self._browser_manager as browser_mgr:
                page = await browser_mgr.new_page()

                if interactive:
                    # Interactive mode: let user manually navigate to the survey
                    page, url = await self._interactive_start(browser_mgr, page, url)
                    if url is None:
                        return {
                            "status": "aborted",
                            "error": "User did not provide a valid starting page.",
                            "pages_filled": 0,
                        }
                else:
                    # Normal mode: navigate directly to the survey URL
                    assert url is not None, "URL is required in non-interactive mode"
                    await browser_mgr.navigate_with_timeout(page, url)

                # Create executor and feedback loop with the active page
                self._action_executor = ActionExecutor(
                    page, delay=self._config.browser.action_delay
                )
                self._feedback_loop = FeedbackLoop(
                    page,
                    max_retries=self._config.agent.feedback_max_retries,
                )

                self._state_machine.mark_page_loaded(page.url)

                # Enable tracing if configured
                if self._config.agent.enable_trace:
                    trace_path = Path(self._config.agent.trace_dir)
                    trace_path.mkdir(parents=True, exist_ok=True)
                    await page.context.tracing.start(
                        screenshots=True, snapshots=True
                    )
                    logger.info("Playwright tracing enabled")

                # --- Main automation loop ---
                while not self._state_machine.is_completed:
                    state = self._state_machine.state

                    if state == PageState.ERROR:
                        logger.error("State machine in ERROR state. Aborting.")
                        break

                    if state == PageState.AWAITING_HUMAN:
                        await self._handle_human_intervention(page)
                        continue

                    if state != PageState.READY:
                        # Wait a bit and re-check
                        await asyncio.sleep(0.5)
                        continue

                    # === Step 0: CAPTCHA / Human Verification Check ===
                    captcha_detected = await self._check_captcha(page)
                    if captcha_detected:
                        logger.warning("🛑 CAPTCHA / human verification detected!")
                        self._state_machine.transition(PageState.AWAITING_HUMAN)
                        continue

                    # === Step 1: Perceive & Transform ===
                    logger.info(f"--- Processing page {self._state_machine.current_page} ---")
                    layout_data = await self._perceive_page(page)

                    # Screenshot for debugging
                    await self._screenshot_manager.capture(
                        page,
                        f"page_{self._state_machine.current_page:03d}",
                    )

                    # === Step 2: Decision ===
                    decision = await self._decide(layout_data, requirements)

                    log_decision(
                        decision,
                        self._state_machine.current_page,
                        record=self._config.agent.record,
                    )

                    # === Step 3: Execute ===
                    actions = decision.get("actions", [])
                    status = decision.get("status", "CONTINUE")

                    if status == "FINISHED":
                        logger.info("LLM signaled survey completion.")
                        # Try to submit
                        await self._state_machine.click_next(page)
                        completed = await self._state_machine.check_completion(page)
                        if completed:
                            break
                        # If not actually completed, re-evaluate the page
                        continue

                    if status == "NEED_HUMAN":
                        # AI cannot handle this — delegate to human
                        await self._handle_need_human(page, decision)
                        # After human resolves, re-perceive and re-decide
                        continue

                    # Record URL before executing, so we can detect if
                    # the LLM's actions already navigated to the next page
                    page_url_before = page.url
                    await self._execute_actions(actions)

                    # === Step 4: Check if LLM already navigated to next page ===
                    # If the URL changed after executing actions, the LLM already
                    # handled navigation (e.g., via a navigate action or clicking
                    # a platform-specific arrow button). Skip the state machine's
                    # click_next to avoid double-navigating.
                    new_url_after_actions = page.url
                    if new_url_after_actions != page_url_before:
                        logger.info(
                            f"Page changed after actions: "
                            f"{page_url_before} → {new_url_after_actions}"
                        )
                        self._state_machine.mark_page_loaded(new_url_after_actions)
                        await asyncio.sleep(1.0)

                        # Check if this redirect indicates survey failure
                        is_failure, failure_reason = await self._check_survey_failure(page)
                        if is_failure:
                            failure_result = await self._handle_survey_failure(
                                page, failure_reason
                            )
                            return failure_result
                        continue

                    # === Step 5: Feedback Loop (error detection) ===
                    has_errors = await self._feedback_loop.detect_and_retry(
                        self._perceive_page,
                        self._decide,
                        self._execute_actions,
                        layout_data,
                        requirements,
                    )

                    if has_errors:
                        self._state_machine.record_error()
                    else:
                        self._state_machine.reset_errors()

                    # === Step 6: Navigate to next page (fallback) ===
                    navigated = await self._state_machine.click_next(page)
                    if not navigated:
                        # Navigation failed — don't abort.
                        # Re-perceive and let LLM try clicking a specific button
                        logger.warning(
                            "Navigate failed. Re-perceiving page for LLM retry..."
                        )
                        self._state_machine.reset_errors()
                        self._state_machine.transition(PageState.READY)
                        continue

                    # Small delay between pages
                    await asyncio.sleep(1.0)

                    # Check if the new page indicates survey failure (rejection,
                    # disqualification, quota full, duplicate submission, etc.)
                    is_failure, failure_reason = await self._check_survey_failure(page)
                    if is_failure:
                        failure_result = await self._handle_survey_failure(
                            page, failure_reason
                        )
                        return failure_result

                # --- End of main loop ---

                await self._browser_manager.save_storage_state()

                # Stop tracing
                if self._config.agent.enable_trace:
                    trace_file = str(
                        Path(self._config.agent.trace_dir) / "survey_trace.zip"
                    )
                    await page.context.tracing.stop(path=trace_file)
                    logger.info(f"Trace saved to {trace_file}")

        except Exception as e:
            logger.exception(f"Fatal error during survey run: {e}")
            return {
                "status": "error",
                "error": str(e),
                "pages_filled": self._state_machine.current_page,
            }

        elapsed = time.monotonic() - start_time
        is_completed = self._state_machine.is_completed
        summary = {
            "status": "completed" if is_completed else "aborted",
            "pages_filled": self._state_machine.current_page,
            "errors_encountered": self._state_machine.get_summary()["errors_encountered"],
            "time_elapsed_seconds": round(elapsed, 1),
            "url": url,
        }

        logger.info(f"✅ SurveyAgent finished: {summary}")

        # Auto-cleanup: on success, remove heavyweight artifacts
        # (screenshots + decision JSONs). Keep them on failure for debugging.
        # In non-record mode these dirs are empty anyway — this is a no-op.
        if is_completed and self._config.agent.record:
            cleanup_artifacts(self._config.agent.log_dir)
            logger.info("Recorded artifacts cleaned up after successful run.")

        return summary

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    async def _interactive_start(
        self,
        browser_mgr: Any,
        page: Any,
        initial_url: str | None,
    ) -> tuple[Any, str | None]:
        """
        Interactive mode startup: ask user for the survey URL, navigate to it,
        wait for user to log in, then return the active page and URL.

        Args:
            browser_mgr: BrowserManager instance.
            page: Playwright Page.
            initial_url: Optional starting URL from --url flag.

        Returns:
            Tuple of (active_page, current_url), or (page, None) if aborted.
        """
        # Step 1: Get the target URL from the user
        if initial_url:
            target_url = initial_url
        else:
            print()
            print("=" * 60)
            print("  🖐️  Interactive Mode")
            print()
            print("  Paste the survey URL (or type it in), then press Enter:")
            print("=" * 60)
            print()
            print(">>> ", end="")
            try:
                target_url = input().strip()
            except (EOFError, KeyboardInterrupt):
                print("\n⚠️  Cancelled by user.")
                return page, None

            if not target_url:
                print("❌ No URL provided. Exiting.")
                return page, None

        # Normalize URL: add https:// if no protocol specified
        if not target_url.startswith("http://") and not target_url.startswith("https://"):
            target_url = "https://" + target_url
            logger.info(f"Normalized URL: {target_url}")

        # Step 2: Navigate to the target URL
        logger.info(f"🌐 Interactive mode: navigating to: {target_url}")
        print(f"\n⏳ Navigating to: {target_url}")
        await browser_mgr.navigate_with_timeout(page, target_url)

        # Step 3: Wait for user to log in and reach the survey
        print()
        print("=" * 60)
        print("  ✅ Navigated to target page")
        print()
        print("  Now please complete the following:")
        print("  1. Log in to your account (if needed)")
        print("  2. Click into the survey (until you see the first question)")
        print("  3. ⚠️ Keep operations in this tab — do not open new tabs")
        print()
        print("  When ready, press Enter to begin auto-filling...")
        print("=" * 60)
        print()

        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("\n⚠️  Cancelled by user.")
            return page, None

        # Give any in-flight navigation a moment to settle
        await asyncio.sleep(0.5)

        # Find the active page
        active_page = await self._find_user_page(browser_mgr, page)
        current_url = active_page.url
        logger.info(f"👤 User ready. Current page URL: {current_url}")

        # Validate: make sure the page loaded
        if not current_url or current_url in ("about:blank", "about://blank"):
            logger.warning("Page still blank — navigation may have failed.")
            print()
            print("⚠️  Browser page is blank.")
            print("   Possible causes: unreachable address, network issue, or redirect failure.")
            print()
            print("   Please confirm you can see the page content in the browser, then press Enter to retry...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                return page, None

            await asyncio.sleep(0.5)
            active_page = await self._find_user_page(browser_mgr, page)
            current_url = active_page.url

            if not current_url or current_url in ("about:blank", "about://blank"):
                print("❌ Still no page content detected. Exiting. Please check the URL and retry.")
                return page, None

        print(f"\n✅ Starting auto-fill on current page: {current_url}\n")
        return active_page, current_url

    async def _find_user_page(self, browser_mgr: Any, original_page: Any) -> Any:
        """
        Find the page the user is likely working on.

        Checks all open pages in the browser context. If the user opened
        a new tab and navigated there, we switch to that tab. Otherwise
        we keep the original page.

        Args:
            browser_mgr: BrowserManager instance.
            original_page: The original Playwright Page.

        Returns:
            The most likely active page.
        """
        try:
            context = original_page.context
            all_pages = context.pages

            # If there's only one page, use it
            if len(all_pages) <= 1:
                return original_page

            logger.info(f"Found {len(all_pages)} open pages. Scanning for survey page...")

            # Look for a page that has navigated somewhere (not blank)
            for p in all_pages:
                url = p.url
                if url and url not in ("about:blank", "about://blank", ""):
                    logger.info(f"  → Switching to page: {url}")
                    # Bring this page to the front
                    await p.bring_to_front()
                    return p

            # If all pages are blank, check the most recently created one
            # (user might have opened a new tab but not navigated yet)
            if len(all_pages) > 1:
                newest = all_pages[-1]
                if newest != original_page:
                    logger.info("  → Using most recent page (still blank)")
                    await newest.bring_to_front()
                    return newest

        except Exception as e:
            logger.debug(f"Page scan error (non-fatal): {e}")

        return original_page

    async def _perceive_page(self, page: Any) -> dict[str, Any]:
        """
        Perception + Transformation: Inject tags, extract raw layout,
        clean HTML, and build a structured view of the page.

        Returns a dict with:
          - raw_layout: full interactive elements data
          - compact_text: token-efficient markdown for the LLM
          - cleaned_html: stripped-down HTML (optional)
        """
        # Step 1: Inject data-ui-id tags and extract layout
        raw_json = await page.evaluate(INJECT_AND_EXTRACT_SCRIPT)
        layout = parse_layout_json(raw_json)

        # Step 2: Build compact text representation
        compact_text = build_compact_layout(layout)

        # Step 3: Also get raw HTML for deeper cleaning if needed
        raw_html = await page.content()
        cleaned_html = self._html_cleaner.clean(raw_html)

        return {
            "raw_layout": layout,
            "compact_text": compact_text,
            "cleaned_html": cleaned_html,
            "element_count": len(layout.get("interactive_elements", [])),
        }

    async def _decide(
        self, layout_data: dict, requirements: str
    ) -> dict[str, Any]:
        """
        Decision layer: Send the page layout + requirements to DeepSeek V4 Pro
        and get back a structured action plan.
        """
        system_prompt = self._prompt_manager.get_system_prompt()
        user_prompt = self._prompt_manager.build_user_prompt(
            compact_layout=layout_data.get("compact_text", ""),
            requirements=requirements,
            page_number=self._state_machine.current_page,
            cleaned_html=layout_data.get("cleaned_html", ""),
        )

        # Get structured decision from LLM
        raw_response = await self._llm_client.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        # Parse and validate the response
        decision = self._response_parser.parse(raw_response)

        return cast(dict[str, Any], decision)

    async def _execute_actions(self, actions: list[ActionDict]) -> None:
        """Execute a list of actions via the ActionExecutor."""
        if not actions:
            logger.info("No actions to execute.")
            return

        assert self._action_executor is not None
        for i, action in enumerate(actions):
            logger.info(
                f"Executing action {i + 1}/{len(actions)}: "
                f"{action.get('type', '?')} → {action.get('ui_id', '?')}"
            )
            await self._action_executor.execute(action)

    async def _handle_human_intervention(self, page: Any) -> None:
        """
        Handle cases requiring human intervention (e.g., CAPTCHA).

        Flow:
          1. Take screenshot of the blocked state
          2. Notify user via console + optional webhook
          3. Wait for user to resolve (manual Enter or auto-detect)
          4. Verify CAPTCHA is gone, then resume
        """
        logger.warning("⚠️  Human intervention required (likely CAPTCHA).")
        await self._screenshot_manager.capture(page, "captcha_blocked")

        # Detect what kind of challenge this is
        try:
            raw = await page.evaluate(DETECT_CAPTCHA_SCRIPT)
            import json
            captcha_info = json.loads(raw) if isinstance(raw, str) else raw
            captcha_types = [c.get("type", "unknown") for c in captcha_info.get("captchas", [])]
            captcha_detail = ", ".join(captcha_types) if captcha_types else "unknown type"
        except Exception:
            captcha_detail = "unknown type"

        await self._human_interface.request_help(
            page_url=page.url,
            message=f"CAPTCHA verification challenge detected (type: {captcha_detail}). "
                    f"Please complete the verification in the browser window. "
                    f"The system will auto-resume when done.",
        )

        # Wait for human to resolve — with auto-detection
        result = await self._human_interface.wait_for_resolution(
            timeout_seconds=self._config.agent.captcha_timeout_seconds,
            auto_detect_fn=lambda: self._is_captcha_resolved(page),
            poll_interval=self._config.agent.captcha_poll_interval,
        )

        if result:
            logger.info("✅ Human verification resolved. Resuming automation...")
            self._state_machine.transition(PageState.READY)
        else:
            logger.error("Human intervention timed out after 5 minutes.")
            self._state_machine.transition(PageState.ERROR)

    async def _handle_need_human(self, page: Any, decision: dict) -> None:
        """
        Handle NEED_HUMAN status from the LLM — the AI cannot fill this
        question/page automatically and needs the user to take over.

        Args:
            page: Playwright Page.
            decision: LLM decision dict with human_request details.
        """
        human_request = decision.get("human_request") or {}
        reason = human_request.get("reason", "AI cannot auto-complete the current question")
        description = human_request.get("description", "Please manually complete the relevant steps in the browser.")
        affected = human_request.get("affected_elements", [])

        # Build a clear message for the user
        message_lines = [
            f"Reason: {reason}",
            f"",
            f"Instructions: {description}",
        ]
        if affected:
            message_lines.append(f"Related elements: {', '.join(affected)}")
        if decision.get("thought"):
            message_lines.append(f"")
            message_lines.append(f"AI Analysis: {decision['thought']}")

        full_message = "\n".join(message_lines)

        logger.warning(f"🤚 NEED_HUMAN: {reason}")
        logger.info(f"   Description: {description}")

        # Take screenshot for context
        await self._screenshot_manager.capture(page, "need_human")

        # Notify user via human interface
        await self._human_interface.request_help(
            page_url=page.url,
            message=full_message,
        )

        # Wait for user to resolve
        result = await self._human_interface.wait_for_resolution(
            timeout_seconds=self._config.agent.captcha_timeout_seconds,
        )

        if result:
            logger.info("✅ User resolved the issue. Resuming automation...")
        else:
            logger.warning("⏰ Human intervention timed out — will retry on next loop.")

    # ------------------------------------------------------------------
    # CAPTCHA detection helpers
    # ------------------------------------------------------------------

    async def _check_captcha(self, page: Any) -> bool:
        """
        Check whether the current page has a CAPTCHA / human verification challenge.

        Returns True if a challenge is detected and needs human intervention.
        """
        try:
            raw = await page.evaluate(DETECT_CAPTCHA_SCRIPT)
            import json
            result = json.loads(raw) if isinstance(raw, str) else raw

            # Log all findings for visibility
            captchas = result.get("captchas", [])
            for c in captchas:
                subtype = c.get('subtype', '')
                ctype = c.get('type', '')
                if ctype == 'captcha_indicator':
                    # Informational only — badge/checkbox anchor, not blocking
                    logger.info(
                        f"  ℹ️  CAPTCHA indicator (non-blocking): type={ctype}, "
                        f"subtype={subtype[:80]}"
                    )
                else:
                    logger.warning(
                        f"  🔍 CAPTCHA detected: type={ctype}, "
                        f"subtype={subtype[:80]}, "
                        f"text={c.get('text', '')[:60]}"
                    )

            if result.get("has_captcha"):
                return True

            # Log indicator-only presence at a higher level
            if captchas and not result.get("has_captcha"):
                logger.info(
                    "ℹ️  CAPTCHA service indicators present (badge/checkbox) "
                    "but no active challenge — continuing automation."
                )
        except Exception as e:
            logger.debug(f"CAPTCHA check skipped (error): {e}")

        return False

    async def _is_captcha_resolved(self, page: Any) -> bool:
        """
        Check whether a previously detected CAPTCHA has been resolved by the human.

        Returns True if the page is now clear of verification challenges.
        """
        try:
            raw = await page.evaluate(CHECK_CAPTCHA_RESOLVED_SCRIPT)
            return bool(raw)
        except Exception:
            # If check fails, assume not resolved yet
            return False

    # ------------------------------------------------------------------
    # Survey failure detection (rejection / disqualification / closed)
    # ------------------------------------------------------------------

    async def _check_survey_failure(self, page: Any) -> tuple[bool, str]:
        """
        Check whether the current page indicates the survey has rejected
        or disqualified the respondent.

        Called after every page transition (URL change or next-page click).

        Args:
            page: Playwright Page object.

        Returns:
            Tuple of (is_failure, failure_reason). If is_failure is True,
            failure_reason contains a human-readable explanation.
        """
        # Delegate to the state machine's content-based failure check
        is_failure, reason = await self._state_machine.check_failure(page)
        if is_failure:
            return True, reason

        # Additional heuristic: if the URL domain changed significantly
        # (e.g., survey platform redirected to its home page), treat as
        # potential failure and scan the page text more broadly.
        try:
            current_url = page.url
            if self._state_machine._page_history:
                # Compare with the original survey URL domain
                from urllib.parse import urlparse
                current_domain = urlparse(current_url).netloc
                original_url = self._state_machine._page_history[0]
                original_domain = urlparse(original_url).netloc

                if current_domain and original_domain:
                    if current_domain != original_domain:
                        logger.info(
                            f"Domain changed: {original_domain} → {current_domain}. "
                            f"Checking for cross-domain redirect failure..."
                        )
                        # Cross-domain redirect — scan page body for any
                        # failure-like text more aggressively
                        try:
                            body_text = await page.locator("body").inner_text()
                            body_lower = body_text.lower()

                            # Broader scan: look for any suspicious text
                            warning_keywords = [
                                "not eligible", "screened out", "closed",
                                "quota", "unfortunately", "sorry",
                                "no longer", "already completed",
                            ]
                            found = [kw for kw in warning_keywords
                                     if kw.lower() in body_lower]
                            if found:
                                return True, (
                                    f"Cross-domain redirect detected with suspicious keywords: {', '.join(found)}\n"
                                    f"Redirect target domain: {current_domain}\n"
                                    f"Page content excerpt: {body_text[:300]}"
                                )
                        except Exception:
                            pass
        except Exception:
            pass

        return False, ""

    async def _handle_survey_failure(
        self, page: Any, reason: str
    ) -> dict:
        """
        Handle a survey failure / rejection gracefully.

        - Takes a screenshot for the record
        - Logs the failure reason prominently
        - Prints a clear summary to the terminal
        - Keeps the browser open for manual inspection

        Args:
            page: Playwright Page object.
            reason: Human-readable failure explanation.

        Returns:
            A failure summary dict suitable for the run() return value.
        """
        failure_type = self._state_machine._categorize_failure(reason.lower())

        # Take a screenshot of the failure page
        await self._screenshot_manager.capture(page, "survey_failed")

        # Log prominently
        logger.error("=" * 60)
        logger.error("⚠️  SURVEY FAILURE DETECTED")
        logger.error(f"    Type: {failure_type}")
        logger.error(f"    URL: {page.url}")
        logger.error("=" * 60)
        for line in reason.splitlines():
            logger.error(f"    {line.strip()}")
        logger.error("=" * 60)

        # Print to terminal (user-facing, outside the log stream)
        print()
        print("=" * 60)
        print("  ⚠️  Survey Failed — browser will stay open for inspection")
        print("=" * 60)
        print()
        print(f"  📍 Failure type: {failure_type}")
        print(f"  🔗 Current page: {page.url}")
        print()
        if reason:
            print(f"  📝 Reason:")
            for line in reason.splitlines():
                print(f"     {line.strip()}")
        print()
        print("  💡 Possible causes:")
        print(f"     • You do not meet the survey's target demographic criteria (screened out)")
        print(f"     • You have already participated in this survey (duplicate rejection)")
        print(f"     • The survey quota is full / survey is closed / expired")
        print(f"     • The survey ended and redirected to another page")
        print()
        print("  🔍 The browser window has been kept open for manual inspection.")
        print("  ⌨️  Press Enter to close the browser...")
        print("=" * 60)
        print()

        # Wait for user to press Enter — keeps browser open for inspection
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        # Transition state machine to FAILED
        self._state_machine.transition(PageState.FAILED)

        return {
            "status": "failed",
            "failure_type": failure_type,
            "failure_reason": reason,
            "failure_url": page.url,
            "pages_filled": self._state_machine.current_page,
            "errors_encountered": self._state_machine.get_summary()["errors_encountered"],
        }