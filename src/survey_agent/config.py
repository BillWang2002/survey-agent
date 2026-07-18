"""
Configuration management for SurveyAgent.

Loads settings from environment variables with sensible defaults.
Uses .env file via python-dotenv for local development.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load from the project root .env (3 levels up from this file)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


@dataclass
class LLMConfig:
    """DeepSeek V4 Pro (or compatible) API configuration."""

    api_key: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )
    base_url: str = field(
        default_factory=lambda: os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
    )
    model: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    )
    max_tokens: int = field(
        default_factory=lambda: int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096"))
    )
    temperature: float = field(
        default_factory=lambda: float(os.getenv("DEEPSEEK_TEMPERATURE", "0.1"))
    )
    request_timeout: int = field(
        default_factory=lambda: int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "3"))
    )


@dataclass
class BrowserConfig:
    """Playwright browser configuration."""

    headless: bool = field(
        default_factory=lambda: os.getenv("BROWSER_HEADLESS", "false").lower()
        == "true"
    )
    slow_mo: int = field(
        default_factory=lambda: int(os.getenv("BROWSER_SLOW_MO", "100"))
    )
    viewport_width: int = field(
        default_factory=lambda: int(os.getenv("BROWSER_VIEWPORT_WIDTH", "1920"))
    )
    viewport_height: int = field(
        default_factory=lambda: int(os.getenv("BROWSER_VIEWPORT_HEIGHT", "1080"))
    )
    action_delay: float = field(
        default_factory=lambda: float(os.getenv("ACTION_DELAY", "0.5"))
    )
    page_load_timeout: int = field(
        default_factory=lambda: int(os.getenv("PAGE_LOAD_TIMEOUT", "30000"))
    )
    navigation_timeout: int = field(
        default_factory=lambda: int(os.getenv("NAVIGATION_TIMEOUT", "60000"))
    )


@dataclass
class AgentConfig:
    """SurveyAgent behavioral configuration."""

    max_pages: int = field(
        default_factory=lambda: int(os.getenv("MAX_PAGES", "50"))
    )
    max_actions_per_page: int = field(
        default_factory=lambda: int(os.getenv("MAX_ACTIONS_PER_PAGE", "100"))
    )
    feedback_max_retries: int = field(
        default_factory=lambda: int(os.getenv("FEEDBACK_MAX_RETRIES", "3"))
    )
    record: bool = field(
        default_factory=lambda: os.getenv("AGENT_RECORD", "false").lower()
        == "true"
    )
    enable_trace: bool = field(
        default_factory=lambda: os.getenv("ENABLE_TRACE", "false").lower()
        == "true"
    )
    log_dir: str = field(
        default_factory=lambda: os.getenv("LOG_DIR", "./logs")
    )
    trace_dir: str = field(
        default_factory=lambda: os.getenv("TRACE_DIR", "./traces")
    )
    human_interface_webhook: str = field(
        default_factory=lambda: os.getenv("HUMAN_INTERFACE_WEBHOOK", "")
    )
    captcha_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("CAPTCHA_TIMEOUT_SECONDS", "300"))
    )
    captcha_poll_interval: float = field(
        default_factory=lambda: float(os.getenv("CAPTCHA_POLL_INTERVAL", "2.0"))
    )


@dataclass
class Config:
    """Top-level configuration aggregator."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    @classmethod
    def from_env(cls) -> "Config":
        """Create a Config instance from environment variables."""
        return cls()


# Singleton config instance
config = Config.from_env()
