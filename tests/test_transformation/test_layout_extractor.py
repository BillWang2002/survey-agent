"""
Tests for the LayoutExtractor — question type classification,
matrix table grouping, slider detection, and orphan element detection.
"""

import pytest
from survey_agent.transformation.layout_extractor import LayoutExtractor


class TestLayoutExtractor:
    """Test the LayoutExtractor's ability to classify and group elements."""

    def setup_method(self) -> None:
        self.extractor = LayoutExtractor()

    # ------------------------------------------------------------------
    # Basic extraction
    # ------------------------------------------------------------------

    def test_empty_layout(self) -> None:
        """Empty layout should return empty questions."""
        result = self.extractor.extract({
            "page_title": "Empty",
            "page_url": "https://example.com",
            "interactive_elements": [],
            "body_text": "",
        })
        assert result["element_count"] == 0
        assert result["questions"] == []
        assert result["orphan_elements"] == []

    def test_page_info_preserved(self) -> None:
        """Page title and URL should be preserved in the output."""
        result = self.extractor.extract({
            "page_title": "My Survey",
            "page_url": "https://example.com/survey",
            "interactive_elements": [],
            "body_text": "",
        })
        assert result["page_info"]["title"] == "My Survey"
        assert result["page_info"]["url"] == "https://example.com/survey"

    # ------------------------------------------------------------------
    # Radio group detection (by name attribute)
    # ------------------------------------------------------------------

    def test_groups_radio_by_name(self) -> None:
        """Radio inputs with the same name should be grouped into one question."""
        elements = [
            _make_el("ui-id-0", "input", "radio", name="q1", label_text="Red", checked=False),
            _make_el("ui-id-1", "input", "radio", name="q1", label_text="Blue", checked=False),
            _make_el("ui-id-2", "input", "radio", name="q1", label_text="Green", checked=False),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        # All 3 radios should be in one question
        assert len(result["questions"]) == 1
        q = result["questions"][0]
        assert q["type"] == "radio"
        assert q["name"] == "q1"
        assert len(q["options"]) == 3

    def test_groups_checkbox_by_name(self) -> None:
        """Checkbox inputs with the same name should be grouped."""
        elements = [
            _make_el("ui-id-0", "input", "checkbox", name="hobbies", label_text="Sports"),
            _make_el("ui-id-1", "input", "checkbox", name="hobbies", label_text="Reading"),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert len(result["questions"]) == 1
        assert result["questions"][0]["type"] == "checkbox"

    def test_different_names_create_separate_questions(self) -> None:
        """Radios with different names should be separate questions."""
        elements = [
            _make_el("ui-id-0", "input", "radio", name="q1", label_text="A"),
            _make_el("ui-id-1", "input", "radio", name="q2", label_text="B"),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert len(result["questions"]) == 2

    # ------------------------------------------------------------------
    # Matrix table grouping
    # ------------------------------------------------------------------

    def test_groups_matrix_elements_by_row(self) -> None:
        """Elements with table_context should be grouped by row_header."""
        elements = [
            _make_el("ui-id-0", "input", "radio",
                     table_context={"row_header": "Product Quality", "col_header": "Strongly Agree", "col_index": 1, "total_cols": 5}),
            _make_el("ui-id-1", "input", "radio",
                     table_context={"row_header": "Product Quality", "col_header": "Agree", "col_index": 2, "total_cols": 5}),
            _make_el("ui-id-2", "input", "radio",
                     table_context={"row_header": "Customer Service", "col_header": "Strongly Agree", "col_index": 1, "total_cols": 5}),
            _make_el("ui-id-3", "input", "radio",
                     table_context={"row_header": "Customer Service", "col_header": "Agree", "col_index": 2, "total_cols": 5}),
        ]
        result = self.extractor.extract({
            "page_title": "Matrix Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        # Two rows
        assert len(result["questions"]) == 2
        types = {q["type"] for q in result["questions"]}
        assert types == {"matrix_scale"}

    def test_matrix_elements_include_column_headers(self) -> None:
        """Matrix questions should list their column headers."""
        elements = [
            _make_el("ui-id-0", "input", "radio",
                     table_context={"row_header": "Quality", "col_header": "Strongly Agree", "col_index": 1, "total_cols": 5}),
            _make_el("ui-id-1", "input", "radio",
                     table_context={"row_header": "Quality", "col_header": "Agree", "col_index": 2, "total_cols": 5}),
        ]
        result = self.extractor.extract({
            "page_title": "Matrix",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        q = result["questions"][0]
        assert "Strongly Agree" in q["column_headers"]
        assert "Agree" in q["column_headers"]

    # ------------------------------------------------------------------
    # Element type classification
    # ------------------------------------------------------------------

    def test_classifies_slider(self) -> None:
        """An input with type='range' should be classified as slider."""
        elements = [
            _make_el("ui-id-0", "input", "range", visible_position={"x": 100, "y": 200, "width": 300, "height": 20}),
        ]
        result = self.extractor.extract({
            "page_title": "Slider Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert result["questions"][0]["type"] == "slider"

    def test_classifies_text_input(self) -> None:
        """An input with type='text' should be classified as text_input."""
        elements = [
            _make_el("ui-id-0", "input", "text"),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert result["questions"][0]["type"] == "text_input"

    def test_classifies_textarea(self) -> None:
        """A textarea should be classified as textarea."""
        elements = [
            _make_el("ui-id-0", "textarea", ""),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert result["questions"][0]["type"] == "textarea"

    def test_classifies_select(self) -> None:
        """A select element should be classified as select."""
        elements = [
            _make_el("ui-id-0", "select", ""),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert result["questions"][0]["type"] == "select"

    # ------------------------------------------------------------------
    # Required / Disabled flags
    # ------------------------------------------------------------------

    def test_required_flag_carried_through(self) -> None:
        """The required flag should be preserved on extracted questions."""
        elements = [
            _make_el("ui-id-0", "input", "text", required=True),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert result["questions"][0]["required"] is True

    def test_disabled_flag_carried_through(self) -> None:
        """The disabled flag should be preserved."""
        elements = [
            _make_el("ui-id-0", "input", "text", disabled=True),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert result["questions"][0]["disabled"] is True

    # ------------------------------------------------------------------
    # Element map
    # ------------------------------------------------------------------

    def test_element_map_created(self) -> None:
        """element_map should provide quick ui_id → element lookup."""
        elements = [
            _make_el("ui-id-0", "input", "text"),
            _make_el("ui-id-1", "input", "radio", name="q1"),
            _make_el("ui-id-2", "input", "radio", name="q1"),
        ]
        result = self.extractor.extract({
            "page_title": "Survey",
            "page_url": "",
            "interactive_elements": elements,
            "body_text": "",
        })
        assert "ui-id-0" in result["element_map"]
        assert result["element_map"]["ui-id-0"]["tag"] == "input"


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _make_el(
    ui_id: str,
    tag: str,
    el_type: str,
    *,
    name: str = "",
    label_text: str = "",
    text: str = "",
    checked: bool = False,
    disabled: bool = False,
    required: bool = False,
    table_context: dict | None = None,
    visible_position: dict | None = None,
) -> dict:
    """Create a minimal interactive element dict for testing."""
    return {
        "ui_id": ui_id,
        "tag": tag,
        "type": el_type,
        "name": name,
        "text": text or label_text,
        "label_text": label_text,
        "checked": checked,
        "disabled": disabled,
        "required": required,
        "visible_position": visible_position or {"x": 100, "y": 100, "width": 20, "height": 20},
        "is_visible": True,
        "table_context": table_context,
    }
