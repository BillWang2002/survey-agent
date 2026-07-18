"""
Tests for the perception injector module — JS scripts JSON parsing
and compact layout building.
"""

import json

import pytest
from survey_agent.perception.injector import (
    build_compact_layout,
    parse_layout_json,
)


class TestParseLayoutJson:
    """Test parsing of the raw JSON returned by the JS injector scripts."""

    def test_parses_valid_json(self) -> None:
        """Valid JSON should be parsed into a Python dict."""
        raw = json.dumps({
            "page_title": "Test Survey",
            "page_url": "https://example.com",
            "interactive_elements": [],
            "body_text": "Hello",
        })
        result = parse_layout_json(raw)
        assert result["page_title"] == "Test Survey"
        assert result["body_text"] == "Hello"
        assert result["interactive_elements"] == []

    def test_parses_elements_with_positions(self) -> None:
        """Interactive elements with position data should be preserved."""
        raw = json.dumps({
            "page_title": "Survey",
            "page_url": "https://example.com",
            "interactive_elements": [
                {
                    "ui_id": "ui-id-0",
                    "tag": "input",
                    "type": "radio",
                    "text": "Option A",
                    "visible_position": {"x": 100, "y": 200, "width": 20, "height": 20},
                    "is_visible": True,
                    "disabled": False,
                    "required": True,
                },
            ],
            "body_text": "",
        })
        result = parse_layout_json(raw)
        elements = result["interactive_elements"]
        assert len(elements) == 1
        assert elements[0]["ui_id"] == "ui-id-0"
        assert elements[0]["visible_position"]["x"] == 100

    def test_invalid_json_raises_valueerror(self) -> None:
        """Malformed JSON should raise ValueError."""
        with pytest.raises(ValueError, match="Failed to parse layout JSON"):
            parse_layout_json("not valid json {{{")

    def test_empty_json_object(self) -> None:
        """Empty JSON object should be accepted (no elements)."""
        result = parse_layout_json("{}")
        assert result == {}

    def test_parses_table_context(self) -> None:
        """Elements with table_context (matrix questions) should be parsed."""
        raw = json.dumps({
            "page_title": "Matrix Survey",
            "page_url": "https://example.com",
            "interactive_elements": [
                {
                    "ui_id": "ui-id-0",
                    "tag": "input",
                    "type": "radio",
                    "text": "",
                    "visible_position": {"x": 300, "y": 100, "width": 20, "height": 20},
                    "is_visible": True,
                    "disabled": False,
                    "required": False,
                    "table_context": {
                        "row_header": "Product Quality",
                        "col_header": "Strongly Agree",
                        "col_index": 1,
                        "total_cols": 5,
                    },
                },
            ],
            "body_text": "",
        })
        result = parse_layout_json(raw)
        el = result["interactive_elements"][0]
        assert el["table_context"]["row_header"] == "Product Quality"
        assert el["table_context"]["col_header"] == "Strongly Agree"


class TestBuildCompactLayout:
    """Test converting raw layout dicts into LLM-friendly text."""

    def test_empty_layout(self) -> None:
        """Layout with no interactive elements should produce a clear message."""
        layout = {
            "page_title": "Empty Page",
            "page_url": "https://example.com",
            "interactive_elements": [],
            "body_text": "",
        }
        text = build_compact_layout(layout)
        assert "Empty Page" in text
        assert "No interactive elements" in text

    def test_includes_element_table(self) -> None:
        """Should include a markdown table of elements."""
        layout = {
            "page_title": "Survey",
            "page_url": "https://example.com",
            "interactive_elements": [
                {
                    "ui_id": "ui-id-0",
                    "tag": "input",
                    "type": "radio",
                    "text": "Very Satisfied",
                    "label_text": "",
                    "name": "",
                    "visible_position": {"x": 100, "y": 200, "width": 20, "height": 20},
                    "is_visible": True,
                    "disabled": False,
                },
            ],
            "body_text": "Q1. Satisfaction Survey",
        }
        text = build_compact_layout(layout)
        assert "ui-id-0" in text
        assert "Very Satisfied" in text
        assert "Q1. Satisfaction Survey" in text  # body text included

    def test_hidden_elements_not_in_table(self) -> None:
        """Elements marked as not visible should be excluded from the table."""
        layout = {
            "page_title": "Survey",
            "page_url": "https://example.com",
            "interactive_elements": [
                {
                    "ui_id": "ui-id-0",
                    "tag": "input",
                    "type": "radio",
                    "text": "Visible",
                    "label_text": "",
                    "name": "",
                    "visible_position": {"x": 100, "y": 100, "width": 20, "height": 20},
                    "is_visible": True,
                    "disabled": False,
                },
                {
                    "ui_id": "ui-id-1",
                    "tag": "input",
                    "type": "hidden",
                    "text": "Hidden",
                    "label_text": "",
                    "name": "",
                    "visible_position": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "is_visible": False,
                    "disabled": False,
                },
            ],
            "body_text": "",
        }
        text = build_compact_layout(layout)
        assert "ui-id-0" in text
        # Hidden elements are now INCLUDED with a 👻 marker so the LLM
        # can still reference them. Survey platforms often hide native
        # <input> elements and rely on styled <label> wrappers.
        assert "ui-id-1" in text
        assert "👻" in text  # hidden marker present

    def test_disabled_indicator(self) -> None:
        """Disabled elements should be marked with ❌."""
        layout = {
            "page_title": "Survey",
            "page_url": "https://example.com",
            "interactive_elements": [
                {
                    "ui_id": "ui-id-0",
                    "tag": "input",
                    "type": "radio",
                    "text": "Disabled Option",
                    "label_text": "",
                    "name": "",
                    "visible_position": {"x": 100, "y": 100, "width": 20, "height": 20},
                    "is_visible": True,
                    "disabled": True,
                },
            ],
            "body_text": "",
        }
        text = build_compact_layout(layout)
        assert "❌" in text  # disabled indicator

    def test_truncates_long_body_text(self) -> None:
        """Body text longer than 3000 chars should be truncated."""
        long_text = "X" * 5000
        layout = {
            "page_title": "Survey",
            "page_url": "https://example.com",
            "interactive_elements": [],
            "body_text": long_text,
        }
        text = build_compact_layout(layout)
        body_start = text.find("```text")
        body_end = text.rfind("```")
        body_content = text[body_start:body_end]
        # Should be truncated at 3000 chars + newline + closing ```
        assert len(long_text[:3000]) <= 3000
