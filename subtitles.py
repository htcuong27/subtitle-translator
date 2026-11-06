"""Read, write and batch SRT cues."""

import re
from pathlib import Path
from typing import Dict, List


def format_timestamp_srt(seconds: float) -> str:
    total_millis = round(max(0, seconds) * 1000)
    millis = total_millis % 1000
    total_seconds = total_millis // 1000
    secs = total_seconds % 60
    mins = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def parse_srt(srt_text: str) -> List[Dict]:
    blocks = re.split(r"\n\s*\n", srt_text.strip(), flags=re.MULTILINE)
    items = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        index = lines[0].strip()
        timestamp = lines[1].strip()
        text = "\n".join(lines[2:]).strip()
        items.append({"index": index, "timestamp": timestamp, "text": text})
    return items


def has_source_subtitles(path: Path) -> bool:
    if not path.is_file():
        return False
    items = parse_srt(path.read_text(encoding="utf-8-sig", errors="replace"))
    return bool(items) and all(item["index"].isdigit() and " --> " in item["timestamp"]
                               and item["text"] for item in items)


def write_srt(items: List[Dict], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(f"{item['index']}\n")
            f.write(f"{item['timestamp']}\n")
            f.write(f"{item['text']}\n\n")


def batch_items(items: List[Dict], batch_size: int = 80):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]
