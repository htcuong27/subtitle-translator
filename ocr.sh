#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec uv run python cr_region.py "$@"
