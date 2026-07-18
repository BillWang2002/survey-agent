"""
Structured logging for SurveyAgent.

Provides:
- Timestamped, level-based logging to both console and file
- Per-session log files
- Decision logging (records every LLM decision for audit trail)
- Trace-level debugging for the feedback loop
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Module-level logger cache
_loggers: dict[str, logging.Logger] = {}
_log_dir: Path | None = None
_session_start: str = ""


def setup_logging(
    log_dir: str | Path = "./logs",
    level: int = logging.INFO,
    console_level: int = logging.INFO,
) -> None:
    """
    Configure the root logger with console and file handlers.

    Should be called once at application startup.

    Args:
        log_dir: Directory for log files.
        level: File logging level.
        console_level: Console logging level.
    """
    global _log_dir, _session_start
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)
    _session_start = datetime.now().strftime("%Y%m%d_%H%M%S")

    root_logger = logging.getLogger("survey_agent")
    root_logger.setLevel(logging.DEBUG)

    # Clear existing handlers
    root_logger.handlers.clear()

    # --- Console handler (colored) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(_ColoredFormatter())
    root_logger.addHandler(console_handler)

    # --- File handler (detailed) ---
    log_file = _log_dir / f"survey_agent_{_session_start}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)

    # Quiet noisy third-party loggers
    for noisy in ("openai", "httpx", "httpcore", "playwright"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root_logger.info(f"Logging to {log_file}")


def get_logger(name: str) -> logging.Logger:
    """Get a named logger (cached)."""
    if name not in _loggers:
        _loggers[name] = logging.getLogger(f"survey_agent.{name}")
    return _loggers[name]


def log_decision(decision: dict, page_number: int = 0, *, record: bool = False) -> None:
    """
    Log a complete LLM decision.

    Always writes a summary to the main log. Only writes a separate JSON
    decision file when ``record`` is enabled.

    Args:
        decision: The validated decision dict from the LLM.
        page_number: Current page number.
        record: If True, also persist the decision as a JSON file.
    """
    logger = get_logger("decision")
    thought = decision.get("thought", "")
    actions = decision.get("actions", [])
    status = decision.get("status", "?")

    logger.info(
        f"🧠 Decision [page={page_number}, status={status}]: "
        f"{thought[:120]}{'...' if len(thought) > 120 else ''}"
    )

    for action in actions:
        logger.debug(
            f"  → {action.get('type', '?')}: {action.get('ui_id', '?')} "
            f"({action.get('reason', '')})"
        )

    # Persist decision JSON only when recording
    if record and _log_dir:
        decision_dir = _log_dir / "decisions"
        decision_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%H%M%S_%f")
        decision_file = decision_dir / f"page{page_number:03d}_{timestamp}.json"
        try:
            decision_file.write_text(
                json.dumps(decision, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass  # Don't let logging failures crash the agent


def cleanup_artifacts(log_dir: str | Path = "./logs") -> None:
    """
    Remove screenshot and decision directories after a successful run.

    Keeps the main log file intact — only removes the heavyweight artifact
    directories that are useful for debugging failures but unnecessary for
    normal successful runs.

    Args:
        log_dir: The log directory to clean up.
    """
    import shutil

    base = Path(log_dir)
    for sub in ("screenshots", "decisions"):
        target = base / sub
        if target.exists() and target.is_dir():
            try:
                shutil.rmtree(target)
                logging.getLogger("survey_agent").info(
                    f"Cleaned up artifact directory: {target}"
                )
            except Exception:
                pass


def clear_old_logs(log_dir: str | Path = "./logs") -> int:
    """
    Purge all existing log files, screenshots, and decision records.

    Call this before starting a new run to wipe the slate clean.
    The log directory itself is preserved — only its contents are removed.

    Args:
        log_dir: The log directory to purge.

    Returns:
        Number of files/directories removed.
    """
    import shutil

    base = Path(log_dir)
    if not base.exists() or not base.is_dir():
        return 0

    count = 0
    for entry in base.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            count += 1
        except Exception:
            pass

    if count > 0:
        print(f"🧹 Cleared {count} item(s) from {base.resolve()}/")
    return count


# ---------------------------------------------------------------------------
# Colored console formatter
# ---------------------------------------------------------------------------

class _ColoredFormatter(logging.Formatter):
    """Adds ANSI color codes to log levels for better readability."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        levelname = f"{color}{record.levelname:<8}{self.RESET}"
        record.levelname = levelname
        return (
            f"\033[90m{self.formatTime(record, '%H:%M:%S')}\033[0m "
            f"| {record.levelname} "
            f"| \033[90m{record.name}\033[0m "
            f"| {record.getMessage()}"
        )
