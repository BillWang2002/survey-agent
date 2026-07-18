"""
Tests for the HTML cleaner module.
"""

import pytest
from survey_agent.transformation.html_cleaner import HTMLCleaner


class TestHTMLCleaner:
    """Test the HTMLCleaner for token reduction and structure preservation."""

    def setup_method(self) -> None:
        self.cleaner = HTMLCleaner()

    def test_removes_script_tags(self) -> None:
        """Script tags should be completely removed."""
        html = "<html><body><script>alert('hi')</script><p>Hello</p></body></html>"
        result = self.cleaner.clean(html)
        assert "alert" not in result
        assert "Hello" in result

    def test_removes_style_tags(self) -> None:
        """Style tags should be removed."""
        html = "<html><body><style>.red{color:red}</style><p>Hello</p></body></html>"
        result = self.cleaner.clean(html)
        assert ".red" not in result
        assert "Hello" in result

    def test_removes_comments(self) -> None:
        """HTML comments should be stripped."""
        html = "<html><body><!-- this is a comment --><p>Hello</p></body></html>"
        result = self.cleaner.clean(html)
        assert "comment" not in result.lower()

    def test_strips_inline_styles(self) -> None:
        """Inline style attributes should be removed."""
        html = '<html><body><p style="color: red;">Hello</p></body></html>'
        result = self.cleaner.clean(html)
        assert "color:" not in result
        assert "Hello" in result

    def test_strips_onclick_handlers(self) -> None:
        """Event handler attributes (onclick, etc.) should be removed."""
        html = '<html><body><button onclick="doStuff()">Click</button></body></html>'
        result = self.cleaner.clean(html)
        assert "onclick" not in result.lower()
        assert "Click" in result

    def test_preserves_input_elements(self) -> None:
        """Input elements should be preserved in the output."""
        html = (
            '<html><body><form>'
            '<input type="radio" name="q1" value="yes" data-ui-id="ui-id-0">'
            '</form></body></html>'
        )
        result = self.cleaner.clean(html)
        assert "radio" in result
        assert "ui-id-0" in result

    def test_preserves_data_ui_id_attribute(self) -> None:
        """The data-ui-id attribute must be preserved for element targeting."""
        html = (
            '<html><body><form>'
            '<input type="text" name="name" data-ui-id="ui-id-5" style="width:100px">'
            '</form></body></html>'
        )
        result = self.cleaner.clean(html)
        assert "ui-id-5" in result

    def test_truncates_long_content(self) -> None:
        """Content exceeding max_text_length should be truncated."""
        cleaner = HTMLCleaner(max_text_length=100)
        html = f"<html><body><p>{'X' * 5000}</p></body></html>"
        result = cleaner.clean(html)
        assert len(result) <= 150  # 100 + truncation message

    def test_normalizes_whitespace(self) -> None:
        """Multiple blank lines should be collapsed."""
        html = "<html><body>\n\n\n\n<p>Hello</p>\n\n\n\n</body></html>"
        result = self.cleaner.clean(html)
        # Should not have 4+ consecutive newlines
        assert "\n\n\n\n" not in result

    def test_removes_svg_and_canvas(self) -> None:
        """SVG and canvas elements should be removed."""
        html = "<html><body><svg><circle></circle></svg><canvas></canvas><p>Hello</p></body></html>"
        result = self.cleaner.clean(html)
        assert "circle" not in result.lower()
        assert "canvas" not in result.lower()
        assert "Hello" in result

    def test_preserves_form_structure(self) -> None:
        """Form structure with labels and inputs should be preserved."""
        html = """
        <html><body><form>
            <label for="email">Email:</label>
            <input type="email" id="email" name="email" data-ui-id="ui-id-0" placeholder="Enter email">
        </form></body></html>
        """
        result = self.cleaner.clean(html)
        assert "Email" in result
