import argparse
import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from gemini import episode_batches, translate_batch, translation_profile
from media import _get_video_info, probe_subtitle_streams, report_progress, run, run_ffmpeg_export
from recognition import (
    _run_ocr_correction_pass,
    parse_crop_box,
    parse_ocr_config,
    transcribe_video_audio,
    transcribe_video_hybrid,
    transcribe_video_ocr,
)
from subtitles import (
    has_source_subtitles, parse_srt, write_srt,
)

load_dotenv(Path(__file__).with_name(".env"))


def check_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Không tìm thấy {name}. Hãy cài {name} trước.")


def translate_srt(input_srt: Path, output_srt: Path, target_language: str = "Vietnamese") -> None:
    srt_text = input_srt.read_text(encoding="utf-8", errors="ignore")
    items = parse_srt(srt_text)
    if not items:
        raise ValueError("Không có câu phụ đề để dịch.")
    translated_items = []
    total = len(items)
    report_progress("Dịch", 0, f" (0/{total} câu)")
    connection = translation_profile()
    for batch_no, (start, end) in enumerate(episode_batches(items), start=1):
        batch = items[start:end]
        options = {}
        if start or end < total:
            options["context"] = {
                "previous": [
                    {"source": source["text"], "translation": translated["text"]}
                    for source, translated in zip(items[max(0, start - 12):start], translated_items[-12:])
                ],
                "next": [item["text"] for item in items[end:end + 8]],
            }
        print(f"Đang dịch đoạn {batch_no} ({len(batch)} câu)...")
        translations = translate_batch(batch, target_language=target_language, connection=connection, **options)
        for item, translated_text in zip(batch, translations):
            translated_items.append({
                "index": item["index"],
                "timestamp": item["timestamp"],
                "text": translated_text,
            })
        if end < total:
            report_progress("Dịch", 100 * end // total, f" ({end}/{total} câu)")
    temporary = output_srt.with_suffix(".srt.tmp")
    write_srt(translated_items, temporary)
    temporary.replace(output_srt)
    report_progress("Dịch", 100, f" ({total}/{total} câu)")
    print(f"Đã dịch thành công {total} dòng phụ đề -> {output_srt}")


def copy_srt_timing(source_srt: Path, translated_srt: Path) -> None:
    source_items = parse_srt(source_srt.read_text(encoding="utf-8", errors="ignore"))
    translated_items = parse_srt(translated_srt.read_text(encoding="utf-8", errors="ignore"))
    if len(source_items) != len(translated_items):
        raise RuntimeError(
            "Không thể chỉ cập nhật timing vì số câu đã thay đổi "
            f"({len(source_items)} nguồn, {len(translated_items)} bản dịch)."
        )
    for source, translated in zip(source_items, translated_items):
        translated["timestamp"] = source["timestamp"]
    write_srt(translated_items, translated_srt)


def burn_subtitle_to_video(input_video: Path, input_srt: Path, output_video: Path, preview_start=None, preview_duration=5, *, show_progress=False) -> None:
    """Burn Vietnamese subtitles into video (hardsub). Uses temp file for ASCII path safety."""
    import tempfile

    video_dir = input_video.parent
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False, dir=video_dir, prefix="sub_tmp_") as tmp:
        tmp_srt = Path(tmp.name)
    try:
        if input_srt is not None:
            shutil.copy2(input_srt, tmp_srt)
        srt_filename = tmp_srt.name
        video_filename = input_video.name
        output_abs = str(output_video.resolve())
        filters = []
        if input_srt is not None:
            if preview_start is not None:
                filters.append(f'setpts=PTS+{preview_start}/TB')
            filters.append(f"subtitles=filename={srt_filename}")
            if preview_start is not None:
                filters.append('setpts=PTS-STARTPTS')
        subtitle_filter = ','.join(filters)
        cmd = [
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
            *(['-ss', str(preview_start)] if preview_start is not None else []),
            "-i", video_filename,
            *(['-t', str(preview_duration)] if preview_start is not None else []),
            *(['-vf', subtitle_filter] if subtitle_filter else []),
            "-c:v", "libx264", "-preset", "fast",
            *(['-c:a', 'aac', '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
              if preview_start is not None else ['-c:a', 'copy']), output_abs,
        ]
        if show_progress and preview_start is None:
            run_ffmpeg_export(cmd, input_video, cwd=video_dir)
        else:
            subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=str(video_dir), **({"timeout": 120} if preview_start is not None else {}))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg burn subtitle thất bại:\nSTDERR:\n{e.stderr[-2000:]}")
    finally:
        tmp_srt.unlink(missing_ok=True)


