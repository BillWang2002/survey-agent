"""
HTML Cleaner — strips irrelevant content from raw HTML to reduce token costs.

Uses BeautifulSoup4 to remove:
- <script>, <style>, <noscript> tags
- Comments
- Inline CSS styles (class/ids kept for structure)
- Hidden elements (display:none, visibility:hidden)
- SVG, canvas, video, audio, iframe elements
- Invisible whitespace-only text nodes

Goal: reduce HTML size by ~80% while preserving all interactive element
structure needed for the LLM to make decisions.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Tags to completely remove
REMOVE_TAGS = {
    "script", "style", "noscript", "svg", "canvas",
    "video", "audio", "iframe", "object", "embed",
    "meta", "link", "path", "img", "picture", "source",
}

# Attributes to strip (CSS styles, JS handlers, etc.)
STRIP_ATTRIBUTES = {
    "style", "onclick", "onchange", "onfocus", "onblur",
    "onkeydown", "onkeyup", "onkeypress", "onmouseover",
    "onmouseout", "onmousedown", "onmouseup", "onload",
    "onerror", "onsubmit", "data-track", "data-analytics",
    "data-gtm", "aria-hidden",
}

# Attributes to KEEP (whitelist for interactive elements)
KEEP_ATTRIBUTES = {
    "data-ui-id", "type", "name", "value", "placeholder",
    "id", "class", "href", "role", "aria-label",
    "aria-labelledby", "aria-required", "aria-checked",
    "checked", "disabled", "required", "selected",
    "tabindex", "title", "for", "action", "method",
    "data-survey-option", "data-question-id",
}


class HTMLCleaner:
    """
    Strips an HTML document down to its essential interactive structure.

    Usage:
        cleaner = HTMLCleaner()
        cleaned = cleaner.clean(raw_html)  # returns compact text representation
    """

    def __init__(
        self,
        remove_tags: set[str] | None = None,
        strip_attributes: set[str] | None = None,
        keep_attributes: set[str] | None = None,
        max_text_length: int = 8000,
    ) -> None:
        self._remove_tags = remove_tags or REMOVE_TAGS
        self._strip_attributes = strip_attributes or STRIP_ATTRIBUTES
        self._keep_attributes = keep_attributes or KEEP_ATTRIBUTES
        self._max_text_length = max_text_length

    def clean(self, html_content: str) -> str:
        """
        Clean raw HTML and return a compact, structured text representation.

        Args:
            html_content: Raw HTML string from page.content().

        Returns:
            Cleaned, compact text suitable for LLM context window.
        """
        soup = BeautifulSoup(html_content, "html.parser")

        # Step 1: Remove unwanted tags entirely
        self._remove_unwanted_tags(soup)

        # Step 2: Remove comments
        self._remove_comments(soup)

        # Step 3: Clean attributes on remaining tags
        self._clean_attributes(soup)

        # Step 4: Remove hidden elements
        self._remove_hidden_elements(soup)

        # Step 5: Extract structured text
        text = self._extract_structured_text(soup)

        # Step 6: Collapse whitespace and trim
        text = self._normalize_whitespace(text)

        if len(text) > self._max_text_length:
            text = text[: self._max_text_length] + "\n... [truncated]"

        return text

    def clean_to_soup(self, html_content: str) -> BeautifulSoup:
        """Same as clean() but returns the BeautifulSoup object for further processing."""
        soup = BeautifulSoup(html_content, "html.parser")
        self._remove_unwanted_tags(soup)
        self._remove_comments(soup)
        self._clean_attributes(soup)
        self._remove_hidden_elements(soup)
        return soup

    # --- Internal methods ---

    def _remove_unwanted_tags(self, soup: BeautifulSoup) -> None:
        """Remove all tags in the REMOVE_TAGS set."""
        for tag_name in self._remove_tags:
            for tag in soup.find_all(tag_name):
                tag.decompose()

    def _remove_comments(self, soup: BeautifulSoup) -> None:
        """Strip all HTML comments."""
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

    def _clean_attributes(self, soup: BeautifulSoup) -> None:
        """Keep only whitelisted attributes; remove everything else."""
        for tag in soup.find_all(True):
            if tag.attrs is None:
                continue
            attrs_to_remove = []
            for attr_name in tag.attrs:
                if attr_name in self._strip_attributes:
                    attrs_to_remove.append(attr_name)
                elif attr_name in self._keep_attributes:
                    continue
                elif attr_name.startswith("data-") and attr_name != "data-ui-id":
                    # Keep data-ui-id but strip other data- attrs
                    attrs_to_remove.append(attr_name)
            for attr in attrs_to_remove:
                del tag[attr]

    def _remove_hidden_elements(self, soup: BeautifulSoup) -> None:
        """
        Remove elements that are visually hidden.

        Checks inline style and common hidden classes.
        """
        hidden_patterns = [
            "display:none",
            "display: none",
            "visibility:hidden",
            "visibility: hidden",
            "opacity: 0",
            "opacity:0",
        ]

        for tag in soup.find_all(True):
            # Guard against tags with None attrs (can occur with malformed HTML)
            if tag.attrs is None:
                continue

            style = tag.get("style", "")
            style_lower = style.lower().replace(" ", "")

            is_hidden = any(
                p.replace(" ", "") in style_lower for p in hidden_patterns
            )

            if is_hidden:
                tag.decompose()
                continue

            # Also check for common hidden classes
            classes = tag.get("class", [])
            if isinstance(classes, str):
                classes = [classes]
            hidden_classes = {"hidden", "hide", "d-none", "sr-only", "visually-hidden"}
            if any(cls in hidden_classes for cls in classes):
                tag.decompose()

    def _extract_structured_text(self, soup: BeautifulSoup) -> str:
        """
        Extract a structured text representation from the cleaned soup.

        Focuses on form elements, labels, and question containers.
        """
        lines = []

        # Extract title
        title = soup.find("title")
        if title and title.string:
            lines.append(f"TITLE: {title.string.strip()}")
            lines.append("")

        # Extract form structure
        forms = soup.find_all("form")
        for form_idx, form in enumerate(forms):
            if len(forms) > 1:
                lines.append(f"--- Form {form_idx + 1} ---")

            # Find question containers
            question_containers = form.find_all(
                class_=re.compile(
                    r"question|field|form-group|survey-item|q-",
                    re.IGNORECASE,
                )
            )

            if not question_containers:
                # Fall back to all labels + inputs
                self._extract_flat_form(form, lines)
            else:
                for container in question_containers:
                    self._extract_question_container(container, lines)

        # If no forms found, extract from body
        if not forms:
            self._extract_body_structure(soup, lines)
        else:
            # Even with forms, capture body-level headings/text outside the form
            body = soup.find("body")
            if body:
                for el in body.find_all(["h1", "h2", "h3", "p"], recursive=False):
                    text = el.get_text(strip=True)
                    if text:
                        lines.insert(0, f"{text[:200]}")

        return "\n".join(lines)

    def _extract_flat_form(self, form: Tag, lines: list[str]) -> None:
        """Extract form elements in flat list style, including label context."""
        for el in form.find_all(["input", "select", "textarea", "button", "label"]):
            tag = el.name

            # For <label> tags, capture their text as context
            if tag == "label":
                label_text = el.get_text(strip=True)
                if label_text:
                    lines.append(f"  Label: {label_text[:150]}")
                continue

            el_type = el.get("type", "")
            ui_id = el.get("data-ui-id", "")
            name = el.get("name", "")
            value = el.get("value", "")
            placeholder = el.get("placeholder", "")
            text = el.get_text(strip=True) or placeholder or value or name

            # Try to find associated label
            label_context = ""
            el_id = el.get("id", "")
            if el_id:
                associated_label = form.find("label", attrs={"for": el_id})
                if associated_label:
                    label_context = f" ({associated_label.get_text(strip=True)})"
            # Also check parent label
            parent_label = el.find_parent("label")
            if parent_label and not label_context:
                label_context = f" ({parent_label.get_text(strip=True)[:100]})"

            uid_prefix = f"[{ui_id}]" if ui_id else "[?]"
            lines.append(
                f"{uid_prefix} <{tag}{' type=' + el_type if el_type else ''}> "
                f"{text[:100]}{label_context}"
            )

    def _extract_question_container(
        self, container: Tag, lines: list[str]
    ) -> None:
        """Extract a single question container with its options."""
        # Find question text
        label = container.find("label") or container.find(
            class_=re.compile(r"question-text|q-title", re.I)
        )
        question_text = label.get_text(strip=True) if label else ""

        lines.append(f"\nQ: {question_text[:150]}")

        # Find all interactive elements in this container
        for el in container.find_all(["input", "select", "textarea", "button"]):
            ui_id = el.get("data-ui-id", "")
            el_type = el.get("type", "")
            option_text = el.get("value", "") or el.get_text(strip=True) or ""

            # Try to get associated label text
            el_id = el.get("id", "")
            if el_id:
                associated_label = container.find("label", attrs={"for": el_id})
                if associated_label:
                    option_text = associated_label.get_text(strip=True)

            lines.append(
                f"  [{ui_id}] {el_type or el.name}: {option_text[:100]}"
            )

    def _extract_body_structure(self, soup: BeautifulSoup, lines: list[str]) -> None:
        """Extract structure from the full body (no form tag)."""
        body = soup.find("body")
        if not body:
            return

        # Capture text from structural elements (paragraphs, headings, labels)
        for el in body.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "label", "span"]):
            text = el.get_text(strip=True)
            if text and len(text) > 1:
                # Skip if it's inside a form (forms handled separately)
                if not el.find_parent("form"):
                    tag = el.name
                    prefix = {"h1": "# ", "h2": "## ", "h3": "### "}.get(tag, "")
                    lines.append(f"{prefix}{text[:200]}")

        # Capture interactive elements (with or without data-ui-id)
        for el in body.find_all(
            ["input", "select", "textarea", "button", "a"]
        ):
            ui_id = el.get("data-ui-id", "")
            tag = el.name
            el_type = el.get("type", "")
            text = (
                el.get("value", "")
                or el.get("placeholder", "")
                or el.get_text(strip=True)
                or el.get("aria-label", "")
            )
            # Try to find associated label
            label_context = ""
            el_id = el.get("id", "")
            if el_id:
                associated_label = body.find("label", attrs={"for": el_id})
                if associated_label:
                    label_context = f" ({associated_label.get_text(strip=True)})"
            parent_label = el.find_parent("label")
            if parent_label and not label_context:
                parent_label_text = parent_label.get_text(strip=True)
                # Remove the element's own text from the label to avoid duplication
                if text and parent_label_text.endswith(text):
                    parent_label_text = parent_label_text[:-len(text)].strip()
                if parent_label_text:
                    label_context = f" ({parent_label_text[:100]})"

            uid_prefix = f"[{ui_id}]" if ui_id else "[?]"
            lines.append(
                f"{uid_prefix} <{tag}{' type=' + el_type if el_type else ''}> "
                f"{text[:100]}{label_context}"
            )

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Collapse multiple blank lines and whitespace."""
        text = re.sub(r"[ \t]+", " ", text)  # Collapse horizontal whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)  # Max 2 consecutive newlines
        return text.strip()
