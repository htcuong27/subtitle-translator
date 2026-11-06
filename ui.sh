#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ]; then
  PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
  export PATH
fi
exec uv run python web_ui.py "$@"
