"""
Playwright browser lifecycle manager.

Handles browser launch, context creation, page management,
cookie persistence, anti-detection measures, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from survey_agent.utils.logger import get_logger

if TYPE_CHECKING:
    from survey_agent.config import BrowserConfig

logger = get_logger(__name__)

# Default storage directory for browser state (cookies, localStorage)
DEFAULT_STORAGE_DIR = Path("./browser_state")


class BrowserManager:
    """
    Manages the full lifecycle of a Playwright browser session.

    Features:
    - Headless / visible mode toggle
    - Anti-detection via stealth-like configuration
    - Cookie / storage state persistence
    - Automatic viewport & locale configuration
    - Graceful shutdown with cleanup

    Usage:
        async with BrowserManager(config) as manager:
            page = await manager.new_page()
            await page.goto("https://example.com")
    """

    def __init__(
        self,
        config: BrowserConfig,
        storage_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._storage_dir = storage_dir or DEFAULT_STORAGE_DIR
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def browser(self) -> Browser:
        """Get the underlying Playwright Browser instance."""
        if self._browser is None:
            raise RuntimeError("Browser not launched. Call start() first.")
        return self._browser

    @property
    def context(self) -> BrowserContext:
        """Get the default BrowserContext."""
        if self._context is None:
            raise RuntimeError("Browser context not created. Call start() first.")
        return self._context

    async def start(self) -> None:
        """
        Launch the browser and create a persistent context.

        Configures anti-detection measures including:
        - Realistic viewport size
        - Common browser locale & timezone
        - WebDriver flag removal (via Chromium args)
        """
        logger.info("Launching Playwright browser...")
        self._playwright = await async_playwright().start()

        # Browser launch arguments for anti-detection
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
        ]

        # In visible mode, start maximized for a natural browser feel
        if not self._config.headless:
            launch_args.append("--start-maximized")

        self._browser = await self._playwright.chromium.launch(
            headless=self._config.headless,
            slow_mo=self._config.slow_mo,
            args=launch_args,
        )

        # Ensure storage directory exists
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        storage_file = str(self._storage_dir / "state.json")

        # Try to load persisted storage state
        storage_state = None
        if Path(storage_file).exists():
            try:
                storage_state = storage_file
                logger.info("Loaded persisted browser state (cookies, etc.)")
            except Exception:
                logger.warning("Failed to load browser state, starting fresh.")

        # Build context options
        context_options: dict = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "storage_state": storage_state,
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        }

        if self._config.headless:
            # Headless mode: use fixed viewport for deterministic screenshots
            context_options["viewport"] = {
                "width": self._config.viewport_width,
                "height": self._config.viewport_height,
            }
        else:
            # Visible mode: no fixed viewport — let the browser window
            # determine the natural size (fixes weird scaling on Retina displays)
            context_options["no_viewport"] = True

        self._context = await self._browser.new_context(**context_options)

        # Apply stealth-like evasion scripts
        await self._apply_stealth_scripts()

        viewport_desc = (
            f"{self._config.viewport_width}x{self._config.viewport_height}"
            if self._config.headless
            else "auto (window-sized)"
        )
        logger.info(
            f"Browser launched (headless={self._config.headless}, "
            f"viewport={viewport_desc})"
        )

    async def _apply_stealth_scripts(self) -> None:
        """Inject scripts to hide automation traces from detection."""
        if self._context is None:
            return

        # Override navigator.webdriver
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false,
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en'],
            });
            // Override chrome runtime
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
        """)

    async def new_page(self) -> Page:
        """
        Create and return a new page in the default context.

        Sets reasonable default timeouts.
        """
        if self._context is None:
            await self.start()

        page = await self._context.new_page()  # type: ignore[union-attr]
        page.set_default_timeout(self._config.navigation_timeout)
        return page

    async def save_storage_state(self) -> None:
        """Persist cookies and localStorage to disk."""
        if self._context is None:
            return

        storage_file = str(self._storage_dir / "state.json")
        try:
            await self._context.storage_state(path=storage_file)
            logger.info(f"Browser state saved to {storage_file}")
        except Exception as e:
            logger.warning(f"Failed to save browser state: {e}")

    async def close(self) -> None:
        """Gracefully close the browser and save state."""
        try:
            await self.save_storage_state()
        except Exception:
            pass

        try:
            if self._context:
                await self._context.close()
        except Exception as e:
            logger.debug(f"Error closing context: {e}")

        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            logger.debug(f"Error closing browser: {e}")

        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.debug(f"Error stopping playwright: {e}")

        logger.info("Browser closed.")

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def navigate_to_blank(self, page: Page) -> None:
        """
        Navigate to about:blank — a clean starting page for interactive mode
        where the user will manually navigate to the survey.
        """
        logger.info("Navigating to about:blank (interactive mode)")
        await page.goto("about:blank", wait_until="domcontentloaded")

    async def navigate_with_timeout(
        self, page: Page, url: str, timeout_ms: int | None = None
    ) -> None:
        """
        Navigate to a URL with a configurable timeout.

        Args:
            page: Playwright Page to navigate.
            url: Target URL.
            timeout_ms: Timeout in milliseconds. Uses config default if None.
        """
        timeout = timeout_ms or self._config.navigation_timeout
        logger.info(f"Navigating to: {url}")
        try:
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception as e:
            logger.warning(f"Navigation to {url} had issues: {e}")
            # Don't fail — the page may still be usable
