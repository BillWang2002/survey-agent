# SurveyAgent — Complete Runtime Flow

> Based on the first live test run on 2026-06-19

---

## 1. Startup Flow (CLI → Main Loop)

```
User Command
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ $ python -m survey_agent.main                               │
│     --url "http://localhost:8765/simple_radio.html"         │
│     --requirements "Q1 select 'Satisfied', Q2 select 'Blue'"│
│     --visible                                               │
└─────────────────────────────────────────────────────────────┘
  │
  ▼
main.py:main()
  │
  ├─ 1. argparse parse (url, requirements, --visible, ...)
  ├─ 2. asyncio.run(run_survey(...))
  │
  ▼
main.py:run_survey()
  │
  ├─ 3. Load Config (env vars + .env file)
  ├─ 4. setup_logging() initialize logging system
  ├─ 5. SurveyAgent(config) construct instance
  └─ 6. agent.run(url, requirements) → enter main loop
```

### Configuration Loading Chain

```
config.py: Config.from_env()
  ├─ LLMConfig    ← DEEPSEEK_API_KEY, DEEPSEEK_MODEL, ...
  ├─ BrowserConfig ← BROWSER_HEADLESS, BROWSER_VIEWPORT_WIDTH, ...
  └─ AgentConfig  ← MAX_PAGES, FEEDBACK_MAX_RETRIES, LOG_DIR, ...
```

---

## 2. Agent Construction (Initialize All Subsystems)

```
SurveyAgent.__init__(config)
  │
  ├─ BrowserManager(config.browser)       ← Playwright lifecycle
  ├─ HTMLCleaner()                        ← BeautifulSoup cleaning
  ├─ LayoutExtractor()                    ← Question type grouping/classification
  ├─ LLMClient(config.llm)                ← DeepSeek API (OpenAI SDK)
  ├─ PromptManager()                      ← System Prompt + Few-shot
  ├─ ResponseParser()                     ← JSON validation
  ├─ HumanInterface(webhook_url)          ← CAPTCHA notifications
  ├─ ScreenshotManager(enabled, log_dir)  ← Screenshot management
  └─ SurveyStateMachine(max_pages)        ← Multi-page state machine
```

---

## 3. Main Loop — Detailed Walkthrough (agent.run())

