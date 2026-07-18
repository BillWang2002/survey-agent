"""
Multi-page navigation state machine for survey automation.

Handles page transitions, "Next" / "Submit" button detection,
progress tracking, and graceful survey completion detection.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from survey_agent.utils.logger import get_logger

logger = get_logger(__name__)


class PageState(Enum):
    """States for the survey navigation state machine."""

    LOADING = auto()          # Page is loading
    READY = auto()            # Page loaded, ready to scan & fill
    FILLING = auto()          # Agent is filling in answers
    VALIDATING = auto()       # Checking for validation errors
    NAVIGATING = auto()       # Clicking "Next" / "Submit"
    COMPLETED = auto()        # Survey finished successfully
    FAILED = auto()           # Survey rejected / disqualified / closed
    ERROR = auto()            # Unrecoverable error encountered
    AWAITING_HUMAN = auto()   # Blocked by CAPTCHA, waiting for human


class SurveyStateMachine:
    """
    Tracks the state of a multi-page survey filling session.

    Detects page transitions, tracks progress, and handles
    the survey completion flow (including final submission).

    Usage:
        sm = SurveyStateMachine(max_pages=50)
        sm.transition(PageState.READY)

        # After filling a page:
        action = await sm.determine_next_action(page)
        if action == "next":
            await click_next_button(page)
        elif action == "submit":
            await click_submit_button(page)
        elif action == "completed":
            # Survey is done
            pass
    """

    # Common "next page" button selectors across survey platforms
    NEXT_BUTTON_SELECTORS = [
        # Text-based buttons
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button[data-ui-id]:has-text("Next")',
        '.next-button',
        '.survey-next',
        '#NextButton',
        '[data-action="next"]',
        'button[aria-label="Next"]',
        # Icon/arrow navigation (no visible text — common on platforms like Roy Morgan)
        'button[class*="next"]',
        'button[class*="arrow"]',
        'button[class*="forward"]',
        'button[class*="continue"]',
        'button[aria-label*="Next" i]',
        'button[aria-label*="next" i]',
        'button[aria-label*="Continue" i]',
        'a[class*="next"]',
        'a[class*="arrow"]',
        '[role="button"][aria-label*="next" i]',
    ]

    # Common "submit" / "finish" button selectors
    SUBMIT_BUTTON_SELECTORS = [
        'button:has-text("Submit")',
        'button:has-text("Finish")',
        'input[type="submit"]',
        '.submit-button',
        '.survey-submit',
        '#SubmitButton',
        '[data-action="submit"]',
    ]

    # Common "completion" indicators
    COMPLETION_INDICATORS = [
        'text=Thank you',
        'text=Survey complete',
        '.survey-complete',
        '.completion-message',
        '.thank-you',
    ]

    # Common "failure" / "rejection" / "disqualification" indicators
    # These mean the survey has ended but NOT successfully — the respondent
    # was screened out, the quota is full, or the survey is closed.
    FAILURE_INDICATORS: list[tuple[str, str]] = [
        # --- Disqualified / not eligible ---
        ("not eligible", "You are not eligible for this survey"),
        ("do not qualify", "You do not qualify for this survey"),
        ("screened out", "You have been screened out"),
        # --- Already participated / duplicate ---
        ("already participated", "You have already participated in this survey"),
        ("already completed", "You have already completed this survey"),
        ("duplicate", "Duplicate submission detected"),
        ("thank you for your interest", "Rejection: thank you for your interest but..."),
        # --- Quota full ---
        ("quota full", "The quota for this survey is full"),
        ("quota filled", "Target quota has been filled"),
        # --- Survey closed / ended ---
        ("survey is closed", "This survey is now closed"),
        ("survey has ended", "This survey has ended"),
        ("no longer accepting", "This survey is no longer accepting responses"),
    ]

    def __init__(self, max_pages: int = 50) -> None:
        self._state: PageState = PageState.LOADING
        self._current_page: int = 0
        self._max_pages: int = max_pages
        self._page_history: list[str] = []  # Track URLs to detect loops
        self._error_count: int = 0
        self._max_errors: int = 3

    # -- Properties --

    @property
    def state(self) -> PageState:
        return self._state

    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def is_completed(self) -> bool:
        return self._state == PageState.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self._state == PageState.FAILED

    @property
    def needs_human(self) -> bool:
        return self._state == PageState.AWAITING_HUMAN

    # -- State transitions --

    def transition(self, new_state: PageState) -> None:
        """Transition to a new state with logging."""
        old_state = self._state
        self._state = new_state
        logger.info(
            f"State transition: {old_state.name} → {new_state.name} "
            f"(page {self._current_page})"
        )

    def mark_page_loaded(self, url: str) -> None:
        """Record that a new page has been loaded."""
        self._current_page += 1
        self._page_history.append(url)
        logger.info(f"Page {self._current_page} loaded: {url}")

        if self._current_page > self._max_pages:
            logger.error(
                f"Exceeded max pages ({self._max_pages}). Possible infinite loop."
            )
            self.transition(PageState.ERROR)
        else:
            self.transition(PageState.READY)

    def record_error(self) -> None:
        """Record an error occurrence. Transitions to ERROR if threshold exceeded."""
        self._error_count += 1
        if self._error_count >= self._max_errors:
            logger.error(
                f"Error threshold reached ({self._error_count}/{self._max_errors})"
            )
            self.transition(PageState.ERROR)
        else:
            logger.warning(
                f"Error recorded ({self._error_count}/{self._max_errors})"
            )

    def reset_errors(self) -> None:
        """Reset the error counter (e.g., after successful recovery)."""
        self._error_count = 0

    # -- Page analysis --

    async def check_completion(self, page: Any) -> bool:
        """
        Check whether the survey has been completed.

        Looks for thank-you messages, completion indicators, etc.

        Args:
            page: Playwright Page object.

        Returns:
            True if the survey appears to be completed.
        """
        from survey_agent.perception.injector import DETECT_ERRORS_SCRIPT

        for indicator in self.COMPLETION_INDICATORS:
            try:
                el = page.locator(indicator).first
                if await el.is_visible(timeout=1000):
                    logger.info(f"Completion indicator found: '{indicator}'")
                    self.transition(PageState.COMPLETED)
                    return True
            except Exception:
                continue

        return False

    async def check_failure(self, page: Any) -> tuple[bool, str]:
        """
        Check whether the current page indicates survey failure / rejection.

        Scans both the page body text and individual selectors for known
        rejection / disqualification / quota-full / duplicate-submission
        messages.

        Args:
            page: Playwright Page object.

        Returns:
            Tuple of (is_failure, reason). If is_failure is True, reason
            contains the human-readable failure explanation; otherwise
            reason is an empty string.
        """
        try:
            body_text = await page.locator("body").inner_text()
            body_lower = body_text.lower()
        except Exception:
            return False, ""

        # Scan for known failure keywords in the page body
        matched_keywords: list[str] = []
        for keyword, explanation in self.FAILURE_INDICATORS:
            kw_lower = keyword.lower()
            # Use simple substring matching — survey rejection messages
            # are usually plain text on the page
            if kw_lower in body_lower:
                matched_keywords.append(keyword)
                logger.info(
                    f"  ⚠️  Failure indicator found: '{keyword}' → {explanation}"
                )

        if not matched_keywords:
            return False, ""

        # Build a human-readable failure reason
        # Try to extract the most specific sentence containing the keyword
        reason_parts: list[str] = []
        for kw in matched_keywords:
            for line in body_text.splitlines():
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                if kw.lower() in line_stripped.lower():
                    if line_stripped not in reason_parts:
                        reason_parts.append(line_stripped)
                    break

        reason = "\n".join(reason_parts) if reason_parts else f"Rejection keywords detected: {', '.join(matched_keywords)}"

        # Categorize the failure type for the summary
        failure_type = self._categorize_failure(body_lower)
        logger.warning(f"Survey failure detected: {failure_type}")
        logger.warning(f"Failure reason: {reason}")

        self.transition(PageState.FAILED)
        return True, reason

    @staticmethod
    def _categorize_failure(body_lower: str) -> str:
        """
        Categorize the failure into a human-readable type.

        Args:
            body_lower: Lowercased page body text.

        Returns:
            A failure category string.
        """
        disqualify_kw = [
            "not eligible", "do not qualify", "screened out",
        ]
        duplicate_kw = [
            "already participated", "already completed",
            "duplicate",
        ]
        quota_kw = [
            "quota full", "quota filled",
        ]
        closed_kw = [
            "survey is closed", "survey has ended",
            "no longer accepting",
        ]

        for kw in disqualify_kw:
            if kw in body_lower:
                return "Disqualified — respondent does not meet target criteria"
        for kw in duplicate_kw:
            if kw in body_lower:
                return "Duplicate submission — already participated in this survey"
        for kw in quota_kw:
            if kw in body_lower:
                return "Quota full — no longer accepting new responses"
        for kw in closed_kw:
            if kw in body_lower:
                return "Survey closed / expired"

        return "Survey rejected (reason unknown)"

    async def find_next_button(self, page: Any) -> str | None:
        """
        Find and return the selector for the Next/Submit button on the current page.

        Returns a `data-ui-id` reference when available, otherwise a CSS selector.
        Returns None if no navigation button is found.
        """
        # Check submit button first (might be the last page)
        for selector in self.SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    # Try to get its data-ui-id
                    ui_id = await btn.get_attribute("data-ui-id")
                    logger.info(f"Submit button found: {selector}")
                    return ui_id or selector
            except Exception:
                continue

        # Then check next button
        for selector in self.NEXT_BUTTON_SELECTORS:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    ui_id = await btn.get_attribute("data-ui-id")
                    logger.info(f"Next button found: {selector}")
                    return ui_id or selector
            except Exception:
                continue

        logger.warning("No navigation button found on current page.")
        return None

    async def click_next(self, page: Any) -> bool:
        """
        Attempt to click the "Next" button and wait for page transition.

        Returns True if a page transition occurred.
        """
        self.transition(PageState.NAVIGATING)

        button_selector = await self.find_next_button(page)
        if button_selector is None:
            logger.error("Cannot navigate: no Next/Submit button found.")
            self.transition(PageState.ERROR)
            return False

        try:
            current_url = page.url

            # Click the button
            if button_selector.startswith("ui-id-"):
                await page.click(f'[data-ui-id="{button_selector}"]')
            else:
                await page.click(button_selector)

            # Wait for page transition
            await page.wait_for_load_state("networkidle", timeout=10000)

            # Check if URL changed
            new_url = page.url
            if new_url != current_url:
                logger.info(f"Page transitioned: {current_url} → {new_url}")
                self.mark_page_loaded(new_url)
                return True

            # URL might not change on SPA-based surveys — rely on completion check
            logger.info("URL unchanged (possible SPA). Checking for completion...")
            completed = await self.check_completion(page)
            if completed:
                return True

            # Assume the page content updated
            self.transition(PageState.READY)
            return True

        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            self.record_error()
            return False

    # -- Summary --

    def get_summary(self) -> dict:
        """Return a summary of the session."""
        return {
            "state": self._state.name,
            "pages_visited": self._current_page,
            "errors_encountered": self._error_count,
            "page_history": self._page_history[-5:],  # Last 5 pages
            "is_completed": self.is_completed,
        }
