#!/usr/bin/env bash
# Entrypoint to stop Gemini Live Duo Translator
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/scripts/manage.sh" stop "$@"
