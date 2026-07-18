"""
Screenshot manager — captures page screenshots for debugging and audit trails.

Every screenshot is timestamped and saved with the current page number,
enabling easy correlation with the decision log.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from survey_agent.utils.logger import get_logger

logger = get_logger(__name__)


class ScreenshotManager:
    """
    Captures and manages screenshots during survey automation.

    Usage:
        sm = ScreenshotManager(enabled=True, log_dir=Path("./logs"))
        await sm.capture(page, "page_001_before_fill")
    """

    def __init__(
        self,
        enabled: bool = True,
        log_dir: Path = Path("./logs"),
    ) -> None:
        self._enabled = enabled
        self._screenshot_dir = log_dir / "screenshots"
        self._counter = 0

        if self._enabled:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def disable(self) -> None:
        self._enabled = False

    async def capture(
        self,
        page: Any,
        label: str = "",
        full_page: bool = True,
    ) -> str | None:
        """
        Capture a screenshot of the current page.

        Args:
            page: Playwright Page object.
            label: Human-readable label for the screenshot filename.
            full_page: Capture the full scrollable page, not just viewport.

        Returns:
            Path to the saved screenshot, or None if disabled or failed.
        """
        if not self._enabled:
            return None

        self._counter += 1
        timestamp = datetime.now().strftime("%H%M%S")
        safe_label = label.replace(" ", "_").replace("/", "_") if label else ""
        filename = f"{self._counter:04d}_{timestamp}_{safe_label}.png"
        filepath = self._screenshot_dir / filename

        try:
            await page.screenshot(
                path=str(filepath),
                full_page=full_page,
            )
            logger.debug(f"📸 Screenshot: {filename}")
            return str(filepath)
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
            return None

    async def capture_error_state(
        self,
        page: Any,
        error_info: str = "",
    ) -> str | None:
        """
        Capture a screenshot specifically for an error/debug scenario.

        Uses a distinctive filename prefix.
        """
        self._counter += 1
        timestamp = datetime.now().strftime("%H%M%S")
        error_summary = error_info[:50].replace(" ", "_") if error_info else "unknown"
        filename = f"ERROR_{self._counter:04d}_{timestamp}_{error_summary}.png"
        filepath = self._screenshot_dir / filename

        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info(f"📸 Error screenshot: {filename}")
            return str(filepath)
        except Exception as e:
            logger.warning(f"Failed to capture error screenshot: {e}")
            return None
