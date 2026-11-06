#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ]; then
  PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
  export PATH
fi
for argument in "$@"; do
  if [ "$argument" = "--list" ]; then
    exec python3 translate.py "$@"
  fi
done
exec uv run python translate.py "$@"
