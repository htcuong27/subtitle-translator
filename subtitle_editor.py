"""Validated, locked edits of the same subtitle cache used by the queue."""
import fcntl
import json
import re
from pathlib import Path

from subtitles import parse_srt, write_srt
from translate import fingerprint
from web_runner import build_jobs


def validate_srt(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Nhập phụ đề SRT trước khi lưu')
    items = parse_srt(text)
    blocks = re.split(r'\n\s*\n', text.strip())
    if len(items) != len(blocks):
        raise ValueError('Mỗi câu cần số thứ tự, thời gian và nội dung')
    for index, item in enumerate(items, 1):
        match = re.fullmatch(r'(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> (\d{2,}):([0-5]\d):([0-5]\d),(\d{3})', item['timestamp'])
        if not match:
            raise ValueError(f'Thời gian câu {index} không đúng định dạng SRT')
        values = list(map(int, match.groups()))
        def milliseconds(parts):
            h, m, s, ms = parts
            return ((h * 60 + m) * 60 + s) * 1000 + ms
        if milliseconds(values[:4]) >= milliseconds(values[4:]):
            raise ValueError(f'Câu {index}: kết thúc phải sau bắt đầu')
        item['index'] = str(index)
    return items


def edit_subtitles(root, body, save=False):
    jobs = build_jobs(root, {**body, 'mode': 'srt'})
    video = str(Path(body.get('video', '')).resolve())
    command = next((cmd for path, cmd in jobs if path == video), None)
    if command is None:
        raise ValueError('Video đang xem phải nằm trong nhóm đã chọn')
    output = Path(command[command.index('--output') + 1])
    destination = output / Path(video).stem
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.translate.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Hàng đợi đang dùng thư mục này; hãy đợi hoàn tất')
        marker = destination / 'source.json'
        identity = json.loads(marker.read_text()) if marker.exists() else None
        if identity and identity.get('sha256') != fingerprint(Path(video)):
            raise ValueError('Video nguồn đã thay đổi; hãy tạo lại SRT trước')
        cache = destination / 'outputs'
        path = cache / (Path(video).stem + '.vi.srt')
        if save:
            items = validate_srt(body.get('text'))
            cache.mkdir(parents=True, exist_ok=True)
            if identity is None:
                marker.write_text(json.dumps({'sha256': fingerprint(Path(video))}))
            temporary = path.with_suffix('.srt.tmp')
            write_srt(items, temporary)
            for name in ('burn.done', 'soft.done'):
                (destination / name).unlink(missing_ok=True)
            temporary.replace(path)
            (destination / 'srt.done').touch()
        loaded_path = path
        if not save and (not path.exists() or not path.stat().st_size):
            # Grouping the queue changes cache paths. Only reuse a transcript when
            # its recorded source fingerprint matches, never by episode name alone.
            source_hash = fingerprint(Path(video))
            candidates = set()
            for search_root in {root / 'outputs', output}:
                if not search_root.is_dir():
                    continue
                for candidate in search_root.rglob(Path(video).stem + '.vi.srt'):
                    candidate_marker = candidate.parent.parent / 'source.json'
                    try:
                        if (candidate.stat().st_size and candidate_marker.is_file() and
                                json.loads(candidate_marker.read_text()).get('sha256') == source_hash):
                            candidates.add(candidate)
                    except (OSError, ValueError):
                        continue
            if candidates:
                loaded_path = max(candidates, key=lambda p: p.stat().st_mtime_ns)
        source = loaded_path.with_name(Path(video).stem + '.zh.srt')
        return {'text': loaded_path.read_text(encoding='utf-8') if loaded_path.exists() else '',
                'loaded_path': str(loaded_path),
                'source': source.read_text(encoding='utf-8') if source.exists() else '', 'path': str(path)}
