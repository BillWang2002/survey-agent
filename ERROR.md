# Error Diagnosis Reference

> Documents issues discovered during SurveyAgent operation, root cause analysis, and fix plans.

---

## ERROR-001: Navigation Failure Causes Unexpected Browser Shutdown (2026-06-20)

### Symptoms

```
✅ LLM decision correct: select option → then navigate
✅ Option click succeeded (ui-id-5)
❌ No next button found.
❌ Cannot navigate: no Next/Submit button found.
❌ ERROR → Browser closed
```

### Error Chain Trace

```
LLM outputs navigate action
  → action_executor._navigate() searches for standard buttons
    → Searches for: "Next" / "Continue" / ...
    → Roy Morgan platform uses custom icon-arrow buttons, no text match
    → Returns False: "No next button found."
  → URL unchanged
  → state_machine.click_next() retries
    → Same result — no standard button found
    → Transitions directly to ERROR state
  → BrowserManager.__aexit__ → Browser closed
```

### Root Cause Analysis

#### Root Cause 1: LLM uses generic `navigate` instead of clicking specific elements

The LLM should click the icon button's `ui_id` (e.g., `ui-id-19`) instead of outputting `{"type": "navigate", "action": "next"}`. The generic `navigate` action only matches text-based buttons and cannot handle pure icon/arrow navigation elements.

**Affected files:**
- `prompts/system_prompt.md` — needs guidance: prefer `click` for specific navigation elements
- `src/survey_agent/execution/action_executor.py:_navigate()` — selectors not comprehensive enough

#### Root Cause 2: Navigation failure directly enters ERROR → no recovery

When `_navigate()` returns `False`, the flow enters `state_machine.click_next()`, which fails again and transitions directly to `ERROR`, terminating the run.

**Expected behavior:** Navigation failure should loop back to the main cycle, re-perceiving the page, and letting the LLM try alternative approaches (e.g., clicking a specific arrow button by `ui_id`).

**Affected files:**
- `src/survey_agent/core/agent.py` — main loop after `detect_and_retry`
- `src/survey_agent/core/state_machine.py:click_next()` — ERROR transition is too aggressive

#### Root Cause 3: Missing icon/arrow button selectors

`_navigate()` and `state_machine.find_next_button()` selectors only cover text-matching patterns and lack icon/arrow/SVG button recognition.

Roy Morgan platform's navigation button characteristics:
- Right-arrow icon (likely `<button>` containing SVG or `▶` / `→` symbols)
- No visible text content
- Class names may contain `next`, `arrow`, `forward`, etc.

**Affected files:**
- `src/survey_agent/execution/action_executor.py:_navigate()`
- `src/survey_agent/core/state_machine.py:NEXT_BUTTON_SELECTORS`

### Fix Plan

#### Fix 1: System Prompt guides LLM to prefer clicking specific buttons [P0]

Added to `prompts/system_prompt.md` navigate documentation:

```
- If the page has arrow icons, ">" symbols, or other non-text navigation elements,
  use the click action with its ui_id instead of the navigate action
- The navigate action should only be used when the page clearly has text buttons
  like "Next" / "Submit" / "Continue"
```

#### Fix 2: Navigation failure does not enter ERROR state [P0]

In `agent.py` main loop, after `state_machine.click_next()` fails:
- Do NOT transition to ERROR state
- Re-perceive the page
- Pass "previous navigation failed" context to the LLM
- Let the LLM attempt clicking specific buttons

#### Fix 3: Add icon/arrow button selectors [P1]

Added to `_navigate()` and `NEXT_BUTTON_SELECTORS`:

```python
# Icon/arrow button selectors
'button[class*="next"]',
'button[class*="arrow"]',
'button[class*="forward"]',
'button[aria-label*="Next" i]',
'button[aria-label*="next" i]',
'button:has(svg)',  # SVG icon buttons
'a[class*="next"]',
'[role="button"][aria-label*="next" i]',
```

### Verification

Run on the Roy Morgan survey and observe:
1. Whether the LLM outputs `click ui-id-XX` instead of `navigate`
2. Whether navigation failure triggers automatic retry instead of exit
3. Whether the retry successfully navigates to the next page

---

## Error Classification

| Level | Meaning | Example |
|-------|---------|---------|
| P0 | Blocking — prevents task completion | Unexpected browser close, consecutive LLM failures |
| P1 | Non-blocking but significant UX impact | Multiple manual interventions needed, repeated actions |
| P2 | Optimization | Extra token consumption, verbose logging |
