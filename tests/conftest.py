"""
Pytest fixtures for SurveyAgent tests.

Provides shared fixtures for:
- Mock configuration objects
- Playwright browser/page (with async support)
- Sample layout data for unit tests
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Configuration fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_config() -> Any:
    """Create a mock LLMConfig for testing (no API key needed)."""
    from survey_agent.config import LLMConfig
    return LLMConfig(
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-pro",
        max_tokens=4096,
        temperature=0.1,
        request_timeout=30,
        max_retries=1,
    )


@pytest.fixture
def mock_browser_config() -> Any:
    """Create a mock BrowserConfig for testing."""
    from survey_agent.config import BrowserConfig
    return BrowserConfig(
        headless=True,
        slow_mo=0,
        viewport_width=1280,
        viewport_height=720,
        action_delay=0.0,
    )


@pytest.fixture
def mock_config(mock_llm_config: Any, mock_browser_config: Any) -> Any:
    """Create a full mock Config for testing."""
    from survey_agent.config import Config, AgentConfig
    return Config(
        llm=mock_llm_config,
        browser=mock_browser_config,
        agent=AgentConfig(max_pages=5, enable_screenshot=False, enable_trace=False),
    )


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_layout_json() -> str:
    """Sample layout JSON from the JS injector (simplified)."""
    return json.dumps({
        "page_title": "Test Survey",
        "page_url": "https://example.com/survey/page1",
        "body_text": "Q1. What is your favorite color?\nRed\nBlue\nGreen",
        "interactive_elements": [
            {
                "ui_id": "ui-id-0",
                "tag": "input",
                "type": "radio",
                "name": "color",
                "text": "Red",
                "label_text": "Red",
                "visible_position": {"x": 100, "y": 100, "width": 20, "height": 20},
                "is_visible": True,
                "checked": False,
                "disabled": False,
                "required": True,
                "table_context": None,
            },
            {
                "ui_id": "ui-id-1",
                "tag": "input",
                "type": "radio",
                "name": "color",
                "text": "Blue",
                "label_text": "Blue",
                "visible_position": {"x": 100, "y": 130, "width": 20, "height": 20},
                "is_visible": True,
                "checked": False,
                "disabled": False,
                "required": True,
                "table_context": None,
            },
            {
                "ui_id": "ui-id-2",
                "tag": "input",
                "type": "radio",
                "name": "color",
                "text": "Green",
                "label_text": "Green",
                "visible_position": {"x": 100, "y": 160, "width": 20, "height": 20},
                "is_visible": True,
                "checked": False,
                "disabled": False,
                "required": True,
                "table_context": None,
            },
            {
                "ui_id": "ui-id-3",
                "tag": "button",
                "type": "button",
                "text": "Next",
                "label_text": "",
                "visible_position": {"x": 300, "y": 400, "width": 80, "height": 30},
                "is_visible": True,
                "checked": False,
                "disabled": False,
                "required": False,
                "table_context": None,
            },
        ],
    })


@pytest.fixture
def sample_layout_dict(sample_layout_json: str) -> dict:
    """Sample layout as a parsed Python dict."""
    return json.loads(sample_layout_json)


@pytest.fixture
def sample_decision() -> dict:
    """Sample valid LLM decision response."""
    return {
        "thought": "Single-choice question, 3 options. Select Blue.",
        "status": "CONTINUE",
        "actions": [
            {"type": "click", "ui_id": "ui-id-1", "reason": "Select Blue"},
            {"type": "navigate", "action": "next", "reason": "Next page"},
        ],
    }


@pytest.fixture
def sample_decision_finished() -> dict:
    """Sample LLM decision with FINISHED status."""
    return {
        "thought": "All questions are completed.",
        "status": "FINISHED",
        "actions": [
            {"type": "navigate", "action": "submit", "reason": "Submit survey"},
        ],
    }


# ---------------------------------------------------------------------------
# Mock HTML page fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pages_dir() -> Path:
    """Path to the mock HTML pages directory."""
    return Path(__file__).resolve().parent / "fixtures" / "mock_pages"


@pytest.fixture
def simple_radio_html(mock_pages_dir: Path) -> str:
    """Read the simple radio mock page HTML."""
    path = mock_pages_dir / "simple_radio.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