```
agent.run(url, requirements)
  │
  ├─ [Startup Phase]
  │   ├─ BrowserManager.start()           ← Launch Chromium
  │   │   ├─ async_playwright().start()
  │   │   ├─ chromium.launch(headless, slow_mo, anti_detect_args)
  │   │   ├─ browser.new_context(viewport, locale, storage_state)
  │   │   └─ Inject stealth script (hide webdriver markers)
  │   │
  │   ├─ browser_mgr.new_page()           ← Create new tab
  │   ├─ ActionExecutor(page, delay)      ← Bind to page
  │   ├─ FeedbackLoop(page, max_retries)  ← Bind to page
  │   ├─ page.goto(url)                   ← Navigate to survey
  │   └─ state_machine.mark_page_loaded() ← LOADING → READY
  │
  └─ [Main Loop] while not state_machine.is_completed:
       │
       ├─────────────────────────────────────────────────────┐
       │ Each loop iteration = process one survey page         │
       └─────────────────────────────────────────────────────┘
       │
       ├─ Step 1: _perceive_page(page)          [Perception + Transformation]
       │   ├─ page.evaluate(INJECT_AND_EXTRACT_SCRIPT)
       │   │   └─ JS injects data-ui-id + extracts layout JSON
       │   ├─ parse_layout_json(raw_json)
       │   ├─ build_compact_layout(layout)      ← Compact Markdown table
       │   ├─ page.content() → HTMLCleaner.clean()
       │   └─ return {raw_layout, compact_text, cleaned_html, element_count}
       │
       ├─ Step 2: _screenshot_manager.capture() [Screenshot for audit]
       │
       ├─ Step 3: _decide(layout_data, requirements) [Decision Layer]
       │   ├─ prompt_manager.get_system_prompt()
       │   ├─ prompt_manager.build_user_prompt(compact_layout, reqs, page_num)
       │   ├─ llm_client.chat_json(system_prompt, user_prompt)
       │   │   └─ POST https://api.deepseek.com/v1/chat/completions
       │   │       response_format={"type": "json_object"}
       │   ├─ response_parser.parse(raw_response)
       │   └─ log_decision(decision, page_number)  ← Write to logs/decisions/
       │
       ├─ Step 4: _execute_actions(actions)   [Execution Layer]
       │   └─ for action in actions:
       │       └─ action_executor.execute(action)
       │           ├─ click   → page.click('[data-ui-id="ui-id-X"]')
       │           ├─ fill    → page.fill('[data-ui-id="ui-id-X"]', value)
       │           ├─ slider  → page.mouse.move() + drag_to()
       │           ├─ select  → page.select_option()
       │           └─ navigate → find & click Next/Submit buttons
       │
       ├─ Step 5: FeedbackLoop.detect_and_retry() [Feedback Loop]
       │   ├─ detect_errors()
       │   │   └─ page.evaluate(DETECT_ERRORS_SCRIPT)
       │   │       └─ Scan .error, [role="alert"], :invalid, etc.
       │   ├─ if errors → build error_requirements
       │   ├─ LLM re-decides (with error context)
       │   ├─ Execute corrective actions
       │   └─ Re-detect → retry up to 3 times
       │
       └─ Step 6: state_machine.click_next(page) [Page Navigation]
           ├─ find_next_button() → search for Next/Submit buttons
           ├─ page.click(button)
           ├─ wait_for_load_state("networkidle")
           ├─ if URL changed → mark_page_loaded(new_url) → next iteration
           ├─ if URL unchanged → check_completion()
           │   └─ Scan for "Submitted", "Thank you", "Survey complete", etc.
           └─ if completed → COMPLETED → exit loop
```

---

## 4. Single-Iteration Timeline (Measured)

Using `simple_radio.html` (2 single-choice questions) as an example:

```
Time (relative)  Event                                  Duration
─────────────────────────────────────────────────────────
T+0.0s           Main loop start                       —
T+0.0s           JS injection + layout extraction       ~0.3s
T+0.0s           Screenshot capture                     ~0.1s
T+0.3s           Prompt construction                    ~0.1s
T+0.4s           Send LLM request (2758 prompt tokens)  —
T+2.8s           LLM response (217 completion tokens)   ~2.4s
T+2.8s           JSON parse + validate                  ~0.01s
T+2.8s           Action 1: click ui-id-1                ~0.6s
T+3.4s           Action 2: click ui-id-6                ~0.6s
T+4.0s           Action 3: navigate (Next button)        ~0.8s
T+4.8s           Wait for networkidle                   ~0.5s
T+5.3s           Error detection (no errors)             ~0.2s
T+5.5s           URL unchanged → check completion        ~0.3s
T+5.8s           → Next loop iteration
─────────────────────────────────────────────────────────
Per-iteration total: ~5.8s (LLM call 2.4s, browser ops ~2.5s)
```

---

## 5. Data Flow Between Layers

### Perception → Transformation

```
Raw HTML Page
  │
  │ [JS Injection: INJECT_AND_EXTRACT_SCRIPT]
  ▼
{
  "page_title": "Single Choice Test",
  "page_url": "http://localhost:8765/simple_radio.html",
  "interactive_elements": [
    {
      "ui_id": "ui-id-0",
      "tag": "input",
      "type": "radio",
      "name": "q1",
      "text": "Very Satisfied",
      "label_text": "Very Satisfied",
      "visible_position": {"x": 118, "y": 146, "width": 14, "height": 14},
      "is_visible": true,
      "checked": false,
      "disabled": false,
      "required": false,
      "table_context": null
    },
    // ... 9 elements total (8 radios + 1 button)
  ],
  "body_text": "Employee Satisfaction Survey\nQ1. How satisfied are you with your work environment?\n..."
}
  │
  │ [build_compact_layout()]
  ▼
## Page: Single Choice Test
URL: http://localhost:8765/simple_radio.html

### Interactive Elements (9 total)

| ui-id | Tag | Type | Text/Label | Position | Disabled |
|-------|-----|------|------------|----------|----------|
| `ui-id-0` | input | radio | Very Satisfied | (118, 146) |  |
| `ui-id-1` | input | radio | Satisfied | (118, 173) |  |
| `ui-id-2` | input | radio | Neutral | (118, 200) |  |
| ...
| `ui-id-8` | button | button | Next Page | (118, 360) |  |

### Page Text Content
Employee Satisfaction Survey
Q1. How satisfied are you with your current work environment?
  Very Satisfied  Satisfied  Neutral  Dissatisfied  Very Dissatisfied
Q2. What is your favorite color?
  Red  Blue  Green
```

