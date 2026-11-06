"""Sequential Web UI jobs; CLI owns caching, output locking and translation."""
import os
import json
from recognition import parse_ocr_config
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading


def build_jobs(root, body):
    if not isinstance(body, dict):
        raise ValueError('Yêu cầu không hợp lệ')
    videos = body.get('videos', [body.get('video')])
    if not isinstance(videos, list) or not videos or any(not isinstance(v, str) for v in videos):
        raise ValueError('Chọn ít nhất một video MP4')
    videos = list(dict.fromkeys(Path(v).resolve() for v in videos))
    if any(not v.is_file() or v.suffix.lower() != '.mp4' for v in videos):
        raise ValueError('Video MP4 không hợp lệ')
    mode = body.get('mode', 'srt')
    if mode not in ('srt', 'srt_burn', 'burn', 'soft'):
        raise ValueError('Chế độ không hợp lệ')
    output_text = body.get('output') or 'outputs'
    if not isinstance(output_text, str):
        raise ValueError('Thư mục đầu ra không hợp lệ')
    output = (root / (output_text.strip() or 'outputs')).resolve()
    base = Path(os.path.commonpath([str(v.parent) for v in videos]))
    video_crops = body.get('video_crops', {})
    if not isinstance(video_crops, dict):
        raise ValueError('Vùng crop theo video không hợp lệ')
    video_crops = {str(Path(path).resolve()): crop for path, crop in video_crops.items()}
    video_regions = body.get('video_ocr_regions', {})
    if not isinstance(video_regions, dict):
        raise ValueError('Danh sách vùng OCR theo video không hợp lệ')
    video_regions = {str(Path(path).resolve()): parse_ocr_config(config)
                     for path, config in video_regions.items()}
    if body.get('no_ocr') and body.get('ocr_only'):
        raise ValueError('Chỉ chọn một chế độ nhận dạng')
    destinations = [output / video.parent.relative_to(base) / video.stem for video in videos]
    if len({str(path).casefold() for path in destinations}) != len(destinations):
        raise ValueError('Các video trùng đường dẫn đầu ra; hãy đổi tên một video')
    jobs = []
    for video in videos:
        destination = output / video.parent.relative_to(base)
        if destination == video.parent or destination.is_relative_to(video.parent):
            raise ValueError('Thư mục đầu ra phải nằm ngoài thư mục nguồn')
        cmd = [sys.executable, '-u', str(root / 'translate.py'), '--input', str(video),
               '--output', str(destination), '--mode', mode]
        crop = video_crops.get(str(video)) or body.get('crop_box')
        if crop:
            cmd += ['--crop-box', str(crop)]
        config = video_regions.get(str(video))
        if config:
            cmd += ['--ocr-regions', json.dumps(config)]
        if body.get('no_ocr'):
            cmd.append('--no-ocr')
        elif body.get('ocr_only'):
            cmd.append('--ocr-only')
        jobs.append((str(video), cmd))
    return jobs


class RunnerState:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.process = None
        self.status = 'idle'
        self.items = []
        self.cancel = threading.Event()
        self.log_subscribers = []
        self.history_logs = []

    def snapshot(self):
        with self.lock:
            return {'type': 'status', 'status': self.status, 'items': [dict(i) for i in self.items]}

    def subscribe(self):
        subscriber = queue.Queue(maxsize=1000)
        with self.lock:
            for item in self.history_logs[-100:]:
                subscriber.put_nowait(item)
            self.log_subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        with self.lock:
            if subscriber in self.log_subscribers:
                self.log_subscribers.remove(subscriber)

    def broadcast(self, item):
        with self.lock:
            self.history_logs.append(item)
            self.history_logs = self.history_logs[-500:]
            for subscriber in self.log_subscribers[:]:
                try:
                    subscriber.put_nowait(item)
                except queue.Full:
                    self.log_subscribers.remove(subscriber)

    def start(self, body):
        jobs = build_jobs(self.root, body)
        with self.lock:
            if self.status == 'running':
                raise ValueError('Một hàng đợi đang chạy hoặc đang dừng')
            self.cancel.clear()
            self.history_logs.clear()
            self.items = [{'video': video, 'status': 'pending'} for video, _ in jobs]
            self.status = 'running'
            self.broadcast(self.snapshot())
            threading.Thread(target=self._run, args=(jobs,), daemon=True).start()

    def _run(self, jobs):
        env = dict(os.environ, PYTHONUNBUFFERED='1')
        env['PATH'] = '/opt/homebrew/opt/ffmpeg-full/bin:' + env.get('PATH', '')
        try:
            for index, (video, command) in enumerate(jobs):
                with self.lock:
                    if self.cancel.is_set():
                        break
                    self.items[index]['status'] = 'running'
                    self.broadcast(self.snapshot())
                    self.broadcast({'type': 'log', 'text': f'[{index + 1}/{len(jobs)}] {video}', 'level': 'info'})
                    proc = subprocess.Popen(command, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                            errors='replace', bufsize=1, start_new_session=True)
                    self.process = proc
                try:
                    for line in proc.stdout:
                        if line.startswith('VTS_PROGRESS '):
                            try:
                                progress = json.loads(line[len('VTS_PROGRESS '):])
                                with self.lock:
                                    self.items[index]['progress'] = progress
                                    self.broadcast(self.snapshot())
                            except (ValueError, TypeError):
                                pass
                        self.broadcast({'type': 'log', 'text': line.rstrip(), 'level': 'info'})
                    code = proc.wait()
                finally:
                    proc.stdout.close()
                with self.lock:
                    self.process = None
                    self.items[index]['status'] = 'stopped' if self.cancel.is_set() else ('completed' if code == 0 else 'failed')
                    self.broadcast(self.snapshot())
        except Exception as error:
            with self.lock:
                self.items[index]['status'] = 'failed'
            self.broadcast({'type': 'log', 'text': str(error), 'level': 'error'})
        finally:
            with self.lock:
                for item in self.items:
                    if item['status'] == 'pending':
                        item['status'] = 'stopped'
                self.process = None
                self.status = 'stopped' if self.cancel.is_set() else ('failed' if any(i['status'] == 'failed' for i in self.items) else 'completed')
                self.broadcast(self.snapshot())

    def stop(self):
        with self.lock:
            if self.status != 'running':
                return
            self.cancel.set()
            proc = self.process
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
