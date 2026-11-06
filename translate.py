"""Batch Chinese → Vietnamese subtitles for MP4 videos."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from media import report_progress
from subtitles import has_source_subtitles

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / 'videos'


def discover(source):
    if source.is_file():
        return [source] if source.suffix.lower() == '.mp4' else []
    return sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.mp4'
                  and p.resolve().is_relative_to(source.resolve())
                  and not p.name.lower().endswith(('.vi.burned.mp4', '.vi.softsub.mp4')))


def fingerprint(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def process(video, destination, args):
    # Work in the destination: interrupted engine output never becomes a completed result.
    destination.mkdir(parents=True, exist_ok=True)
    marker = destination / 'source.json'
    identity = {'sha256': fingerprint(video), 'model': os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'),
                'fallback_models': os.getenv('GEMINI_FALLBACK_MODELS', 'gemini-3.5-flash-lite'),
                'whisper': os.getenv('WHISPER_MODEL', 'base'), 'ocr': not args.no_ocr,
                'crop_box': getattr(args, 'crop_box', None),
                'ocr_regions': getattr(args, 'ocr_regions', None)}
    if getattr(args, 'ocr_only', False):
        identity['whisper'] = None
    previous = json.loads(marker.read_text()) if marker.exists() else None
    cache = destination / 'outputs'
    source_changed = previous is not None and previous.get('sha256') != identity['sha256']
    if source_changed:
        # Clear completion flags before deleting or rebuilding cached artifacts.
        for flag in destination.glob('*.done'):
            flag.unlink()
        if cache.exists():
            for artifact in cache.iterdir():
                if artifact.is_file():
                    artifact.unlink()
    if previous and previous.get('remove_watermark', False):
        (destination / 'burn.done').unlink(missing_ok=True)
    if previous != identity:
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(identity))
        temporary.replace(marker)
    vi_srt = cache / (video.stem + '.vi.srt')
    zh_srt = cache / (video.stem + '.zh.srt')
    suffix = {'srt': '.vi.srt', 'soft': '.vi.softsub.mp4', 'burn': '.vi.burned.mp4', 'srt_burn': '.vi.burned.mp4'}[args.mode]
    result = cache / (video.stem + suffix)
    complete = destination / ('burn.done' if args.mode in ('burn', 'srt_burn') else args.mode + '.done')
    if complete.exists() and result.exists() and result.stat().st_size:
        if has_source_subtitles(cache / (video.stem + '.zh.srt')):
            (destination / 'zh.done').touch()
            report_progress("Nhận dạng", 100, " (dùng lại zh.srt, zh.done)")
        else:
            (destination / 'zh.done').unlink(missing_ok=True)
            print("[Nhận dạng] Bỏ qua theo kết quả đã lưu.", flush=True)
        if vi_srt.is_file() and vi_srt.stat().st_size:
            (destination / 'srt.done').touch()
            report_progress("Dịch", 100, " (dùng lại vi.srt, srt.done)")
        else:
            print("[Dịch] Bỏ qua theo kết quả đã lưu.", flush=True)
        if args.mode == 'srt':
            print("[Xuất video] Bỏ qua theo chế độ SRT.", flush=True)
        else:
            report_progress("Xuất video", 100, f" (dùng lại {result.name}, {complete.name})")
        print(f'Đã hoàn tất: {video}', flush=True)
        return
    complete.unlink(missing_ok=True)
    command = [sys.executable, str(ROOT / 'engine.py'), '--input', str(video), '--mode', args.mode]
    if args.no_ocr:
        command.append('--no-ocr')
    elif getattr(args, 'ocr_only', False):
        command.append('--ocr-only')
    if getattr(args, 'crop_box', None):
        command.extend(['--crop-box', args.crop_box])
    if getattr(args, 'ocr_regions', None):
        command.extend(['--ocr-regions', args.ocr_regions])
    subprocess.run(command, cwd=destination, check=True)
    if vi_srt.exists() and vi_srt.stat().st_size > 0:
        (destination / 'srt.done').touch()
    if not result.exists() or not result.stat().st_size:
        raise RuntimeError(f'Không tạo được đầu ra: {result}')
    complete.touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs')
    parser.add_argument('--mode', choices=['srt', 'soft', 'burn', 'srt_burn'], default='srt')
    recognition_mode = parser.add_mutually_exclusive_group()
    recognition_mode.add_argument('--no-ocr', action='store_true')
    recognition_mode.add_argument('--ocr-only', action='store_true')
    parser.add_argument('--crop-box', type=str, default=None, help='Vùng crop OCR: ymin,ymax,xmin,xmax (tỉ lệ 0.0-1.0 hoặc pixel)')
    parser.add_argument('--ocr-regions', default=None, help='JSON các vùng OCR')
    parser.add_argument('--list', action='store_true', help='Liệt kê video, không tải model/gọi API')
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    if not source.exists():
        parser.error(f'Không tìm thấy: {source}')
    base = source.parent if source.is_file() else source
    if output == base or output.is_relative_to(base):
        parser.error('--output phải nằm ngoài thư mục nguồn để tránh dịch lại video đầu ra')
    videos = discover(source)
    if not videos:
        parser.error('Không tìm thấy video MP4')
    destinations = [video.relative_to(base).with_suffix('') for video in videos]
    if len({str(path).casefold() for path in destinations}) != len(destinations):
        parser.error('Các video trùng đường dẫn đầu ra; hãy đổi tên một video')
    if args.list:
        for video in videos:
            print(video.relative_to(base))
        print(f'{len(videos)} video')
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    import fcntl
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.translate.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Một tiến trình khác đang dùng thư mục đầu ra')
        failures = 0
        for video in videos:
            relative = video.relative_to(base)
            try:
                print(f'\nĐang xử lý: {relative}', flush=True)
                process(video, output / relative.with_suffix(''), args)
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
                failures += 1
                print(f'Lỗi {relative}: {error}', file=sys.stderr)
        return int(bool(failures))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nĐã dừng. Chạy lại để tiếp tục.', file=sys.stderr)
        sys.exit(130)
