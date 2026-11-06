"""FFmpeg and ffprobe operations shared by the engine and recognition."""

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryFile
from typing import Dict, List, Tuple


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()[-2000:]
        raise RuntimeError(f"{cmd[0]} thất bại (mã {error.returncode}): {detail}") from None


def report_progress(stage, percent, detail=""):
    print(f"[{stage}] {percent}%{detail}", flush=True)


def progress_items(stage, items):
    """Report completed items in roughly 5% increments."""
    if not items:
        return
    report_progress(stage, 0)
    last = 0
    for index, item in enumerate(items, 1):
        yield item
        percent = 100 * index // len(items)
        if percent // 5 > last // 5:
            report_progress(stage, percent)
            last = percent


def run_ffmpeg_export(cmd, input_video, cwd=None):
    _, duration, _, _ = _get_video_info(input_video)
    command = [cmd[0], "-progress", "pipe:1", "-nostdin", *cmd[1:]]
    last = 0
    with TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as errors:
        with subprocess.Popen(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=errors) as process:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and duration > 0 and value.lstrip("-").isdigit():
                    percent = min(99, int(int(value) / (duration * 10_000)))
                    if percent // 5 > last // 5:
                        report_progress("Xuất video", percent)
                        last = percent
            if process.wait():
                errors.seek(0)
                detail = errors.read()[-2000:].strip()
                raise RuntimeError(f"ffmpeg thất bại (mã {process.returncode}): {detail}")


def probe_subtitle_streams(video_path: Path) -> List[Dict]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-of", "json",
        str(video_path),
    ]
    result = run(cmd)
    data = json.loads(result.stdout or "{}")
    return data.get("streams", [])


def _get_video_info(video_path: Path) -> Tuple[float, float, int, int]:
    """Return (fps, duration_sec, width, height) via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    result = run(cmd)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    fmt = data.get("format", {})
    w = int(stream["width"])
    h = int(stream["height"])
    rate_parts = stream["r_frame_rate"].split("/")
    fps = float(rate_parts[0]) / float(rate_parts[1]) if len(rate_parts) == 2 else float(rate_parts[0])
    duration = float(fmt.get("duration", 0))
    if duration == 0 and "nb_frames" in stream:
        duration = int(stream["nb_frames"]) / fps
    return fps, duration, w, h