### Decision Layer: Input → Output

```
[System Prompt] (~2500 tokens)
  You are a professional survey automation specialist...
  JSON format requirements...
  Action type reference...
  Filling strategy...

[User Prompt] (~260 tokens)
  # Current Page (Page 1)
  ## Page Layout & Interactive Elements
  [Compact table above]
  ## Filling Requirements
  Q1 select 'Satisfied', Q2 select 'Blue'
  ## Reference Examples
  [3 few-shot example groups]

        ↓ DeepSeek V4 Pro ↓

{
  "thought": "Page has two single-choice questions: Q1 'Satisfied' maps to ui-id-1, Q2 'Blue' maps to ui-id-6. After selecting both, click the Next button (ui-id-8).",
  "status": "CONTINUE",
  "actions": [
    {"type": "click", "ui_id": "ui-id-1", "reason": "Select Q1 'Satisfied' option"},
    {"type": "click", "ui_id": "ui-id-6", "reason": "Select Q2 'Blue' option"},
    {"type": "navigate", "action": "next", "reason": "All questions complete, go to next page"}
  ]
}
```

### Execution Layer: Action Mapping

```
actions[0]: {"type": "click", "ui_id": "ui-id-1"}
  → page.locator('[data-ui-id="ui-id-1"]').scroll_into_view_if_needed()
  → page.click('[data-ui-id="ui-id-1"]', delay=50)
  → ✅ Clicked "Satisfied" radio

actions[1]: {"type": "click", "ui_id": "ui-id-6"}
  → page.locator('[data-ui-id="ui-id-6"]').scroll_into_view_if_needed()
  → page.click('[data-ui-id="ui-id-6"]', delay=50)
  → ✅ Clicked "Blue" radio

actions[2]: {"type": "navigate", "action": "next"}
  → Iterate over selector list:
     'button:has-text("Next")' ← Hit!
  → page.click('button:has-text("Next")')
  → ✅ Navigated to next page
```

---

## 6. State Machine Transitions

```
                    ┌─────────┐
                    │ LOADING │  ← Page loading
                    └────┬────┘
                         │ page.goto() complete
                         ▼
                    ┌─────────┐
         ┌──────────│  READY  │──────────┐
         │          └────┬────┘          │
         │               │               │
         │    ┌──────────┴──────────┐    │
         │    │  Step 0: CAPTCHA?   │    │
         │    └────┬──────────┬─────┘    │
         │         │          │          │
         │     [CAPTCHA]   [No CAPTCHA]  │
         │         │          │          │
         │         ▼          ▼          │
         │  ┌────────────┐ ┌─────────┐   │
         │  │AWAITING    │ │ FILLING │   │
         │  │_HUMAN      │ │(implicit)│   │
         │  │(manual)    │ └────┬────┘   │
         │  └─────┬──────┘      │        │
         │        │             ▼        │
         │        │        ┌──────────┐  │
         │        │        │VALIDATING│  │
         │        │        └────┬─────┘  │
         │        │             │        │
         │        │      ┌──────┴──────┐ │
         │        │      │             │ │
         │        │   [Errors]    [No errors]│
         │        │      │             │ │
         │        │      ▼             ▼ │
         │        │ ┌──────────┐ ┌───────────┐
         │        │ │Feedback  │ │ NAVIGATING│
         │        │ │Retry ≤3  │ └─────┬─────┘
         │        │ └──────────┘       │
         │        │              ┌────┴────┐
         │        │           [URL changed] [Unchanged]
         │        │              │        │
         │        │              ▼        ▼
         │        │         ┌────────┐ ┌──────────────┐
         │        │         │ READY  │ │ check_       │
         │        │         │(next)  │ │ completion() │
         │        │         └────────┘ └──────┬───────┘
         │        │                           │
         │        └─── Back to READY ─────────┘
         │                     │
         ▼                     ▼
    ┌───────────┐
    │ COMPLETED │  ← Detected "Thank you" / "Submission received"
    └───────────┘

    ┌───────────┐
    │   ERROR   │  ← Max retries exceeded / exception
    └───────────┘
```

