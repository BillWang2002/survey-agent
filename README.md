# SurveyAgent

<p align="center">
  <strong>Intelligent Web Survey Automation</strong><br>
  Powered by Playwright Spatial-DOM Mapping + DeepSeek V4 Pro
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/status-beta-orange.svg" alt="Status: Beta">
  <img src="https://img.shields.io/badge/tests-54%20passed-brightgreen.svg" alt="Tests: 54 passed">
</p>

---

## Overview

SurveyAgent is a production-ready web survey automation system that combines **Playwright browser automation** with **DeepSeek V4 Pro's reasoning** to intelligently fill out online surveys. Instead of brittle pixel-level computer vision, it uses **spatial-DOM mapping** — injecting unique identifiers into every interactive element, extracting a structured layout, and letting the LLM decide what to click.


### Supported Question Types

| Type | Method | Example |
|------|--------|---------|
| Single choice | Click `radio` by `ui-id` | Satisfaction rating |
| Multi choice | Click `checkbox` by `ui-id` | Select all that apply |
| Matrix / Likert | Row-col mapping in JSON | "Rate 10 attributes on a 5-point scale" |
| Slider | Coordinate-based drag | "70% satisfaction" |
| Dropdown | `select_option` by value/label | City, occupation picker |
| Free text | `fill` / `type` with realistic content | Name, email, open-ended |

---

## Quick Start

### 1. Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium

# Configure API key
cp .env.example .env
# Edit .env and add your DEEPSEEK_API_KEY
```

### 2. Run

> **Recommended:** Use `./run.sh` — it automatically sets the Python path. No `pip install -e .` needed.

#### Interactive Mode (recommended for time-sensitive survey URLs)

```bash
# Paste the survey URL in terminal → browser opens → you log in → press Enter
./run.sh --interactive --requirements-file ./REQUIREMENTS.md

# Or provide the URL upfront (skips the paste step)
./run.sh -i --url "https://octopusgroup.com.au" --requirements-file ./REQUIREMENTS.md
```

#### Direct Mode (stable survey URLs)

```bash
# Visible browser (default)
./run.sh --url "https://example.com/survey" --requirements "Select 'Agree' for all"

# Headless mode (no browser window)
./run.sh --url "https://example.com/survey" --requirements "All A's" --headless

# With full audit recording (screenshots + decision JSONs — auto-cleaned on success)
./run.sh --url "https://example.com/survey" --requirements "Select 'Agree'" --record
```

#### Using `python -m` (requires `pip install -e .`)

```bash
pip install -e .
python -m survey_agent.main --interactive --requirements-file ./REQUIREMENTS.md
```

#### CLI Reference

| Flag | Description |
|------|-------------|
| `-i, --interactive` | Interactive mode: open browser, wait for login, press Enter to begin |
| `--url URL` | Survey URL (optional in interactive mode) |
| `--requirements TEXT` | Natural-language filling instructions |
| `--requirements-file PATH` | Read requirements from a file (recommended: `./REQUIREMENTS.md`) |
| `--visible` | Force visible browser mode |
| `--headless` | Headless mode (no browser window) |
| `--record` | Full audit recording: screenshots + decision JSONs. Auto-cleaned on success. |
| `--clean` | Purge all old logs, screenshots, and decision files from previous runs |
| `--trace` | Enable Playwright trace recording |
| `--model MODEL` | Override the LLM model name |
| `--api-key KEY` | Override the API key |

### 3. Programmatic API

```python
import asyncio
from survey_agent.main import run_survey

# Direct mode
result = await run_survey(
    url="https://example.com/survey",
    requirements="Select the most positive options throughout",
    headless=False,
)

# Interactive mode (waits for manual login)
result = await run_survey(
    url="https://example.com/survey",
    requirements="Select the most positive options throughout",
    interactive=True,
)
print(result)
```

---

## Project Structure

```
src/survey_agent/
├── __init__.py                  # Version info
├── main.py                      # CLI entry point
├── config.py                    # Environment-based configuration
├── core/
│   ├── agent.py                 # Main orchestrator (6-step pipeline)
│   └── state_machine.py         # Multi-page navigation state machine
├── perception/
│   ├── injector.py              # JS injection + element tagging
│   └── browser_manager.py       # Playwright browser lifecycle
├── transformation/
│   ├── html_cleaner.py          # BeautifulSoup HTML cleanup (~80% token savings)
│   └── layout_extractor.py      # Layout → structured JSON by question type
├── decision/
│   ├── llm_client.py            # DeepSeek API client (async + retry)
│   ├── prompt_manager.py        # System prompt + few-shot examples
│   └── response_parser.py       # LLM JSON output validation
├── execution/
│   ├── action_executor.py       # click / fill / slider / select / navigate
│   ├── feedback_loop.py         # Error detection + LLM retry
│   └── human_interface.py       # CAPTCHA human-in-the-loop
└── utils/
    ├── logger.py                # Structured logging
    └── screenshot.py            # Screenshot management
```

---

## Key Features

- ✅ **Full question-type coverage** — single/multi choice, matrix/Likert scales, sliders, dropdowns, free text
- ✅ **Interactive mode** — log in manually, then let the agent take over
- ✅ **Identity consistency** — AI infers target demographics and maintains coherent persona across all answers to avoid detection
- ✅ **Smart interrupt** — pauses automatically for CAPTCHAs, file uploads, or sensitive information requests
- ✅ **Feedback loop** — detects validation errors → auto-corrects → retries
- ✅ **Multi-page navigation** — state machine + LLM dual navigation, works across survey platforms
- ✅ **CAPTCHA detection** — recognizes reCAPTCHA, hCaptcha, slider verification, image CAPTCHA, Geetest, NetEase Yidun, and more
- ✅ **Structured logging** — every action and LLM decision logged to a session file; full audit trail (screenshots + decision JSONs) available via `--record`
- ✅ **Token optimization** — ~80% token savings vs raw HTML

---

## Configuration

All settings are managed via environment variables or a `.env` file. See `.env.example` for the full list.

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | *(required)* |
| `DEEPSEEK_MODEL` | Model name | `deepseek-v4-pro` |
| `DEEPSEEK_BASE_URL` | API endpoint | `https://api.deepseek.com/v1` |
| `BROWSER_HEADLESS` | Headless mode | `false` |
| `BROWSER_SLOW_MO` | Slow-motion delay (ms) | `100` |
| `AGENT_RECORD` | Enable full audit recording (screenshots + decision JSONs) | `false` |
| `ENABLE_TRACE` | Enable Playwright tracing | `false` |
| `FEEDBACK_MAX_RETRIES` | Max retry attempts on error | `3` |
| `MAX_PAGES` | Max pages before safety-stop | `50` |
| `CAPTCHA_TIMEOUT_SECONDS` | Max wait for human CAPTCHA resolution | `300` |

---

## Key Files

| File | Purpose |
|------|---------|
| `run.sh` | Launch script (auto-sets Python path — recommended) |
| `REQUIREMENTS.md` | Filling rules: identity matching, CAPTCHA handling, strategy |
| `.env` | API key and configuration |
| `prompts/system_prompt.md` | LLM system prompt |
| `prompts/few_shot_examples.json` | Few-shot examples for the LLM |

---

## License

MIT © SurveyAgent Team
