"""
Feedback Loop — detects validation errors after form submissions and
retries with corrected actions via the LLM.

This is a critical component for robustness. When the agent clicks "Next"
and the page shows validation errors (e.g., "This field is required", "Please select an option"),
the feedback loop:
  1. Detects the error messages on the page
  2. Feeds them back to the LLM as context
  3. Gets a new decision with corrections
  4. Re-executes the corrected actions
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from playwright.async_api import Page

from survey_agent.perception.injector import DETECT_ERRORS_SCRIPT
from survey_agent.utils.logger import get_logger
from survey_agent.decision.response_parser import ActionDict

logger = get_logger(__name__)

# Type aliases for the perceive → decide → execute pipeline functions.
# All three are async (return Awaitable), reflecting how agent.py passes
# its bound coroutine methods into the feedback loop.
PerceiveFunc = Callable[[Any], Awaitable[dict[str, Any]]]
DecideFunc = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]
ExecuteFunc = Callable[[list[ActionDict]], Awaitable[None]]


class FeedbackLoop:
    """
    Detects form validation errors and orchestrates LLM retry with error context.

    Flow:
      1. After executing actions, scan the page for error indicators.
      2. If errors found, feed them into the next LLM call.
      3. Execute corrected actions.
      4. Repeat up to max_retries times.

    Usage:
        loop = FeedbackLoop(page, max_retries=3)
        has_errors = await loop.detect_and_retry(
            perceive_fn, decide_fn, execute_fn, layout, requirements
        )
    """

    def __init__(
        self,
        page: Page,
        max_retries: int = 3,
    ) -> None:
        self._page = page
        self._max_retries = max_retries
        self._error_history: list[dict] = []

    @property
    def error_history(self) -> list[dict]:
        """List of all errors encountered during this session."""
        return self._error_history

    async def detect_errors(self) -> list[dict]:
        """
        Scan the current page for validation error messages.

        Returns:
            List of error dicts, each with:
              - selector: CSS selector that matched
              - text: Error message text
              - field_name: Associated field name (if found)
        """
        try:
            raw_result = await self._page.evaluate(DETECT_ERRORS_SCRIPT)
            import json
            result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result

            errors = result.get("errors", [])
            if errors:
                logger.info(f"🔍 Detected {len(errors)} validation error(s):")
                for err in errors:
                    logger.info(f"  - {err.get('text', 'unknown')}")
                    self._error_history.append(err)

            return errors

        except Exception as e:
            logger.warning(f"Error detection failed: {e}")
            return []

    async def detect_and_retry(
        self,
        perceive_fn: PerceiveFunc,
        decide_fn: DecideFunc,
        execute_fn: ExecuteFunc,
        layout_data: dict[str, Any],
        requirements: str,
    ) -> bool:
        """
        Full feedback loop: detect errors, report, retry with LLM correction.

        This is called AFTER the initial actions are executed and
        the "Next" button was clicked.

        Args:
            perceive_fn: Function to re-scan the page for current layout.
            decide_fn: Function to get LLM decision with error context.
            execute_fn: Function to execute actions.
            layout_data: Original layout data from the page.
            requirements: Original filling requirements.

        Returns:
            True if errors were detected (and possibly corrected), False if clean.
        """
        errors = await self.detect_errors()

        if not errors:
            return False

        # We have errors — attempt correction via LLM
        for attempt in range(1, self._max_retries + 1):
            logger.info(
                f"🔄 Feedback retry {attempt}/{self._max_retries}"
            )

            # Re-perceive the page (the UI may have changed after error display)
            try:
                current_layout = await perceive_fn(self._page)
            except Exception as e:
                logger.warning(f"Re-perception failed: {e}")
                current_layout = layout_data

            # Feed errors into the decision function
            try:
                # We pass errors via a special requirements augmentation
                error_requirements = self._build_error_requirements(
                    requirements, errors
                )
                decision = await decide_fn(
                    current_layout, error_requirements
                )
            except Exception as e:
                logger.error(f"LLM decision with error context failed: {e}")
                continue

            # Execute corrected actions
            actions = decision.get("actions", [])
            if not actions:
                logger.warning("No corrective actions from LLM.")
                continue

            await execute_fn(actions)

            # Check if errors are now resolved
            await asyncio.sleep(1.0)
            remaining_errors = await self.detect_errors()
            if not remaining_errors:
                logger.info("✅ All validation errors resolved!")
                return True

            errors = remaining_errors

        logger.warning(
            f"⚠️  Failed to resolve errors after {self._max_retries} retries."
        )
        return True  # Errors remain

    def _build_error_requirements(
        self, original_requirements: str, errors: list[dict]
    ) -> str:
        """Build augmented requirements that include error context."""
        parts = [original_requirements.strip()] if original_requirements else []

        parts.append("\n### ⚠️ Fix the following validation errors (must prioritize):")
        for i, err in enumerate(errors):
            text = err.get("text", "Unknown error")
            field = err.get("field_name", "")
            parts.append(f"{i + 1}. {text}")
            if field:
                parts.append(f"   Related question: {field}")

        return "\n".join(parts)
