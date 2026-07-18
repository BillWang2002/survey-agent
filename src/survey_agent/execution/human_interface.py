"""
Human-in-the-Loop Interface — handles cases where the automation hits
a CAPTCHA or other human-only challenge.

When the agent is blocked:
  1. Sends a webhook notification (if configured)
  2. Pauses automation and waits for manual resolution
  3. Resumes automatically once the human clears the challenge

For local development, falls back to console prompts.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from typing import Any, Awaitable, Callable

from survey_agent.utils.logger import get_logger

logger = get_logger(__name__)


class HumanInterface:
    """
    Manages human-in-the-loop intervention for CAPTCHA and verification challenges.

    Supports two modes:
    - Webhook mode: Sends HTTP POST to a configured webhook URL (e.g., Slack, DingTalk)
    - Console mode: Prompts the user in the terminal (default for local dev)

    Usage:
        hi = HumanInterface(webhook_url="https://hooks.slack.com/...")
        await hi.request_help(page_url, "CAPTCHA detected!")
        resolved = await hi.wait_for_resolution(timeout_seconds=300)
    """

    def __init__(self, webhook_url: str = "") -> None:
        self._webhook_url = webhook_url
        self._resolved_event = asyncio.Event()

    async def request_help(
        self,
        page_url: str,
        message: str,
        screenshot_path: str = "",
    ) -> None:
        """
        Request human assistance.

        Args:
            page_url: The URL where the agent is blocked.
            message: Description of what's needed.
            screenshot_path: Optional path to a screenshot of the current state.
        """
        logger.warning("=" * 60)
        logger.warning("🛑 HUMAN INTERVENTION REQUIRED")
        logger.warning(f"   URL: {page_url}")
        logger.warning(f"   Reason: {message}")
        if screenshot_path:
            logger.warning(f"   Screenshot: {screenshot_path}")
        logger.warning("=" * 60)

        if self._webhook_url:
            await self._send_webhook(page_url, message, screenshot_path)
        else:
            # Console mode
            self._prompt_console(page_url, message)

    async def wait_for_resolution(
        self,
        timeout_seconds: int = 300,
        auto_detect_fn: Callable[[], Awaitable[bool]] | None = None,
        poll_interval: float = 2.0,
    ) -> bool:
        """
        Wait for the human to resolve the challenge.

        Supports two resume modes:

        Mode A — Auto-detect (when auto_detect_fn is provided):
          Polls the page every `poll_interval` seconds. When the CAPTCHA
          element disappears from the DOM, automatically resumes.
          The user can also press Enter to force-resume at any time.

        Mode B — Manual (when auto_detect_fn is None):
          Waits for the user to press Enter in the console.

        Args:
            timeout_seconds: Maximum time to wait before giving up.
            auto_detect_fn: Async callable that returns True when CAPTCHA is resolved.
            poll_interval: Seconds between auto-detection polls.

        Returns:
            True if resolved, False if timed out.
        """
        if self._webhook_url:
            # Webhook mode: also poll for auto-detect if available
            if auto_detect_fn:
                return await self._poll_with_auto_detect(
                    timeout_seconds, auto_detect_fn, poll_interval
                )
            return await self._poll_webhook_resolution(timeout_seconds)
        else:
            # Console mode
            if auto_detect_fn:
                return await self._wait_console_with_auto_detect(
                    timeout_seconds, auto_detect_fn, poll_interval
                )
            return await self._wait_console(timeout_seconds)

    def signal_resolved(self) -> None:
        """External signal that the challenge has been resolved."""
        self._resolved_event.set()
        logger.info("📢 Human intervention resolved!")

    # ------------------------------------------------------------------
    # Webhook mode
    # ------------------------------------------------------------------

    async def _send_webhook(
        self, url: str, message: str, screenshot_path: str
    ) -> None:
        """Send a webhook notification about the CAPTCHA."""
        payload = {
            "msgtype": "text",
            "text": {
                "content": (
                    f"⚠️ SurveyAgent requires human intervention\n\n"
                    f"Page: {url}\n"
                    f"Reason: {message}\n"
                    f"Please complete the verification in the browser. The system will auto-resume when done."
                )
            },
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"Webhook sent, status: {resp.status}")
        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")

    async def _poll_webhook_resolution(self, timeout_seconds: int) -> bool:
        """
        Wait for resolution via event signal.

        In production, this could poll an endpoint or use a callback server.
        For now, it waits for the signal_resolved() method to be called.
        """
        try:
            await asyncio.wait_for(
                self._resolved_event.wait(), timeout=timeout_seconds
            )
            return True
        except asyncio.TimeoutError:
            logger.error("Human intervention timed out.")
            return False

    # ------------------------------------------------------------------
    # Console mode (local development)
    # ------------------------------------------------------------------

    def _prompt_console(self, url: str, message: str) -> None:
        """Display a console prompt to the user."""
        print("\n" + "=" * 60)
        print("🛑 Human Intervention Required")
        print(f"   Page URL: {url}")
        print(f"   Reason: {message}")
        print(f"   Please complete the CAPTCHA / manual operation in the browser.")
        print(f"   When done, return to the terminal and press Enter to continue...")
        print("=" * 60 + "\n")

    async def _wait_console(self, timeout_seconds: int) -> bool:
        """Wait for the user to press Enter in the console."""
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, input, ">>> Press Enter to continue..."),
                timeout=timeout_seconds,
            )
            return True
        except asyncio.TimeoutError:
            logger.error("Console wait timed out.")
            return False

    async def _wait_console_with_auto_detect(
        self,
        timeout_seconds: int,
        auto_detect_fn: Callable[[], Awaitable[bool]],
        poll_interval: float,
    ) -> bool:
        """
        Console mode with auto-detection.

        Simultaneously:
          - Polls the page to check if CAPTCHA is gone
          - Listens for the user to press Enter

        Whichever fires first triggers the resume.
        """
        resolved_flag = threading.Event()
        user_input_flag = threading.Event()

        async def poll_loop() -> None:
            """Periodically check if CAPTCHA has been resolved."""
            deadline = asyncio.get_event_loop().time() + timeout_seconds
            while asyncio.get_event_loop().time() < deadline:
                try:
                    if await auto_detect_fn():
                        logger.info("✅ Auto-detected: CAPTCHA resolved!")
                        resolved_flag.set()
                        return
                except Exception as e:
                    logger.debug(f"Auto-detect poll error: {e}")
                await asyncio.sleep(poll_interval)
            logger.debug("Auto-detect poll timed out.")

        async def listen_input() -> None:
            """Listen for Enter key."""
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, input, ">>> Press Enter to continue (or wait for auto-detection)...")
                user_input_flag.set()
            except Exception:
                pass

        # Start auto-detect immediately
        poll_task = asyncio.ensure_future(poll_loop())

        # Show the prompt
        print("\n" + "=" * 60)
        print("🛑 Human Intervention Required — Auto-detecting verification completion...")
        print(f"   Timeout: {timeout_seconds} seconds")
        print("   Please complete the CAPTCHA directly in the browser.")
        print("   The system will auto-resume when done, or press Enter to resume immediately.")
        print("=" * 60)

        # Wait for Enter key
        try:
            await asyncio.wait_for(listen_input(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            pass

        # If user pressed Enter, proceed immediately
        if user_input_flag.is_set():
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            logger.info("📢 User pressed Enter — resuming.")
            return True

        # If auto-detect fired
        if resolved_flag.is_set():
            logger.info("📢 Auto-detection confirmed CAPTCHA resolved — resuming.")
            return True

        # Both timed out
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        logger.error("Human intervention timed out after %s seconds.", timeout_seconds)
        return False

    async def _poll_with_auto_detect(
        self,
        timeout_seconds: int,
        auto_detect_fn: Callable[[], Awaitable[bool]],
        poll_interval: float,
    ) -> bool:
        """
        Webhook mode with auto-detection polling as backup.

        Waits for either the webhook callback signal or auto-detection.
        """
        deadline = asyncio.get_event_loop().time() + timeout_seconds

        while asyncio.get_event_loop().time() < deadline:
            # Check resolved event (from webhook callback)
            if self._resolved_event.is_set():
                logger.info("📢 Webhook callback signaled — resuming.")
                return True

            # Check auto-detect
            try:
                if await auto_detect_fn():
                    logger.info("✅ Auto-detected: CAPTCHA resolved!")
                    return True
            except Exception as e:
                logger.debug(f"Auto-detect poll error: {e}")

            await asyncio.sleep(poll_interval)

        logger.error("Webhook + auto-detect timed out after %s seconds.", timeout_seconds)
        return False
