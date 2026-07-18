"""
Layout Extractor — converts the raw page layout (from the JS injector) into
structured, typed data suitable for the decision layer.

Handles special question types:
- Matrix/scale tables (rows × columns)
- Sliders (coordinate-based)
- Drag-and-drop sort questions
- Conditional logic (parent → child question dependencies)
"""

from __future__ import annotations

from typing import Any


class LayoutExtractor:
    """
    Processes raw layout JSON and identifies question types, grouping
    related elements together for better LLM comprehension.

    Usage:
        extractor = LayoutExtractor()
        structured = extractor.extract(raw_layout)
    """

    # Question type constants
    TYPE_RADIO = "radio"
    TYPE_CHECKBOX = "checkbox"
    TYPE_SELECT = "select"
    TYPE_TEXT_INPUT = "text_input"
    TYPE_TEXTAREA = "textarea"
    TYPE_MATRIX_SCALE = "matrix_scale"
    TYPE_SLIDER = "slider"
    TYPE_RANKING = "ranking"
    TYPE_UNKNOWN = "unknown"

    def extract(self, raw_layout: dict[str, Any]) -> dict[str, Any]:
        """
        Extract structured question data from raw layout.

        Args:
            raw_layout: Parsed layout dict from the JS injection script.

        Returns:
            A dict with:
              - page_info: {title, url}
              - questions: list of typed question descriptors
              - orphan_elements: elements not grouped into any question
              - element_map: {ui_id → element} for quick lookup
        """
        elements = raw_layout.get("interactive_elements", [])
        page_info = {
            "title": raw_layout.get("page_title", ""),
            "url": raw_layout.get("page_url", ""),
        }

        # Build element map for fast lookup
        element_map: dict[str, dict] = {}
        for el in elements:
            uid = el.get("ui_id", "")
            if uid:
                element_map[uid] = el

        # Group elements by question
        questions = self._group_into_questions(elements)
        orphan_elements = self._find_orphans(elements, questions)

        return {
            "page_info": page_info,
            "questions": questions,
            "orphan_elements": orphan_elements,
            "element_map": element_map,
            "element_count": len(elements),
        }

    def _group_into_questions(
        self, elements: list[dict]
    ) -> list[dict[str, Any]]:
        """
        Group elements into logical questions.

        Strategy:
        1. Identify matrix tables — group by table_context
        2. Group radio/checkbox sets by name attribute
        3. Identify slider questions
        4. Remainder as individual questions
        """
        questions: list[dict] = []
        assigned_uids: set[str] = set()

        # --- 1. Matrix tables ---
        matrix_elements = [e for e in elements if e.get("table_context")]
        if matrix_elements:
            matrix_questions = self._group_matrix_tables(matrix_elements)
            for q in matrix_questions:
                for opt in q.get("options", []):
                    uid = opt.get("ui_id") or opt.get("ui_ids", [])
                    if isinstance(uid, str):
                        assigned_uids.add(uid)
                    else:
                        assigned_uids.update(uid)
            questions.extend(matrix_questions)

        # --- 2. Radio/Checkbox groups (by name attribute) ---
        radio_checkbox_elements = [
            e for e in elements
            if e.get("tag") == "input"
            and e.get("type") in ("radio", "checkbox")
            and e.get("ui_id") not in assigned_uids
        ]

        # Group by name
        name_groups: dict[str, list[dict]] = {}
        for el in radio_checkbox_elements:
            name = el.get("name", "") or f"__anon__{el.get('ui_id')}"
            if name not in name_groups:
                name_groups[name] = []
            name_groups[name].append(el)

        for name, group in name_groups.items():
            if len(group) == 1 and name.startswith("__anon__"):
                # Single anonymous element — treat as individual
                continue

            q_type = self.TYPE_CHECKBOX if group[0].get("type") == "checkbox" else self.TYPE_RADIO
            questions.append({
                "type": q_type,
                "name": name,
                "label": group[0].get("label_text", ""),
                "options": [
                    {
                        "ui_id": el["ui_id"],
                        "text": el.get("label_text") or el.get("text", ""),
                        "checked": el.get("checked", False),
                    }
                    for el in group
                ],
                "required": any(el.get("required", False) for el in group),
            })
            for el in group:
                assigned_uids.add(el["ui_id"])

        # --- 3. Sliders ---
        slider_elements = [
            e for e in elements
            if (e.get("type") == "range" or e.get("type") == "slider"
                or e.get("role") == "slider")
            and e.get("ui_id") not in assigned_uids
        ]
        for el in slider_elements:
            questions.append({
                "type": self.TYPE_SLIDER,
                "ui_id": el["ui_id"],
                "label": el.get("label_text", ""),
                "position": el.get("visible_position", {}),
                "current_value": el.get("text", ""),
            })
            assigned_uids.add(el["ui_id"])

        # --- 4. Remaining individual elements ---
        for el in elements:
            uid = el.get("ui_id", "")
            if not uid or uid in assigned_uids:
                continue

            tag = el.get("tag", "")
            el_type = el.get("type", "")
            questions.append(self._classify_single_element(el))
            assigned_uids.add(uid)

        return questions

    def _group_matrix_tables(
        self, elements: list[dict]
    ) -> list[dict[str, Any]]:
        """
        Group elements that belong to matrix/scale tables.

        A matrix question is: one row header × multiple column options.
        Rows share the same column headers (col_header in table_context).
        """
        # Group by unique row context
        rows: dict[str, list[dict]] = {}
        for el in elements:
            ctx = el.get("table_context", {})
            row_key = ctx.get("row_header", "") or f"__row_{ctx.get('col_index', 0)}"
            if row_key not in rows:
                rows[row_key] = []
            rows[row_key].append(el)

        matrix_questions = []
        for row_header, row_elements in rows.items():
            if row_header.startswith("__row_"):
                row_header = ""

            col_headers = list(set(
                e.get("table_context", {}).get("col_header", "")
                for e in row_elements
            ))

            matrix_questions.append({
                "type": self.TYPE_MATRIX_SCALE,
                "row_label": row_header,
                "column_headers": col_headers,
                "options": [
                    {
                        "ui_id": el["ui_id"],
                        "col_header": el.get("table_context", {}).get("col_header", ""),
                        "checked": el.get("checked", False),
                    }
                    for el in row_elements
                ],
                "required": any(el.get("required", False) for el in row_elements),
            })

        return matrix_questions

    def _classify_single_element(self, el: dict) -> dict[str, Any]:
        """Classify a single interactive element into a question type."""
        tag = el.get("tag", "")
        el_type = el.get("type", "")
        role = el.get("role", "")

        if tag == "select" or el_type == "select" or role == "listbox":
            q_type = self.TYPE_SELECT
        elif tag == "textarea":
            q_type = self.TYPE_TEXTAREA
        elif tag == "input" and el_type in ("text", "email", "number", "tel", "url", "date", ""):
            q_type = self.TYPE_TEXT_INPUT
        elif tag == "input" and el_type == "radio":
            q_type = self.TYPE_RADIO
        elif tag == "input" and el_type == "checkbox":
            q_type = self.TYPE_CHECKBOX
        elif role == "slider" or el_type == "range":
            q_type = self.TYPE_SLIDER
        else:
            q_type = self.TYPE_UNKNOWN

        return {
            "type": q_type,
            "ui_id": el.get("ui_id", ""),
            "label": el.get("label_text", ""),
            "tag": tag,
            "input_type": el_type,
            "placeholder": el.get("text", ""),
            "required": el.get("required", False),
            "disabled": el.get("disabled", False),
            "options": [],  # Single element has no sub-options
        }

    def _find_orphans(
        self, elements: list[dict], questions: list[dict]
    ) -> list[dict]:
        """Find elements not assigned to any question group."""
        assigned = set()
        for q in questions:
            if "ui_id" in q:
                assigned.add(q["ui_id"])
            for opt in q.get("options", []):
                if "ui_id" in opt:
                    assigned.add(opt["ui_id"])
                assigned.update(opt.get("ui_ids", []))

        return [e for e in elements if e.get("ui_id", "") not in assigned]