---

## 7. Feedback Loop Flow

```
Execute actions → Click "Next"
  │
  ▼
page.evaluate(DETECT_ERRORS_SCRIPT)
  │
  ├─ Scan selectors: .error, .validation-error, [role="alert"],
  │                  .invalid-feedback, :invalid, ...
  │
  ├─ [No errors] → Return False → Normal navigation
  │
  └─ [Errors found] → Enter correction loop (max 3 times):
       │
       ├─ 1. Build error context:
       │     "⚠️ Fix the following validation errors: 1. This field is required (question: Q2)"
       │
       ├─ 2. Re-call _perceive_page() for latest page state
       │
       ├─ 3. Call _decide() (requirements now include error info)
       │     → LLM sees error context, outputs corrected actions
       │
       ├─ 4. Execute corrective actions
       │
       └─ 5. Re-detect → No errors → Exit
```

---

## 8. CAPTCHA / Human Verification Flow

### Detection Trigger Timing

```
At start of each main loop iteration (agent.run while loop)
  │
  ├─ state == READY ?
  │
  └─ YES → Step 0: _check_captcha(page)
       │
       │  page.evaluate(DETECT_CAPTCHA_SCRIPT)
       │    │
       │    ├─ Category 1: iframe[src*="recaptcha/hcaptcha/turnstile"]
       │    ├─ Category 2: .g-recaptcha, [data-sitekey], .cf-turnstile
       │    ├─ Category 3: .slider-verify, .drag-verify, .nc_wrapper
       │    ├─ Category 4: Text match "CAPTCHA" / "I'm not a robot" / "Slider verification"
       │    └─ Category 5: .captcha-modal, .verify-modal, etc. overlays
       │
       ├─ has_captcha == false → Continue to Step 1
       │
       └─ has_captcha == true
            │
            ├─ state_machine.transition(AWAITING_HUMAN)
            └─ → _handle_human_intervention(page)
```

### Manual Takeover Flow

```
_handle_human_intervention(page)
  │
  ├─ 1. Screenshot: screenshot_manager.capture("captcha_blocked")
  │
  ├─ 2. Identify CAPTCHA type:
  │     captcha_iframe / captcha_container / slider_captcha
  │     / text_captcha / captcha_overlay
  │
  ├─ 3. Notify user:
  │     ┌──────────────────────────────────────────┐
  │     │  🛑 Human Intervention Required           │
  │     │     Page URL: https://xxx.com/survey      │
  │     │     Reason: CAPTCHA challenge detected    │
  │     │             (type: captcha_container)     │
  │     │     Please complete the verification      │
  │     │     in the browser window.                │
  │     │     The system will auto-resume.          │
  │     │     >>> Press Enter to continue           │
  │     │         (or wait for auto-detection)...   │
  │     └──────────────────────────────────────────┘
  │
  ├─ 4. Wait for resolution (dual channel):
  │     │
  │     ├─ Channel A: Auto-detect
  │     │   └─ Poll every 2s via CHECK_CAPTCHA_RESOLVED_SCRIPT
  │     │      Detect whether CAPTCHA elements have disappeared
  │     │      → Gone = user completed → auto-resume
  │     │
  │     └─ Channel B: Manual resume
  │         └─ User presses Enter in terminal
  │            → Immediate resume (even if auto-detect hasn't fired)
  │
  └─ 5. Resume automation:
       ├─ state_machine.transition(READY)
       └─ → Back to main loop Step 1
```

