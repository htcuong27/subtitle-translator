"""Speech recognition, OCR and subtitle timing."""

import json
import math
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple

from media import _get_video_info, progress_items
from subtitles import format_timestamp_srt, parse_srt, write_srt


def clean_ocr_subtitle(text):
    return "\n".join(line.strip() for line in text.replace("\ufffd", "").splitlines() if any(c.isalnum() for c in line))


def _ocr_engine():
    """Lazy-load RapidOCR. Returns engine or None if not installed."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        return RapidOCR()
    except ImportError:
        return None


def prepare_ocr_frame(frame, config):
    """Mask everything outside OCR regions."""
    import numpy as np
    height, width = frame.shape[:2]
    mask = np.zeros((height, width), dtype=bool)
    for region in config['regions']:
        y1, y2, x1, x2 = parse_crop_box(','.join(map(str, region)), width, height)
        mask[y1:y2, x1:x2] = True
    result = frame.copy()
    result[~mask] = 0
    return result


def parse_ocr_config(value):
    config = json.loads(value) if isinstance(value, str) else value
    if not isinstance(config, dict) or not config.get('regions'):
        raise ValueError('Cần ít nhất một vùng OCR')
    for name in ('regions',):
        regions = config.get(name, [])
        if not isinstance(regions, list):
            raise ValueError('Danh sách vùng OCR không hợp lệ')
        for box in regions:
            if (not isinstance(box, list) or len(box) != 4 or
                    any(type(v) not in (int, float) or not 0 <= v <= 1 for v in box) or
                    box[0] >= box[1] or box[2] >= box[3]):
                raise ValueError('Vùng OCR phải có tọa độ tỉ lệ hợp lệ')
    return config


def _white_horizontal_lines(crop, ocr):
    import cv2
    import numpy as np

    results, _ = ocr(crop)
    kept = []
    for points, text, confidence in results or []:
        text = text.strip()
        confidence = float(confidence)
        if not text or confidence <= 0.40:
            continue

        box = np.asarray(points, dtype=np.float32)
        top, bottom = box[1] - box[0], box[2] - box[3]
        height = (np.linalg.norm(box[3] - box[0]) + np.linalg.norm(box[2] - box[1])) / 2
        width = (np.linalg.norm(top) + np.linalg.norm(bottom)) / 2
        if height < 2 or width < 0.85 * height:
            continue
        if any(abs(dy) > 0.14 * abs(dx) for dx, dy in (top, bottom)):
            continue

        x, y, w, h = cv2.boundingRect(box)
        x0, y0 = max(0, x), max(0, y)
        patch = crop[y0:y+h, x0:x+w]
        if not patch.size:
            continue
        mask = np.zeros(patch.shape[:2], dtype=np.uint8)
        cv2.fillConvexPoly(mask, np.rint(box - (x0, y0)).astype(np.int32), 1)
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        # Local contrast rejects flat bright backgrounds while retaining white strokes.
        dark_nearby = cv2.erode(gray, np.ones((5, 5), dtype=np.uint8))
        bright_edges = (patch.max(axis=2) >= 195) & (gray - dark_nearby >= 35) & (mask != 0)
        white = (patch.min(axis=2) >= 195) & (np.ptp(patch, axis=2) <= 35)
        white_edges = np.count_nonzero(bright_edges & white)
        if white_edges >= max(4, 0.015 * np.count_nonzero(mask), 0.5 * np.count_nonzero(bright_edges)):
            center = (float(box[:, 1].mean()) / crop.shape[0],
                      float(box[:, 0].mean()) / crop.shape[1])
            kept.append((center, text, confidence))

    return sorted(kept)


def read_white_horizontal_text(crop, ocr):
    return [(text, confidence) for _, text, confidence in _white_horizontal_lines(crop, ocr)]


def _ocr_crop_at(cap, ocr, t_sec, fps, y1, y2, x1, x2, duration, ocr_config=None, white_horizontal=False, frame_index=None) -> str:
    """Read frame at t_sec and OCR the subtitle crop region. Returns text or ''."""
    import cv2
    if t_sec < 0 or t_sec > duration:
        return ""
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t_sec * fps) if frame_index is None else frame_index)
    ret, frame = cap.read()
    if not ret:
        return ""
    if ocr_config:
        frame = prepare_ocr_frame(frame, ocr_config)
    crop = frame[y1:y2, x1:x2]
    if white_horizontal:
        return clean_ocr_subtitle("\n".join(text for text, _ in read_white_horizontal_text(crop, ocr)))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    white_px = int((gray > 215).sum())
    if white_px < 120 or white_px > (crop.shape[0] * crop.shape[1] * 0.45):
        return ""
    res, _ = ocr(crop)
    if not res:
        return ""
    return "".join(clean_ocr_subtitle(text) for _, text, confidence in res if float(confidence) > 0.40)


def _smart_max_duration(text: str) -> float:
    """Max display duration based on text length. Prevents static subtitles."""
    return max(1.0, min(6.0, len(text) * 0.4 + 0.6))


# ---------------------------------------------------------------------------
# Whisper split by intra-segment silence
# ---------------------------------------------------------------------------

def _split_whisper_by_gaps(segments, gap_threshold: float = 0.75) -> List[Dict]:
    """
    Split Whisper segments further whenever word-level gap > gap_threshold.
    This prevents one segment from spanning a long silence (frozen subtitle).
    Returns list of {'start', 'end', 'text'} dicts.
    """
    result = []
    for seg in segments:
        words = [w for w in (seg.words or []) if w.start is not None and w.end is not None]
        if not words:
            if seg.text.strip():
                result.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
            continue
        cur = [words[0]]
        for w in words[1:]:
            if w.start - cur[-1].end > gap_threshold:
                s, e = cur[0].start, cur[-1].end
                t = "".join(x.word.strip() for x in cur)
                if t:
                    result.append({"start": s, "end": e, "text": t})
                cur = [w]
            else:
                cur.append(w)
        if cur:
            s, e = cur[0].start, cur[-1].end
            t = "".join(x.word.strip() for x in cur)
            if t:
                result.append({"start": s, "end": e, "text": t})
    return result


# ---------------------------------------------------------------------------
# Gap scanning for hardsubs missed by Whisper
# ---------------------------------------------------------------------------

def _scan_gaps_for_hardsubs(cap, ocr, speech_segs, fps, duration, y1, y2, x1, x2,
                             gap_threshold=2.0, step=1.0, ocr_config=None) -> List[Dict]:
    """Scan silence gaps between speech segments for hardcoded subtitles."""
    gaps = []
    prev_end = 0.0
    for s in sorted(speech_segs, key=lambda x: x["start"]):
        if s["start"] - prev_end > gap_threshold:
            gaps.append((prev_end, s["start"]))
        prev_end = max(prev_end, s["end"])
    if duration - prev_end > gap_threshold:
        gaps.append((prev_end, duration))

    gap_subs = []
    for g_start, g_end in progress_items("OCR quét khoảng lặng", gaps):
        t = g_start + 0.8
        last_text = ""
        last_text_start = None
        while t < g_end - 0.4:
            text = _ocr_crop_at(cap, ocr, t, fps, y1, y2, x1, x2, duration, ocr_config=ocr_config)
            if text and len(text) >= 2:
                if text != last_text:
                    if last_text and last_text_start is not None:
                        gap_subs.append({
                            "start": max(0.0, last_text_start),
                            "end": min(duration, t - 0.1),
                            "text": last_text,
                        })
                    last_text = text
                    last_text_start = t - 0.4
            else:
                if last_text and last_text_start is not None:
                    gap_subs.append({
                        "start": max(0.0, last_text_start),
                        "end": min(duration, t - 0.05),
                        "text": last_text,
                    })
                    last_text = ""
                    last_text_start = None
            t += step
        if last_text and last_text_start is not None:
            gap_subs.append({
                "start": max(0.0, last_text_start),
                "end": min(duration, t),
                "text": last_text,
            })
    return gap_subs


# ---------------------------------------------------------------------------
# Merge overlapping / duplicate subtitle segments
# ---------------------------------------------------------------------------

def _text_overlap_ratio(a: str, b: str) -> float:
    """Ratio of matching chars between two strings (simple longest-common-chars approach)."""
    if not a or not b:
        return 0.0
    # Use shorter/longer as basis
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # Count characters of shorter that appear in longer (positional)
    matched = 0
    pos = 0
    for ch in shorter:
        idx = longer.find(ch, pos)
        if idx != -1:
            matched += 1
            pos = idx + 1
    return matched / max(len(shorter), 1)


def _merge_segments(items: List[Dict]) -> List[Dict]:
    """Merge overlapping or same/similar-text adjacent segments."""
    items = sorted(items, key=lambda x: x["start"])
    merged = []
    for item in items:
        if not item.get("text", "").strip():
            continue
        if not merged:
            merged.append(dict(item))
            continue
        prev = merged[-1]
        gap = item["start"] - prev["end"]

        # Exact same text within 2s gap -> extend
        if item["text"] == prev["text"] and gap < 2.0:
            prev["end"] = max(prev["end"], item["end"])
            continue

        # One contains the other (substring match)
        if item["text"] in prev["text"] and gap < 2.0:
            prev["end"] = max(prev["end"], item["end"])
            continue
        if prev["text"] in item["text"] and gap < 2.0:
            prev["text"] = item["text"]  # prefer the longer/fuller version
            prev["end"] = max(prev["end"], item["end"])
            continue

        # Fuzzy near-duplicate: > 85% char overlap and gap < 1.5s -> merge, keep longer text
        if gap < 1.5 and _text_overlap_ratio(prev["text"], item["text"]) > 0.85:
            if len(item["text"]) > len(prev["text"]):
                prev["text"] = item["text"]
            prev["end"] = max(prev["end"], item["end"])
            continue

        # Overlapping segments
        if item["start"] < prev["end"]:
            # Trim previous end to avoid overlap
            prev["end"] = item["start"] - 0.05
            if prev["end"] <= prev["start"]:
                merged.pop()
            merged.append(dict(item))
        else:
            merged.append(dict(item))
    return merged


# ---------------------------------------------------------------------------
# Core transcription functions
# ---------------------------------------------------------------------------

def _recognize_speech(video_path: Path, language: str):
    print(f"Đang tải Whisper model để nhận diện âm thanh ({language})...")
    from faster_whisper import WhisperModel
    whisper_model_name = os.getenv("WHISPER_MODEL", "base")
    model = WhisperModel(whisper_model_name, device="auto", compute_type="default")
    print(f"Đang phân tích âm thanh từ {video_path.name}...")
    segments, _ = model.transcribe(
        str(video_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400),
    )
    return _split_whisper_by_gaps(segments)


def transcribe_video_audio(video_path: Path, output_srt: Path, language: str = "zh") -> None:
    """Whisper-only audio transcription with smart word-gap splitting."""
    raw_segs = _recognize_speech(video_path, language)
    items = []
    for idx, seg in enumerate(raw_segs, start=1):
        text = seg["text"].strip()
        if not text:
            continue
        start_ts = format_timestamp_srt(seg["start"])
        end_ts = format_timestamp_srt(seg["end"])
        items.append({
            "index": str(idx),
            "timestamp": f"{start_ts} --> {end_ts}",
            "text": text,
        })
    write_srt(items, output_srt)
    print(f"Hoàn thành trích xuất âm thanh thành phụ đề tiếng Trung ({len(items)} câu): {output_srt}")


def parse_crop_box(crop_str: str, width: int, height: int) -> Tuple[int, int, int, int]:
    """Parse 'ymin,ymax,xmin,xmax' to pixel coordinates (y1, y2, x1, x2)."""
    if not crop_str:
        return int(height * 0.78), int(height * 0.96), int(width * 0.12), int(width * 0.88)
    parts = [float(p.strip()) for p in crop_str.split(",")]
    if len(parts) != 4:
        raise ValueError("crop-box phải có 4 giá trị: ymin,ymax,xmin,xmax")
    ymin, ymax, xmin, xmax = parts
    y1 = int(ymin * height) if ymin <= 1.0 else int(ymin)
    y2 = int(ymax * height) if ymax <= 1.0 else int(ymax)
    x1 = int(xmin * width) if xmin <= 1.0 else int(xmin)
    x2 = int(xmax * width) if xmax <= 1.0 else int(xmax)
    y1 = max(0, min(height - 1, y1))
    y2 = max(y1 + 1, min(height, y2))
    x1 = max(0, min(width - 1, x1))
    x2 = max(x1 + 1, min(width, x2))
    return y1, y2, x1, x2


def _similar_ocr_text(first, second):
    if first == second:
        return True
    length = min(len(first), len(second))
    if length < 5:
        return False
    limit = min(2, max(1, length // 6))
    if abs(len(first) - len(second)) > limit:
        return False
    changes = SequenceMatcher(None, first, second, autojunk=False).get_opcodes()
    return sum(max(a2 - a1, b2 - b1) for tag, a1, a2, b1, b2 in changes if tag != 'equal') <= limit


def _group_ocr_samples(texts):
    readings = Counter()
    anchor = ""
    start = 0
    end = 0
    for index, text in enumerate(texts):
        normalized = "".join(text.split())
        # Compare against the first sample so small changes cannot accumulate.
        if readings and (not normalized or not _similar_ocr_text(anchor, normalized)):
            yield start, index, readings.most_common(1)[0][0]
            readings.clear()
        if normalized:
            if not readings:
                anchor, start = normalized, index
            readings[text] += 1
            end = index + 1
    if readings:
        yield start, end, readings.most_common(1)[0][0]


def _refine_ocr_transition(left_frame, right_frame, left_text, right_text, read, stride):
    left = "".join(left_text.split())
    right = "".join(right_text.split())
    frames = [*range(left_frame, right_frame, stride), right_frame]
    readings = [left, *("".join(read(frame).split()) for frame in frames[1:-1]), right]

    # Follow the new reading back from the confirmed sample on the right.
    start = len(frames) - 1
    while start > 1:
        previous = readings[start - 1]
        if previous == left or not _similar_ocr_text(right, previous):
            break
        start -= 1

    # A short blank between two cues can fit entirely inside this bracket.
    last = start - 1
    while last > 0 and not _similar_ocr_text(left, readings[last]):
        last -= 1
    return frames[last + 1], frames[start]


def transcribe_video_ocr(video_path, output_srt, crop_box=None, ocr_config=None):
    """Read hardcoded subtitles and derive timing from frame samples."""
    import cv2

    ocr = _ocr_engine()
    if ocr is None:
        raise RuntimeError("Cần RapidOCR để chạy chế độ Chỉ OCR")
    fps, duration, width, height = _get_video_info(video_path)
    bounds = (0, height, 0, width) if ocr_config else crop_box or parse_crop_box(None, width, height)
    y1, y2, x1, x2 = bounds
    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count > 0:
        duration = min(duration, frame_count / fps)
    last_frame = max(0, math.ceil(duration * fps) - 1)
    if frame_count > 0:
        last_frame = min(last_frame, frame_count - 1)
    coarse_stride = max(1, round(0.4 * fps))
    fine_stride = max(1, round(0.05 * fps))
    frames = [*range(0, last_frame, coarse_stride), last_frame]
    items = []
    try:
        def read(frame):
            return _ocr_crop_at(cap, ocr, frame / fps, fps, y1, y2, x1, x2, duration,
                                ocr_config=ocr_config, white_horizontal=True,
                                frame_index=frame)

        readings = [read(frame) for frame in progress_items("OCR quét khung hình", frames)]
        transitions = {}

        def transition(index):
            if index not in transitions:
                transitions[index] = _refine_ocr_transition(
                    frames[index - 1], frames[index], readings[index - 1], readings[index],
                    read, fine_stride,
                )
            return transitions[index]

        for first, last, text in progress_items("OCR tinh chỉnh timing", list(_group_ocr_samples(readings))):
            start = transition(first)[1] / fps if first else 0.0
            end = transition(last)[0] / fps if last < len(frames) else duration
            if end > start:
                items.append({"index": str(len(items) + 1), "text": text,
                              "timestamp": f"{format_timestamp_srt(start)} --> {format_timestamp_srt(end)}"})
    finally:
        cap.release()
    if not items:
        raise RuntimeError("OCR không tìm thấy phụ đề. Hãy kiểm tra vùng OCR và chữ trên video.")
    temporary = output_srt.with_suffix(".srt.tmp")
    write_srt(items, temporary)
    temporary.replace(output_srt)
    print(f"Hoàn thành trích xuất OCR ({len(items)} câu): {output_srt}")


def transcribe_video_hybrid(video_path: Path, output_srt: Path, language: str = "zh",
                             crop_box: Tuple[int, int, int, int] = None, ocr_config=None) -> None:
    """
    Hybrid transcription combining Whisper ASR + RapidOCR hardsub reading.

    Pipeline:
    1. Whisper: get word-level timestamps (accurate start/end of speech)
    2. OCR: read actual hardsub text from video frames (more accurate than Whisper ASR)
    3. Gap scan: find subtitles in silence gaps that Whisper completely missed
    4. Merge: deduplicate overlapping entries
    5. Smart timing: cap duration per text length so subtitles disappear with speech
    """
    import cv2

    ocr = _ocr_engine()
    if ocr is None:
        print("⚠ rapidocr-onnxruntime không được cài. Dùng chế độ Whisper-only.")
        print("  Để cài: pip install rapidocr-onnxruntime")
        return transcribe_video_audio(video_path, output_srt, language)

    speech_segs = _recognize_speech(video_path, language)
    print(f"Whisper nhận diện {len(speech_segs)} đoạn lời nói.")

    fps, duration, width, height = _get_video_info(video_path)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if ocr_config:
            y1, y2, x1, x2 = 0, height, 0, width
        elif crop_box:
            y1, y2, x1, x2 = crop_box
        else:
            y1, y2, x1, x2 = parse_crop_box(None, width, height)

        # Phase 1: enrich speech segments with OCR text
        print("Đang trích xuất chữ hardsub từ video bằng OCR...")
        combined = []
        for seg in progress_items("OCR theo lời thoại", speech_segs):
            mid = (seg["start"] + seg["end"]) / 2.0
            text_ocr = _ocr_crop_at(cap, ocr, mid, fps, y1, y2, x1, x2, duration, ocr_config=ocr_config)
            if not text_ocr:
                text_ocr = _ocr_crop_at(cap, ocr, seg["start"] + 0.3, fps, y1, y2, x1, x2, duration, ocr_config=ocr_config)
            final_text = text_ocr if text_ocr else seg["text"].strip()
            if not final_text:
                continue
            combined.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": final_text,
                "source": "hybrid" if text_ocr else "whisper",
            })

        # Phase 2: gap scan for missed hardsubs
        print("Đang quét khoảng lặng để tìm phụ đề bị bỏ sót...")
        gap_subs = _scan_gaps_for_hardsubs(
            cap, ocr, speech_segs, fps, duration, y1, y2, x1, x2,
            gap_threshold=2.0, step=1.0, ocr_config=ocr_config,
        )
        if gap_subs:
            print(f"  → Tìm thêm {len(gap_subs)} câu phụ đề trong khoảng lặng.")
        for gs in gap_subs:
            combined.append({**gs, "source": "gap_ocr"})
    finally:
        cap.release()

    # Phase 3: merge overlapping/duplicates
    merged = _merge_segments(combined)

    # Phase 4: apply smart duration cap
    final_items = []
    for idx, item in enumerate(merged, start=1):
        text = item["text"].strip()
        if not text:
            continue
        start = item["start"]
        end = item["end"]
        max_dur = _smart_max_duration(text)
        if end - start > max_dur:
            end = start + max_dur
        if end - start < 0.4:
            end = start + 0.4
        start_ts = format_timestamp_srt(start)
        end_ts = format_timestamp_srt(end)
        final_items.append({
            "index": str(idx),
            "timestamp": f"{start_ts} --> {end_ts}",
            "text": text,
        })

    write_srt(final_items, output_srt)
    print(f"Hoàn thành trích xuất hybrid ({len(final_items)} câu): {output_srt}")


def _run_ocr_correction_pass(video_path: Path, zh_srt: Path,
                             crop_box: Tuple[int, int, int, int] = None, ocr_config=None) -> None:
    """
    When video has an embedded subtitle stream, run OCR to verify/correct text.
    Updates zh_srt in-place when OCR reads hardsub more accurately.
    """
    import cv2

    ocr = _ocr_engine()
    if ocr is None:
        return

    items = parse_srt(zh_srt.read_text(encoding="utf-8", errors="ignore"))
    if not items:
        return

    fps, duration, width, height = _get_video_info(video_path)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if ocr_config:
            y1, y2, x1, x2 = 0, height, 0, width
        elif crop_box:
            y1, y2, x1, x2 = crop_box
        else:
            y1, y2, x1, x2 = parse_crop_box(None, width, height)

        def _ts_to_sec(s):
            h, m, rest = s.split(":")
            secs, ms = rest.replace(",", ".").split(".")
            return int(h) * 3600 + int(m) * 60 + int(secs) + int(ms) / 1000

        corrected = 0
        updated_items = []
        for item in progress_items("OCR hiệu chỉnh phụ đề", items):
            parts = item["timestamp"].split(" --> ")
            t_start = _ts_to_sec(parts[0])
            t_end = _ts_to_sec(parts[1])
            mid = (t_start + t_end) / 2.0

            ocr_text = _ocr_crop_at(cap, ocr, mid, fps, y1, y2, x1, x2, duration, ocr_config=ocr_config)
            if ocr_text and len(ocr_text) >= 2 and ocr_text != item["text"]:
                item = dict(item)
                item["text"] = ocr_text
                corrected += 1
            updated_items.append(item)
    finally:
        cap.release()
    if corrected > 0:
        write_srt(updated_items, zh_srt)
        print(f"  OCR correction: {corrected}/{len(items)} câu được hiệu chỉnh.")


def detect_subtitle_region(video):
    """Find a centered band with changing Chinese text across sampled frames."""
    import cv2

    ocr = _ocr_engine()
    if ocr is None:
        raise RuntimeError("RapidOCR chưa được cài đặt")
    cap = cv2.VideoCapture(str(video))
    bands = {}
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if frame_count <= 0:
            raise ValueError("Không đọc được video")
        for index in sorted({int((frame_count - 1) * fraction / 10) for fraction in range(1, 10)}):
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            if max(height, width) > 960:
                scale = 960 / max(height, width)
                frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
                height, width = frame.shape[:2]
            results, _ = ocr(frame)
            for points, text, confidence in results or []:
                han = ''.join(re.findall(r'[\u3400-\u9fff]', text))
                if len(han) < 2 or float(confidence) < 0.55:
                    continue
                xs, ys = zip(*points)
                x1, x2 = min(xs) / width, max(xs) / width
                y1, y2 = min(ys) / height, max(ys) / height
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                if abs(cx - 0.5) <= 0.2 and 0.38 <= cy <= 0.98 and y2 - y1 <= 0.13:
                    # Overlap bands so a line close to a boundary can still cluster.
                    for band in range(round(cy * 25) - 1, round(cy * 25) + 2):
                        bands.setdefault(band, []).append((index, han, y1, y2, x1, x2))
    finally:
        cap.release()
    candidates = [rows for rows in bands.values()
                  if len({row[0] for row in rows}) >= 2 and len({row[1] for row in rows}) >= 2]
    if not candidates:
        return None
    rows = max(candidates, key=lambda rows: (len({row[0] for row in rows}), sum(row[2] for row in rows) / len(rows)))
    return [max(0, min(row[2] for row in rows) - 0.025),
            min(1, max(row[3] for row in rows) + 0.025),
            max(0, min(0.04, min(row[4] for row in rows) - 0.025)),
            min(1, max(0.96, max(row[5] for row in rows) + 0.025))]
