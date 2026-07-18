"""
Response Parser — validates and normalizes the JSON output from DeepSeek V4 Pro.

Ensures the LLM response conforms to the expected schema:
{
    "thought": "...",
    "status": "CONTINUE" | "FINISHED",
    "actions": [
        {"type": "click"|"fill"|"slider"|"select"|"navigate", "ui_id": "...", ...}
    ]
}

Provides clear error messages when the LLM output is malformed.
"""

from __future__ import annotations

from typing import Any, TypedDict

from survey_agent.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

class ActionDict(TypedDict, total=False):
    """Expected shape of a single action in the LLM response."""
    type: str
    ui_id: str
    reason: str
    value: str
    percentage: int
    action: str  # For "navigate" type: "next" | "submit"


class HumanRequestDict(TypedDict, total=False):
    """Details for human intervention when status is NEED_HUMAN."""
    reason: str
    description: str
    affected_elements: list[str]


class DecisionDict(TypedDict):
    """Expected shape of the full LLM decision response."""
    thought: str
    status: str
    actions: list[ActionDict]
    human_request: HumanRequestDict | None


# ---------------------------------------------------------------------------
# Valid action types
# ---------------------------------------------------------------------------

VALID_ACTION_TYPES = {"click", "fill", "slider", "select", "navigate"}
VALID_STATUSES = {"CONTINUE", "FINISHED", "NEED_HUMAN"}
VALID_NAVIGATE_ACTIONS = {"next", "submit"}


class ResponseParseError(Exception):
    """Raised when the LLM response cannot be parsed or validated."""
    pass


class ResponseParser:
    """
    Parses and validates LLM JSON responses against the action schema.

    Provides informative error messages and attempts graceful recovery
    from common LLM output issues (extra fields, missing fields, etc.).

    Usage:
        parser = ResponseParser()
        decision = parser.parse(llm_response_dict)
        for action in decision["actions"]:
            executor.execute(action)
    """

    def parse(self, raw_response: dict[str, Any]) -> DecisionDict:
        """
        Parse and validate a raw LLM response dict.

        Args:
            raw_response: The JSON dict from the LLM.

        Returns:
            Validated DecisionDict.

        Raises:
            ResponseParseError: If the response is structurally invalid.
        """
        # Validate top-level structure
        if not isinstance(raw_response, dict):
            raise ResponseParseError(
                f"Expected JSON object, got {type(raw_response).__name__}"
            )

        thought = raw_response.get("thought", "")
        if not thought:
            logger.warning("LLM response missing 'thought' field.")

        status = raw_response.get("status", "CONTINUE")
        if status not in VALID_STATUSES:
            logger.warning(
                f"Unknown status '{status}', defaulting to 'CONTINUE'. "
                f"Valid: {VALID_STATUSES}"
            )
            status = "CONTINUE"

        # Handle NEED_HUMAN status — no actions required, human_request expected
        if status == "NEED_HUMAN":
            human_request_data = raw_response.get("human_request", {})
            human_request = None
            if isinstance(human_request_data, dict):
                human_request = HumanRequestDict(
                    reason=human_request_data.get("reason", ""),
                    description=human_request_data.get("description", ""),
                    affected_elements=human_request_data.get("affected_elements", []),
                )
            logger.info(
                f"LLM requested human intervention: "
                f"{human_request.get('reason', 'No reason given') if human_request else 'No details'}"
            )
            return DecisionDict(
                thought=thought,
                status="NEED_HUMAN",
                actions=[],
                human_request=human_request,
            )

        actions = raw_response.get("actions", [])

        if status == "FINISHED" and not actions:
            # FINISHED with no actions is valid (survey is done)
            return DecisionDict(thought=thought, status=status, actions=[])

        if not actions:
            logger.warning("LLM returned no actions. Is the survey complete?")
            # Default: try to navigate to next page
            return DecisionDict(
                thought=thought,
                status="FINISHED" if status == "FINISHED" else "CONTINUE",
                actions=[],
            )

        # Validate each action
        validated_actions = []
        for i, action in enumerate(actions):
            try:
                validated = self._validate_action(action, i)
                validated_actions.append(validated)
            except ResponseParseError as e:
                logger.warning(f"Skipping invalid action #{i}: {e}")
                continue

        return DecisionDict(
            thought=thought,
            status=status,
            actions=validated_actions,
        )

    def _validate_action(
        self, action: dict[str, Any], index: int
    ) -> ActionDict:
        """Validate a single action dict."""
        if not isinstance(action, dict):
            raise ResponseParseError(f"Action #{index} is not a dict: {action}")

        action_type = action.get("type", "").lower().strip()

        if action_type not in VALID_ACTION_TYPES:
            raise ResponseParseError(
                f"Action #{index}: unknown type '{action_type}'. "
                f"Valid types: {VALID_ACTION_TYPES}"
            )

        # --- Type-specific validation ---
        if action_type == "navigate":
            nav_action = action.get("action", "next").lower().strip()
            if nav_action not in VALID_NAVIGATE_ACTIONS:
                raise ResponseParseError(
                    f"Action #{index}: invalid navigate action '{nav_action}'"
                )
            return ActionDict(
                type="navigate",
                action=nav_action,
                reason=action.get("reason", ""),
            )

        ui_id = action.get("ui_id", "")
        if not ui_id or not ui_id.startswith("ui-id-"):
            raise ResponseParseError(
                f"Action #{index}: missing or invalid ui_id '{ui_id}'"
            )

        if action_type == "fill":
            return ActionDict(
                type="fill",
                ui_id=ui_id,
                value=action.get("value", ""),
                reason=action.get("reason", ""),
            )

        if action_type == "slider":
            percentage = action.get("percentage", 50)
            if not isinstance(percentage, (int, float)) or not 0 <= percentage <= 100:
                raise ResponseParseError(
                    f"Action #{index}: slider percentage must be 0-100, got {percentage}"
                )
            return ActionDict(
                type="slider",
                ui_id=ui_id,
                percentage=int(percentage),
                reason=action.get("reason", ""),
            )

        if action_type == "select":
            return ActionDict(
                type="select",
                ui_id=ui_id,
                value=action.get("value", ""),
                reason=action.get("reason", ""),
            )

        # click (default)
        return ActionDict(
            type="click",
            ui_id=ui_id,
            reason=action.get("reason", ""),
        )

    def extract_errors_for_feedback(
        self, raw_response: dict[str, Any]
    ) -> list[str]:
        """
        Extract human-readable error messages from a malformed response,
        suitable for feeding back into the LLM's next attempt.

        Args:
            raw_response: The (possibly invalid) LLM response.

        Returns:
            List of error descriptions.
        """
        errors = []

        if not isinstance(raw_response, dict):
            return ["Response was not a valid JSON object."]

        if "actions" not in raw_response:
            errors.append("Response missing 'actions' field.")
        elif not isinstance(raw_response["actions"], list):
            errors.append("'actions' field must be an array.")

        if "thought" not in raw_response:
            errors.append("Response missing 'thought' field.")

        status = raw_response.get("status", "")
        if status and status not in VALID_STATUSES:
            errors.append(
                f"Invalid status '{status}'. Must be CONTINUE or FINISHED."
            )

        for i, action in enumerate(raw_response.get("actions", [])):
            if not isinstance(action, dict):
                errors.append(f"Action #{i} is not an object.")
                continue
            action_type = action.get("type", "")
            if action_type not in VALID_ACTION_TYPES:
                errors.append(
                    f"Action #{i} has invalid type '{action_type}'."
                )

        return errors
