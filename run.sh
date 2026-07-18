#!/bin/bash
# SurveyAgent
set -e
cd "$(dirname "$0")"

# ------------------------------------------------------------------
# Auto-detect REQUIREMENTS.md if no requirements flags were provided
# ------------------------------------------------------------------
HAS_REQUIREMENTS=false
for arg in "$@"; do
    if [[ "$arg" == "--requirements" || "$arg" == "--requirements-file" || "$arg" == "-r" ]]; then
        HAS_REQUIREMENTS=true
        break
    fi
done

if [[ "$HAS_REQUIREMENTS" == "false" && -f "./REQUIREMENTS.md" ]]; then
    echo "📋 Auto-detected REQUIREMENTS.md — loading as default filling rules."
    echo "   (Use --requirements or --requirements-file to override.)"
    echo ""
    set -- "$@" --requirements-file ./REQUIREMENTS.md
fi

PYTHONPATH="$(pwd)/src" python -m survey_agent.main "$@"
