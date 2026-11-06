"""Short burn previews use the same filters as final export, without changing cache."""
import math
from pathlib import Path
import tempfile
import threading
import uuid

from engine import burn_subtitle_to_video
from media import _get_video_info
from subtitle_editor import validate_srt
from subtitles import write_srt


class VideoPreview:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='vts-preview-')
        self.lock = threading.Lock()

    def render(self, body):
        if not isinstance(body, dict):
            raise ValueError('Yêu cầu không hợp lệ')
        video = Path(body.get('video', '')).resolve()
        if not video.is_file() or video.suffix.lower() != '.mp4':
            raise ValueError('Chọn video MP4 hợp lệ')
        start = float(body.get('time', 0))
        if not math.isfinite(start) or start < 0:
            raise ValueError('Thời điểm preview không hợp lệ')
        _, duration, _, _ = _get_video_info(video)
        if start >= duration:
            raise ValueError('Hãy tua tới trước cuối video')
        clip_duration = min(5, duration-start)
        text = body.get('text', '')
        if not isinstance(text, str):
            raise ValueError('Phụ đề không hợp lệ')
        items = validate_srt(text) if text.strip() else None
        if not self.lock.acquire(blocking=False):
            raise ValueError('Một preview đang được tạo; hãy đợi hoàn tất')
        try:
            root = Path(self.directory.name)
            # Keep a few recent clips for playback, bounded for long UI sessions.
            for old in sorted(root.glob('*.mp4'), key=lambda p: p.stat().st_mtime)[:-6]:
                old.unlink(missing_ok=True)
            token = uuid.uuid4().hex
            output = root / (token + '.mp4')
            with tempfile.TemporaryDirectory(dir=root) as work:
                subtitle = Path(work) / 'preview.srt' if items else None
                if items:
                    write_srt(items, subtitle)
                try:
                    burn_subtitle_to_video(video, subtitle, output,
                                           preview_start=start, preview_duration=clip_duration)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
            return {'path': str(output), 'start': start, 'duration': clip_duration}
        finally:
            self.lock.release()
