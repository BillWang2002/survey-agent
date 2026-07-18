"""
Tests for the response_parser module — validates LLM JSON output parsing.
"""

import pytest
from survey_agent.decision.response_parser import ResponseParser, ResponseParseError


class TestResponseParser:
    """Test the ResponseParser for various valid and invalid LLM outputs."""

    def setup_method(self) -> None:
        self.parser = ResponseParser()

    def test_parse_valid_decision(self, sample_decision: dict) -> None:
        """A valid CONTINUE decision with click and navigate actions."""
        result = self.parser.parse(sample_decision)
        assert result["status"] == "CONTINUE"
        assert result["thought"] == "Single-choice question, 3 options. Select Blue."
        assert len(result["actions"]) == 2

    def test_parse_finished_decision(self, sample_decision_finished: dict) -> None:
        """A FINISHED decision with submit action."""
        result = self.parser.parse(sample_decision_finished)
        assert result["status"] == "FINISHED"
        assert len(result["actions"]) == 1

    def test_missing_thought_defaults_to_empty(self) -> None:
        """Decision without thought field should default to empty string."""
        result = self.parser.parse({"status": "CONTINUE", "actions": []})
        assert result["thought"] == ""

    def test_invalid_status_defaults_to_continue(self) -> None:
        """Unknown status should be defaulted to CONTINUE."""
        result = self.parser.parse({
            "thought": "test",
            "status": "UNKNOWN_STATUS",
            "actions": [],
        })
        assert result["status"] == "CONTINUE"

    def test_invalid_action_type_skipped(self) -> None:
        """Actions with unknown types should be skipped with a warning."""
        result = self.parser.parse({
            "thought": "test",
            "status": "CONTINUE",
            "actions": [
                {"type": "invalid_type", "ui_id": "ui-id-0"},
            ],
        })
        assert len(result["actions"]) == 0  # Skipped

    def test_action_missing_ui_id_skipped(self) -> None:
        """Click action without ui_id should be skipped."""
        result = self.parser.parse({
            "thought": "test",
            "status": "CONTINUE",
            "actions": [
                {"type": "click"},
            ],
        })
        assert len(result["actions"]) == 0

    def test_navigate_action_no_ui_id_needed(self) -> None:
        """Navigate actions don't require ui_id."""
        result = self.parser.parse({
            "thought": "test",
            "status": "CONTINUE",
            "actions": [
                {"type": "navigate", "action": "next"},
            ],
        })
        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "navigate"
        assert result["actions"][0]["action"] == "next"

    def test_slider_percentage_validation(self) -> None:
        """Slider percentage must be between 0 and 100."""
        result = self.parser.parse({
            "thought": "test",
            "status": "CONTINUE",
            "actions": [
                {"type": "slider", "ui_id": "ui-id-0", "percentage": 80},
            ],
        })
        assert result["actions"][0]["percentage"] == 80

    def test_response_not_dict_raises_error(self) -> None:
        """Non-dict input should raise ResponseParseError."""
        with pytest.raises(ResponseParseError):
            self.parser.parse("not a dict")  # type: ignore[arg-type]

    def test_empty_actions_array(self) -> None:
        """Empty actions array should be accepted."""
        result = self.parser.parse({
            "thought": "nothing to do",
            "status": "FINISHED",
            "actions": [],
        })
        assert result["status"] == "FINISHED"
        assert result["actions"] == []

    def test_fill_action_with_value(self) -> None:
        """Fill action should preserve the value field."""
        result = self.parser.parse({
            "thought": "fill name",
            "status": "CONTINUE",
            "actions": [
                {"type": "fill", "ui_id": "ui-id-0", "value": "John Doe"},
            ],
        })
        assert result["actions"][0]["value"] == "John Doe"

    def test_select_action_with_value(self) -> None:
        """Select action should preserve the value field."""
        result = self.parser.parse({
            "thought": "select city",
            "status": "CONTINUE",
            "actions": [
                {"type": "select", "ui_id": "ui-id-0", "value": "New York"},
            ],
        })
        assert result["actions"][0]["value"] == "New York"

    def test_extract_errors_for_feedback(self) -> None:
        """extract_errors_for_feedback should identify issues."""
        errors = self.parser.extract_errors_for_feedback({
            "status": "INVALID",
            "actions": "not_a_list",
        })
        assert len(errors) > 0
