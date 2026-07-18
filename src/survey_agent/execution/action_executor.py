"""
Action Executor — translates LLM decision actions into real Playwright interactions.

Supports:
- click: Click on an element by data-ui-id
- fill: Type text into an input/textarea
- slider: Drag a slider to a target percentage
- select: Choose an option from a <select> dropdown
- navigate: Click Next/Submit buttons
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Page

from survey_agent.decision.response_parser import ActionDict
from survey_agent.utils.logger import get_logger

logger = get_logger(__name__)


class ActionExecutor:
    """
    Executes typed actions against a Playwright Page using data-ui-id selectors.

    Each action references a `data-ui-id` attribute that was injected by the
    perception layer's JS script, ensuring reliable element targeting.

    Usage:
        executor = ActionExecutor(page, delay=0.5)
        await executor.execute({"type": "click", "ui_id": "ui-id-3"})
    """

    def __init__(self, page: Page, delay: float = 0.5) -> None:
        self._page = page
        self._delay = delay
        self._action_count = 0

    @property
    def action_count(self) -> int:
        """Total number of actions executed in this session."""
        return self._action_count

    async def execute(self, action: ActionDict) -> bool:
        """
        Execute a single action.

        Args:
            action: Action dict with at least "type" and "ui_id" (except navigate).

        Returns:
            True if the action was executed successfully, False otherwise.
        """
        action_type = action.get("type", "")
        self._action_count += 1

        try:
            if action_type == "click":
                return await self._click(action)
            elif action_type == "fill":
                return await self._fill(action)
            elif action_type == "slider":
                return await self._slider(action)
            elif action_type == "select":
                return await self._select(action)
            elif action_type == "navigate":
                return await self._navigate(action)
            else:
                logger.warning(f"Unknown action type: {action_type}")
                return False
        except Exception as e:
            logger.error(
                f"Action #{self._action_count} failed "
                f"({action_type} → {action.get('ui_id', 'N/A')}): {e}"
            )
            return False
        finally:
            # Robustness delay between actions
            await asyncio.sleep(self._delay)

    # ------------------------------------------------------------------
    # Action type handlers
    # ------------------------------------------------------------------

    async def _click(self, action: ActionDict) -> bool:
        """Click an element by data-ui-id. Uses JS click for hidden/off-viewport elements."""
        ui_id = action.get("ui_id", "")
        selector = f'[data-ui-id="{ui_id}"]'

        try:
            # Strategy: Always use JS click — bypasses all visibility/viewport checks.
            # This is essential for survey platforms (FocusVision, Qualtrics, etc.)
            # that hide native inputs and use custom-styled labels.
            await self._page.evaluate(
                """
                (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;

                    // For hidden radio/checkbox inputs, click the visible label instead.
                    // Survey platforms commonly hide native <input> elements and style
                    // <label> wrappers to look like radio buttons / checkboxes.
                    if (el.tagName === 'INPUT' && (el.type === 'radio' || el.type === 'checkbox')) {
                        const cs = window.getComputedStyle(el);
                        const isHidden = (
                            cs.display === 'none' ||
                            cs.visibility === 'hidden' ||
                            parseFloat(cs.opacity) === 0 ||
                            el.classList.contains('fir-hidden') ||
                            el.classList.contains('hidden') ||
                            el.classList.contains('sr-only') ||
                            el.classList.contains('visually-hidden') ||
                            el.classList.contains('d-none') ||
                            el.offsetWidth === 0 ||
                            el.offsetHeight === 0
                        );
                        if (isHidden) {
                            // Try parent <label> first, then sibling <label for="...">
                            let label = el.closest('label');
                            if (!label && el.id) {
                                label = document.querySelector(`label[for="${el.id}"]`);
                            }
                            if (label) {
                                label.click();
                                return true;
                            }
                        }
                    }

                    el.click();
                    return true;
                }
                """,
                selector,
            )
            await asyncio.sleep(0.15)
            logger.info(f"✓ Clicked (JS): {ui_id} ({action.get('reason', '')})")
            return True

        except Exception as e:
            logger.error(f"Click failed for {ui_id}: {e}")
            return False

    async def _fill(self, action: ActionDict) -> bool:
        """Fill text into an input or textarea."""
        ui_id = action.get("ui_id", "")
        value = action.get("value", "")
        selector = f'[data-ui-id="{ui_id}"]'

        try:
            await self._page.locator(selector).scroll_into_view_if_needed()
            await asyncio.sleep(0.1)

            # Clear existing content first
            await self._page.fill(selector, "")
            await asyncio.sleep(0.05)

            # Type with realistic delay
            await self._page.fill(selector, value)
            logger.info(
                f"✓ Filled: {ui_id} = '{value[:50]}{'...' if len(value) > 50 else ''}' "
                f"({action.get('reason', '')})"
            )
            return True

        except Exception as e:
            # Fallback: try type() character by character
            logger.debug(f"fill() failed, trying type(): {e}")
            try:
                await self._page.locator(selector).click()
                await self._page.locator(selector).fill("")
                await self._page.type(selector, value, delay=30)
                logger.info(f"✓ Typed into: {ui_id}")
                return True
            except Exception as e2:
                logger.error(f"Fill failed for {ui_id}: {e2}")
                return False

    async def _slider(self, action: ActionDict) -> bool:
        """
        Drag a slider element to a target percentage position.

        Calculates the target X coordinate from the slider's bounding box
        and performs a mouse drag to that position.
        """
        ui_id = action.get("ui_id", "")
        percentage = action.get("percentage", 50)
        selector = f'[data-ui-id="{ui_id}"]'

        try:
            await self._page.locator(selector).scroll_into_view_if_needed()
            await asyncio.sleep(0.15)

            # Get the slider's bounding box
            box = await self._page.locator(selector).bounding_box()
            if box is None:
                logger.error(f"Slider {ui_id} has no bounding box.")
                return False

            # Calculate target position
            target_x = box["x"] + (box["width"] * (percentage / 100.0))
            target_y = box["y"] + (box["height"] / 2)

            # Perform drag from left edge to target
            start_x = box["x"] + 2  # Small offset from edge
            start_y = target_y

            await self._page.mouse.move(start_x, start_y)
            await self._page.mouse.down()
            await self._page.mouse.move(
                target_x, target_y, steps=10
            )  # Smooth drag in 10 steps
            await self._page.mouse.up()

            logger.info(
                f"✓ Slider: {ui_id} → {percentage}% "
                f"({action.get('reason', '')})"
            )
            return True

        except Exception as e:
            logger.error(f"Slider action failed for {ui_id}: {e}")
            return False

    async def _select(self, action: ActionDict) -> bool:
        """
        Select an option from a <select> dropdown.

        Supports selecting by value or by visible label text.
        """
        ui_id = action.get("ui_id", "")
        value = action.get("value", "")
        selector = f'[data-ui-id="{ui_id}"]'

        try:
            await self._page.locator(selector).scroll_into_view_if_needed()
            await asyncio.sleep(0.1)

            # Try selecting by value first
            try:
                await self._page.select_option(selector, value=value)
                logger.info(
                    f"✓ Selected (by value): {ui_id} = '{value}' "
                    f"({action.get('reason', '')})"
                )
                return True
            except Exception:
                pass

            # Fallback: select by label text
            await self._page.select_option(selector, label=value)
            logger.info(
                f"✓ Selected (by label): {ui_id} = '{value}' "
                f"({action.get('reason', '')})"
            )
            return True

        except Exception as e:
            logger.error(f"Select failed for {ui_id}: {e}")
            return False

    async def _navigate(self, action: ActionDict) -> bool:
        """
        Click a navigation button (Next / Submit).

        Uses common selectors to find and click the appropriate button.
        """
        nav_action = action.get("action", "next")

        if nav_action == "submit":
            submit_selectors = [
                'button:has-text("Submit")',
                'button:has-text("Finish")',
                'input[type="submit"]',
                '[data-action="submit"]',
                '.submit-button',
                '#SubmitButton',
            ]
            selectors = submit_selectors
        else:
            next_selectors = [
                # Text-based buttons (standard)
                'button:has-text("Next")',
                'button:has-text("Continue")',
                '[data-action="next"]',
                '.next-button',
                '#NextButton',
                # Icon/arrow navigation (no visible text)
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
            selectors = next_selectors

        for selector in selectors:
            try:
                btn = self._page.locator(selector).first
                if await btn.is_visible(timeout=1000):
                    await btn.scroll_into_view_if_needed()
                    await btn.click(delay=50)
                    logger.info(f"✓ Navigate ({nav_action}): {selector}")
                    return True
            except Exception:
                continue

        # Fallback: look for any button whose text suggests navigation
        try:
            all_buttons = self._page.locator("button")
            count = await all_buttons.count()
            for i in range(count):
                btn = all_buttons.nth(i)
                text = (await btn.inner_text()).strip()
                if nav_action == "submit" and any(
                    kw in text for kw in ["Submit", "Finish"]
                ):
                    await btn.click(delay=50)
                    logger.info(f"✓ Navigate ({nav_action}): button '{text}'")
                    return True
                if nav_action == "next" and any(
                    kw in text for kw in ["Next", "Continue"]
                ):
                    await btn.click(delay=50)
                    logger.info(f"✓ Navigate ({nav_action}): button '{text}'")
                    return True
        except Exception as e:
            logger.debug(f"Button fallback search failed: {e}")

        logger.warning(f"No {nav_action} button found.")
        return False