### Configuration

```bash
# .env or environment variables
CAPTCHA_TIMEOUT_SECONDS=300    # Max wait time (default: 5 minutes)
CAPTCHA_POLL_INTERVAL=2.0      # Auto-detection interval (default: 2 seconds)
```

### Supported CAPTCHA Types

| Type | Detection Method | Example |
|------|------------------|---------|
| Google reCAPTCHA | iframe `src*="recaptcha"`, `.g-recaptcha`, `[data-sitekey]` | "I'm not a robot" |
| hCaptcha | iframe `src*="hcaptcha"`, `.h-captcha` | Image selection |
| Cloudflare Turnstile | `.cf-turnstile` | Automatic verification |
| Slider CAPTCHA | `.slider-verify`, `.drag-verify`, `.nc_wrapper` | Alibaba Cloud / NetEase Yidun |
| Text prompts | "CAPTCHA", "Verification code", "Drag the slider" | Various Chinese platforms |

---

## 9. Audit Log System

Each run automatically generates the following files:

```
logs/
├── survey_agent_20260619_180217.log    ← Full run log
├── screenshots/
│   ├── 0001_180225_page_001.png        ← Per-page screenshots
│   ├── 0002_180232_page_001.png
│   └── 0003_180240_page_001.png
└── decisions/
    ├── page001_180228_792949.json       ← Per-iteration LLM decision JSON
    ├── page001_180236_322463.json
    └── page001_180245_461206.json
```

### Sample Decision Record

```json
{
  "thought": "Page has two single-choice questions: Q1 'Satisfied' maps to ui-id-1, Q2 'Blue' maps to ui-id-6. After selecting both, click Next.",
  "status": "CONTINUE",
  "actions": [
    {"type": "click", "ui_id": "ui-id-1", "reason": "Select Q1 'Satisfied' option"},
    {"type": "click", "ui_id": "ui-id-6", "reason": "Select Q2 'Blue' option"},
    {"type": "navigate", "action": "next", "reason": "All questions complete, go to next page"}
  ]
}
```

---

## 10. Key File Index

| File | Purpose | Lines |
|------|---------|-------|
| `src/survey_agent/main.py` | CLI entry + `run_survey()` | 130 |
| `src/survey_agent/config.py` | Three-layer config (LLM/Browser/Agent) | 100 |
| `src/survey_agent/core/agent.py` | Main loop orchestrator (6-step pipeline) | 327 |
| `src/survey_agent/core/state_machine.py` | Navigation state machine + button finder | 200 |
| `src/survey_agent/perception/injector.py` | 3 JS scripts + compact layout builder | 250 |
| `src/survey_agent/perception/browser_manager.py` | Browser lifecycle + anti-detection | 170 |
| `src/survey_agent/transformation/html_cleaner.py` | BS4 cleaning (~80% token savings) | 300 |
| `src/survey_agent/transformation/layout_extractor.py` | Question-type grouping (matrix/slider/radio) | 230 |
| `src/survey_agent/decision/llm_client.py` | DeepSeek API (async + retry) | 180 |
| `src/survey_agent/decision/prompt_manager.py` | System Prompt + Few-shot | 200 |
| `src/survey_agent/decision/response_parser.py` | JSON validation + type definitions | 190 |
| `src/survey_agent/execution/action_executor.py` | 5 action type executors | 220 |
| `src/survey_agent/execution/feedback_loop.py` | Error detection → LLM correction loop | 183 |
| `src/survey_agent/execution/human_interface.py` | CAPTCHA Webhook + Console | 130 |
| `prompts/system_prompt.md` | Complete System Prompt | 120 |
| `prompts/few_shot_examples.json` | 3 few-shot examples | — |
