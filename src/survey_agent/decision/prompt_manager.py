"""
Prompt Manager — manages system prompts and user prompt templates
for the DeepSeek V4 Pro decision layer.

System prompts are loaded from the prompts/ directory with fallback
to built-in defaults. The prompt engineering here is critical —
it constrains the LLM to output valid, executable JSON actions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from survey_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Default paths for prompt files
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.md"
FEW_SHOT_FILE = PROMPTS_DIR / "few_shot_examples.json"


# ---------------------------------------------------------------------------
# Built-in default system prompt (used if the file is missing)
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """You are a professional survey automation specialist. Your task is to analyze the layout structure of web-based questionnaires and make reasonable filling decisions based on the user's requirements.

## Core Rules

1. **Strictly adhere to the JSON output format** — output nothing besides valid JSON.
2. Every action must reference an element's `ui_id`, which is the unique positioning identifier.
3. Before taking action, analyze the page structure and question types.
4. For matrix/Likert scale questions, understand the row-column mapping before answering.
5. For sliders, provide a target percentage between 0–100.
6. If the page has a "Next" or "Submit" button, click it after completing all questions on the current page.
7. If all questions are complete, set status to "FINISHED".

## JSON Output Format

You must strictly follow this JSON Schema:

```json
{
  "thought": "Analysis of the page (concise, 50–200 words)",
  "status": "CONTINUE or FINISHED",
  "actions": [
    {
      "type": "click",
      "ui_id": "ui-id-3",
      "reason": "Why this option was selected"
    },
    {
      "type": "fill",
      "ui_id": "ui-id-7",
      "value": "Text content to fill",
      "reason": "Why this value was chosen"
    },
    {
      "type": "slider",
      "ui_id": "ui-id-10",
      "percentage": 80,
      "reason": "Drag slider to 80% position"
    },
    {
      "type": "select",
      "ui_id": "ui-id-5",
      "value": "Option text or value",
      "reason": "Reason for selection"
    },
    {
      "type": "navigate",
      "action": "next or submit",
      "reason": "Navigate to next page or submit"
    }
  ]
}
```

## Action Type Reference

- **click**: Click a radio button, checkbox, or regular button
- **fill**: Type text into an input or textarea
- **slider**: Drag a slider to a specified percentage position
- **select**: Choose an option from a dropdown
- **navigate**: Click "Next" or "Submit" button

## Filling Strategy

- Single/Multiple choice: Select appropriate options based on the filling requirements
- Free text: Provide contextually appropriate answers
- Matrix questions: Understand each row as a sub-question and each column as a level/option
- If requirements are unclear, choose the most neutral or common option
- Required fields (required) must be filled
- If validation errors (red prompts) are detected, analyze the error messages and attempt correction
"""


# ---------------------------------------------------------------------------
# Default few-shot examples
# ---------------------------------------------------------------------------

DEFAULT_FEW_SHOT_EXAMPLES: list[dict[str, Any]] = [
    {
        "page_context": """
## Page: Employee Satisfaction Survey
[ui-id-0] <input type=radio> Very Satisfied
[ui-id-1] <input type=radio> Satisfied
[ui-id-2] <input type=radio> Neutral
[ui-id-3] <input type=radio> Dissatisfied
[ui-id-4] <button> Next
""",
        "requirements": "Select 'Satisfied' for all",
        "expected_output": {
            "thought": "A single-choice question with 4 radio button options. Per the requirements, select 'Satisfied' (ui-id-1).",
            "status": "CONTINUE",
            "actions": [
                {"type": "click", "ui_id": "ui-id-1", "reason": "Select 'Satisfied' option"},
                {"type": "navigate", "action": "next", "reason": "Question complete, go to next page"},
            ],
        },
    },
    {
        "page_context": """