def add_soft_subtitle_to_video(input_video: Path, input_srt: Path, output_video: Path) -> None:
    """Add Vietnamese subtitle as a selectable (soft) track."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
        "-i", str(input_video),
        "-i", str(input_srt),
        "-map", "0", "-map", "1",
        "-c", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=vie",
        str(output_video),
    ]
    run_ffmpeg_export(cmd, input_video)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input video path")
    parser.add_argument("--ocr-regions", default=None, help="JSON các vùng OCR")
    parser.add_argument("--subtitle-index", type=int, default=0, help="Subtitle stream index, default 0")
    parser.add_argument("--target", default="Vietnamese", help="Target language")
    parser.add_argument("--mode", choices=["srt", "soft", "burn", "srt_burn"], default="srt")
    parser.add_argument(
        "--crop-box", default=None,
        help="Vùng crop OCR: ymin,ymax,xmin,xmax (tỉ lệ 0.0-1.0 hoặc pixel)",
    )
    parser.add_argument(
        "--refresh-timing", action="store_true",
        help="Re-run Whisper+OCR for updated timing, preserve existing translation if line count matches",
    )
    recognition_mode = parser.add_mutually_exclusive_group()
    recognition_mode.add_argument("--no-ocr", action="store_true", help="Chỉ dùng Whisper")
    recognition_mode.add_argument("--ocr-only", action="store_true", help="Chỉ dùng OCR đọc chữ trên video")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {input_path}")

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    stem = input_path.stem

    # Support direct SRT input
    if input_path.suffix.lower() == ".srt":
        print(f"Nhận diện file đầu vào là file phụ đề SRT: {input_path}")
        vi_srt = output_dir / f"{stem}.vi.srt"
        if vi_srt.exists() and vi_srt.stat().st_size > 0:
            report_progress("Dịch", 100, f" (dùng lại {vi_srt.name})")
        else:
            print("Translating to Vietnamese...")
            translate_srt(input_path, vi_srt, target_language=args.target)
        print(f"Hoàn thành. File phụ đề tiếng Việt: {vi_srt}")
        return

    check_binary("ffmpeg")
    check_binary("ffprobe")

    input_video = input_path
    stem = input_video.stem
    zh_srt = output_dir / f"{stem}.zh.srt"
    vi_srt = output_dir / f"{stem}.vi.srt"
    soft_video = output_dir / f"{stem}.vi.softsub.mp4"
    burn_video = output_dir / f"{stem}.vi.burned.mp4"

    ocr_config = parse_ocr_config(args.ocr_regions) if args.ocr_regions else None
    crop_coords = None
    if args.crop_box:
        _, _, w, h = _get_video_info(input_video)
        crop_coords = parse_crop_box(args.crop_box, w, h)
        print(f">> Sử dụng vùng crop tùy chỉnh: y1={crop_coords[0]}, y2={crop_coords[1]}, x1={crop_coords[2]}, x2={crop_coords[3]}")

    has_translation = vi_srt.is_file() and vi_srt.stat().st_size > 0
    translation_marker = Path("srt.done")
    if not has_translation:
        translation_marker.unlink(missing_ok=True)
    has_source = has_source_subtitles(zh_srt)
    recognition_marker = Path("zh.done")
    if not has_source:
        recognition_marker.unlink(missing_ok=True)
    if has_source and not args.refresh_timing:
        recognition_marker.touch()
        report_progress("Nhận dạng", 100, f" (dùng lại {zh_srt.name}, zh.done)")
    elif args.refresh_timing or not has_translation:
        report_progress("Nhận dạng", 0)
        pending = zh_srt.with_suffix(".pending.srt")
        options = {"crop_box": crop_coords}
        if ocr_config:
            options["ocr_config"] = ocr_config
        try:
            if args.ocr_only:
                transcribe_video_ocr(input_video, pending, **options)
            elif probe_subtitle_streams(input_video):
                run([
                    "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
                    "-i", str(input_video), "-map", f"0:s:{args.subtitle_index}",
                    "-c:s", "srt", str(pending),
                ])
                if not args.no_ocr:
                    _run_ocr_correction_pass(input_video, pending, **options)
            elif args.no_ocr:
                transcribe_video_audio(input_video, pending, language="zh")
            else:
                transcribe_video_hybrid(input_video, pending, language="zh", **options)
            if not has_source_subtitles(pending):
                raise RuntimeError("Không nhận dạng được phụ đề tiếng Trung hợp lệ.")
            if has_translation:
                copy_srt_timing(pending, vi_srt)
            pending.replace(zh_srt)
        finally:
            pending.unlink(missing_ok=True)
        recognition_marker.touch()
        report_progress("Nhận dạng", 100, " (zh.done)")
    else:
        print("[Nhận dạng] Bỏ qua vì đã có bản dịch.", flush=True)

    if has_translation:
        report_progress("Dịch", 100, f" (dùng lại {vi_srt.name})")
    else:
        translate_srt(zh_srt, vi_srt, target_language=args.target)
    translation_marker.touch()
    print(">> Đã lưu dấu hoàn tất bản dịch (srt.done).", flush=True)

    if args.mode == "srt":
        print("[Xuất video] Bỏ qua theo chế độ SRT.", flush=True)
        return

    marker = Path("soft.done" if args.mode == "soft" else "burn.done")
    marker.unlink(missing_ok=True)
    report_progress("Xuất video", 0)
    if args.mode == "soft":
        output = soft_video
        add_soft_subtitle_to_video(input_video, vi_srt, output)
    else:
        output = burn_video
        burn_subtitle_to_video(input_video, vi_srt, output, show_progress=True)
    if not output.is_file() or not output.stat().st_size:
        raise RuntimeError(f"Không tạo được đầu ra: {output}")
    marker.touch()
    report_progress("Xuất video", 100, f" ({marker.name}, {output})")


if __name__ == "__main__":
    main()
