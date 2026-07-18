"""
Main entry point for SurveyAgent.

Provides both CLI interface and programmatic API for running
the intelligent survey automation system.

Usage:
    python -m survey_agent.main --url https://example.com/survey \\
        --requirements "Select 'Agree' for all, fill 'N/A' for text"

    # Or programmatically:
    from survey_agent.main import run_survey
    await run_survey(url="https://example.com/survey", requirements="...")
"""

import argparse
import asyncio
import sys
from pathlib import Path

from survey_agent.config import config
from survey_agent.core.agent import SurveyAgent
from survey_agent.utils.logger import clear_old_logs, get_logger, setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="SurveyAgent — Intelligent Web Survey Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m survey_agent.main --url https://forms.example.com/survey
  python -m survey_agent.main --url https://forms.example.com/survey --requirements "All A"
  python -m survey_agent.main --url https://forms.example.com/survey --headless --trace
        """,
    )

    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Target survey URL to fill (optional in interactive mode)",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        default=False,
        help="Interactive mode: open browser, wait for user to navigate to survey, "
             "then press Enter to start auto-filling. Useful when survey URLs are "
             "time-sensitive or require manual login.",
    )
    parser.add_argument(
        "--requirements",
        type=str,
        default="",
        help="Natural language filling requirements (e.g., 'Select Agree for all, fill N/A for text')",
    )
    parser.add_argument(
        "--requirements-file",
        type=str,
        default=None,
        help="Path to a file containing filling requirements",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=None,
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        default=None,
        help="Run browser in visible mode (override config)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        default=None,
        help="Enable Playwright trace recording",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        default=None,
        help="Enable full audit recording: per-page screenshots + LLM decision JSONs. "
             "Artifacts are auto-cleaned after a successful run (kept on failure for debugging).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=False,
        help="Purge all old logs, screenshots, and decision files from the log directory "
             "before starting a new run.",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Directory for log output",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the LLM model name",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="DeepSeek API key (overrides env DEEPSEEK_API_KEY)",
    )

    return parser.parse_args()


async def run_survey(
    url: str | None = None,
    requirements: str = "",
    *,
    interactive: bool = False,
    headless: bool | None = None,
    enable_trace: bool | None = None,
    record: bool | None = None,
    log_dir: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Run the survey agent against a target URL.

    Args:
        url: The target survey URL. Optional in interactive mode.
        requirements: Natural language description of how to fill the survey.
        interactive: If True, open browser and wait for user to navigate to survey
                     before starting automation. Useful for time-sensitive URLs.
        headless: Override browser headless mode.
        enable_trace: Override trace recording.
        record: Enable full audit recording (screenshots + decision JSONs).
                Artifacts are auto-cleaned on success, kept on failure.
        log_dir: Override log directory.
        model: Override LLM model.
        api_key: Override API key.

    Returns:
        A dict with run summary: {"status": "success", "pages_filled": N, ...}
    """
    # Apply overrides
    if headless is not None:
        config.browser.headless = headless
    if enable_trace is not None:
        config.agent.enable_trace = enable_trace
    if record is not None:
        config.agent.record = record
    if log_dir is not None:
        config.agent.log_dir = log_dir
    if model is not None:
        config.llm.model = model
    if api_key is not None:
        config.llm.api_key = api_key

    setup_logging(config.agent.log_dir)

    agent = SurveyAgent(config)
    return await agent.run(url, requirements, interactive=interactive)


def main() -> None:
    """CLI entry point."""
    args = parse_args()

    # Validate: URL is required unless in interactive mode
    if not args.interactive and not args.url:
        print(
            "Error: --url is required (or use --interactive for manual-start mode).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load requirements from file if specified
    requirements = args.requirements
    if args.requirements_file:
        req_path = Path(args.requirements_file)
        if not req_path.exists():
            print(f"Error: Requirements file not found: {req_path}", file=sys.stderr)
            sys.exit(1)
        requirements = req_path.read_text(encoding="utf-8").strip()

    # Purge old logs if requested
    if args.clean:
        log_dir = args.log_dir or config.agent.log_dir
        clear_old_logs(log_dir)

    # Determine headless mode: --visible takes precedence over --headless
    # Interactive mode forces visible browser
    headless = None
    if args.visible or args.interactive:
        headless = False
    elif args.headless:
        headless = True

    try:
        result = asyncio.run(
            run_survey(
                url=args.url,
                requirements=requirements,
                interactive=args.interactive,
                headless=headless,
                enable_trace=args.trace,
                record=args.record,
                log_dir=args.log_dir,
                model=args.model,
                api_key=args.api_key,
            )
        )
        print(f"\n✅ Survey completed: {result}")
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