## Page: Personal Info
[ui-id-0] <input type=text placeholder=Please enter your name>
[ui-id-1] <input type=email placeholder=Please enter your email>
[ui-id-2] <button> Submit
""",
        "requirements": "Name: 'John Smith', Email: 'test@example.com'",
        "expected_output": {
            "thought": "Two text input fields. Fill in name and email per the requirements.",
            "status": "FINISHED",
            "actions": [
                {"type": "fill", "ui_id": "ui-id-0", "value": "John Smith", "reason": "Enter name"},
                {"type": "fill", "ui_id": "ui-id-1", "value": "test@example.com", "reason": "Enter email"},
                {"type": "navigate", "action": "submit", "reason": "Submit survey"},
            ],
        },
    },
]


class PromptManager:
    """
    Manages LLM prompts: system prompt, user prompt template, and few-shot examples.

    Loads prompts from external files in the prompts/ directory, falling back
    to built-in defaults if files are not found.

    Usage:
        pm = PromptManager()
        system = pm.get_system_prompt()
        user_prompt = pm.build_user_prompt(compact_layout, requirements, page_num)
    """

    def __init__(
        self,
        prompts_dir: Path | None = None,
    ) -> None:
        self._prompts_dir = prompts_dir or PROMPTS_DIR
        self._few_shot_examples = self._load_few_shot_examples()

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def get_system_prompt(self) -> str:
        """
        Get the system prompt.

        Loads from prompts/system_prompt.md if available,
        otherwise uses the built-in default.
        """
        if SYSTEM_PROMPT_FILE.exists():
            try:
                content = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
                if content:
                    logger.info(f"Loaded system prompt from {SYSTEM_PROMPT_FILE}")
                    return content
            except Exception as e:
                logger.warning(f"Failed to load system prompt file: {e}")

        logger.info("Using built-in default system prompt.")
        return DEFAULT_SYSTEM_PROMPT

    # ------------------------------------------------------------------
    # User prompt builder
    # ------------------------------------------------------------------

    def build_user_prompt(
        self,
        compact_layout: str,
        requirements: str,
        page_number: int = 1,
        cleaned_html: str = "",
        previous_errors: list[dict] | None = None,
    ) -> str:
        """
        Build a complete user prompt for the LLM.

        Args:
            compact_layout: Compact text representation of the page (from injector).
            requirements: User's filling instructions.
            page_number: Current page number (for context).
            cleaned_html: Optional cleaned HTML for deeper context.
            previous_errors: Optional list of validation errors from the previous attempt.

        Returns:
            Complete user prompt string ready for the LLM.
        """
        parts = []

        # --- Page context ---
        parts.append(f"# Current Page (Page {page_number})")
        parts.append("")
        parts.append("## Page Layout & Interactive Elements")
        parts.append(compact_layout)
        parts.append("")

        # --- Filling requirements ---
        parts.append("## Filling Requirements")
        parts.append(requirements if requirements else "(No special requirements — please answer reasonably based on common sense)")
        parts.append("")

        # --- Previous errors (feedback loop) ---
        if previous_errors:
            parts.append("## ⚠️ Previous Errors")
            parts.append("The following validation errors appeared after the last submission. Please correct them in your next decision:")
            for err in previous_errors:
                parts.append(f"- {err.get('text', 'Unknown error')}")
                if err.get("field_name"):
                    parts.append(f"  Related field: {err['field_name']}")
            parts.append("")

        # --- Optional HTML context (truncated) ---
        if cleaned_html:
            parts.append("## Page HTML Structure (simplified)")
            parts.append("```html")
            parts.append(cleaned_html[:4000])
            parts.append("```")
            parts.append("")

        # --- Few-shot examples ---
        if self._few_shot_examples:
            parts.append("## Reference Examples")
            for i, example in enumerate(self._few_shot_examples):
                parts.append(f"### Example {i + 1}")
                parts.append(f"Page:\n{example['page_context'][:500]}")
                parts.append(f"Requirements: {example['requirements']}")
                parts.append(
                    f"Expected output: {json.dumps(example['expected_output'], ensure_ascii=False, indent=2)}"
                )
                parts.append("")

        # --- Final instruction ---
        parts.append("## Please Output Your Decision JSON")
        parts.append("Output the next action(s) for the current page in strict JSON format.")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Few-shot examples
    # ------------------------------------------------------------------

    def _load_few_shot_examples(self) -> list[dict[str, Any]]:
        """Load few-shot examples from file, or use built-in defaults."""
        if FEW_SHOT_FILE.exists():
            try:
                data = json.loads(FEW_SHOT_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    logger.info(
                        f"Loaded {len(data)} few-shot examples from {FEW_SHOT_FILE}"
                    )
                    return data
            except Exception as e:
                logger.warning(f"Failed to load few-shot examples: {e}")

        logger.info("Using built-in default few-shot examples.")
        return DEFAULT_FEW_SHOT_EXAMPLES

    def add_few_shot_example(
        self, page_context: str, requirements: str, expected_output: dict
    ) -> None:
        """
        Add a new few-shot example at runtime and persist to file.

        Args:
            page_context: The compact page layout text.
            requirements: The filling requirements that were used.
            expected_output: The correct JSON output for this case.
        """
        example = {
            "page_context": page_context,
            "requirements": requirements,
            "expected_output": expected_output,
        }
        self._few_shot_examples.append(example)

        # Persist to file
        try:
            FEW_SHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            FEW_SHOT_FILE.write_text(
                json.dumps(self._few_shot_examples, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"Saved {len(self._few_shot_examples)} few-shot examples.")
        except Exception as e:
            logger.warning(f"Failed to persist few-shot examples: {e}")
