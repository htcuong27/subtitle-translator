#!/usr/bin/env python3
"""
Web UI cho Video Translate Super.
Cung cấp giao diện trực quan chọn vùng phụ đề (crop box) để OCR và điều khiển translate.py.
"""
import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import queue
import shlex
import re
import socket
import subprocess
import threading
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
import webbrowser

import cv2
from dotenv import load_dotenv
from ai_settings import profile, public_settings, read_settings, requested_settings, save_settings
from media import _get_video_info
from recognition import _ocr_engine, clean_ocr_subtitle, detect_subtitle_region, parse_crop_box, parse_ocr_config, prepare_ocr_frame, read_white_horizontal_text
from video_preview import VideoPreview
from web_runner import RunnerState
from translate import DEFAULT_INPUT, fingerprint
from subtitle_editor import edit_subtitles
from gemini import probe_connection, translate_batch

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

load_dotenv(ROOT / ".env")
runner = RunnerState(ROOT)
video_preview = VideoPreview()


def discover_videos(search_dirs):
    results = []
    seen = set()
    for directory in search_dirs:
        dir_path = Path(directory).resolve()
        if not dir_path.exists():
            continue
        for entry in sorted(dir_path.rglob("*")):
            if entry.suffix.lower() != ".mp4":
                continue
            p = entry.resolve()
            if not p.is_relative_to(dir_path) or p.is_relative_to(ROOT / "models"):
                continue
            if (p.is_file() and p not in seen and not p.name.startswith(".")
                    and not p.name.lower().endswith((".vi.burned.mp4", ".vi.softsub.mp4"))):
                seen.add(p)
                size_mb = p.stat().st_size / (1024 * 1024)
                results.append({
                    "path": str(p),
                    "rel_path": str(p.relative_to(dir_path)),
                    "size": f"{size_mb:.1f} MB",
                })
    return results


def mark_subtitled(videos, output):
    if not videos:
        return
    hashes = {}
    for directory in {ROOT / "outputs", output}:
        for srt in directory.rglob("*.vi.srt"):
            destination = srt.parent.parent
            try:
                if (srt.parent.name == "outputs" and (destination / "srt.done").is_file()
                        and srt.is_file() and srt.stat().st_size):
                    source_hash = json.loads((destination / "source.json").read_text())["sha256"]
                    hashes.setdefault(srt.name[:-len(".vi.srt")], set()).add(source_hash)
            except (OSError, ValueError, TypeError, KeyError):
                continue
    for video in videos:
        path = Path(video["path"])
        candidates = hashes.get(path.stem)
        try:
            video["subtitled"] = bool(candidates and fingerprint(path) in candidates)
        except OSError:
            video["subtitled"] = False


def choose_local_folder():
    script = """
try
    return POSIX path of (choose folder with prompt "Chọn thư mục video")
on error number -128
    return ""
end try
"""
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Không mở được hộp chọn thư mục trên macOS')
    value = result.stdout.rstrip('\n')
    return str(Path(value).resolve()) if value else ""


def update_preview_script(video_path: str, output_path: str) -> None:
    """Cập nhật preview.sh với input và output của video hiện tại."""
    preview_file = ROOT / "preview.sh"
    try:
        vid_p = Path(video_path).resolve()
        try:
            rel_vid = os.path.relpath(vid_p, ROOT)
        except ValueError:
            rel_vid = str(vid_p)

        out_p = Path(output_path).resolve()
        try:
            rel_out = os.path.relpath(out_p, ROOT)
        except ValueError:
            rel_out = str(out_p)

        content = f"""#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec ./translate.sh \\
  --input {shlex.quote(rel_vid)} \\
  --output {shlex.quote(rel_out)} \\
  --mode burn "$@"
"""
        preview_file.write_text(content, encoding="utf-8")
        preview_file.chmod(0o755)
    except Exception as e:
        print(f"Lỗi khi cập nhật preview.sh: {e}")


class AppRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def end_headers(self):
        if urllib.parse.urlparse(self.path).path.endswith(('.html', '.js', '.css')):
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def local_request(self):
        host = self.headers.get("Host")
        return (host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
                and self.headers.get("Origin") in (None, f"http://{host}"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/") and not self.local_request():
            self.send_error(403)
            return

        if path == "/":
            self.path = "/index.html"
            return super().do_GET()

        if path == "/api/videos":
            self.handle_get_videos(query)
            return

        if path == "/api/video_info":
            self.handle_get_video_info(query)
            return

        if path == "/api/frame":
            self.handle_get_frame(query)
            return

        if path == "/api/stream":
            try:
                self.handle_stream_video(query)
            except (BrokenPipeError, ConnectionResetError):
                # Browsers cancel video requests when seeking or changing sources.
                self.close_connection = True
            return

        if path == "/api/logs":
            self.handle_sse_logs()
            return

        if path == "/api/ai_settings":
            self.send_json(public_settings(read_settings()))
            return

        if path == "/api/status":
            self.send_json(runner.snapshot())
            return

        # Serve static assets
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self.local_request():
            self.send_error(403)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            body = {}

        if path in ("/api/ai_settings", "/api/ai_settings/test"):
            if self.headers.get_content_type() != "application/json":
                self.send_json({"error": "Yêu cầu phải dùng JSON."}, status=415)
                return
            try:
                if path.endswith("/test"):
                    settings, provider, fields = requested_settings(body)
                    settings["profiles"][provider] = fields
                    probe_connection(provider, profile(settings, provider))
                    self.send_json({"message": "Kết nối thành công. API đã trả lời từ model đã chọn."})
                else:
                    with runner.lock:
                        if runner.status == "running":
                            raise ValueError("Hãy đợi hàng đợi hoàn tất trước khi đổi cấu hình AI.")
                        settings = save_settings(body)
                    self.send_json(settings)
            except ValueError as error:
                self.send_json({"error": str(error)}, status=400)
            except RuntimeError as error:
                self.send_json({"error": str(error)}, status=502)
            except OSError:
                self.send_json({"error": "Không thể đọc hoặc lưu cấu hình AI trên máy."}, status=500)
            return

        if path == "/api/choose_folder":
            if self.headers.get_content_type() != 'application/json':
                self.send_error(415, "Expected application/json")
                return
            try:
                self.send_json({"folder": choose_local_folder()})
            except (OSError, RuntimeError) as error:
                self.send_json({"error": str(error)}, status=400)
            return

        if path == "/api/detect_ocr":
            try:
                video = self.source_video(body.get("video"), body.get("folder"))
                self.send_json({"region": detect_subtitle_region(video)})
            except Exception as error:
                self.send_json({"error": str(error)}, status=400)
            return

        if path == "/api/preview_burn":
            if self.headers.get_content_type() != 'application/json':
                self.send_error(415, "Expected application/json")
                return
            try:
                self.send_json(video_preview.render(body))
            except Exception as error:
                self.send_json({'error': str(error)}, status=400)
            return

        if path in ("/api/subtitles/load", "/api/subtitles/save", "/api/translate_text"):
            if self.headers.get_content_type() != 'application/json':
                self.send_error(415, "Expected application/json")
                return
            try:
                if not isinstance(body, dict):
                    raise ValueError('Yêu cầu không hợp lệ')
                if path == "/api/translate_text":
                    text = body.get('text')
                    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                        raise ValueError('Nhập văn bản cần dịch (tối đa 10000 ký tự)')
                    result = {'text': translate_batch([{'index': '1', 'text': text}])[0]}
                else:
                    with runner.lock:
                        if runner.status == 'running':
                            raise ValueError('Hãy đợi hàng đợi hoàn tất trước khi sửa phụ đề')
                        result = edit_subtitles(ROOT, body, save=path.endswith('/save'))
                self.send_json(result)
            except Exception as error:
                self.send_json({'error': str(error)}, status=400)
            return

        if path == "/api/test_ocr":
            self.handle_test_ocr(body)
            return

        if path == "/api/start_translate":
            self.handle_start_translate(body)
            return

        if path == "/api/stop_translate":
            self.handle_stop_translate()
            return

        if path == "/api/reset_process_status":
            self.handle_reset_process_status()
            return

        if path == "/api/sync_preview":
            self.handle_sync_preview(body)
            return

        self.send_error(404, "Not Found")

    # --- Handlers ---
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def source_video(value, folder):
        if not isinstance(value, str) or not isinstance(folder, str) or not folder:
            raise ValueError("Hãy chọn thư mục video")
        path = Path(value).resolve()
        directory = Path(folder).resolve()
        if (not path.is_relative_to(directory) or path.is_relative_to(ROOT / "models")
                or not path.is_file() or path.suffix.lower() != ".mp4"
                or path.name.startswith(".")
                or path.name.lower().endswith((".vi.burned.mp4", ".vi.softsub.mp4"))):
            raise ValueError("Video phải nằm trong thư mục đã chọn")
        return path

    def handle_get_videos(self, query):
        folder = Path(query.get("folder", [""])[0] or DEFAULT_INPUT).resolve()
        output = (ROOT / query.get("output", ["outputs"])[0]).resolve()
        videos = discover_videos([folder])
        mark_subtitled(videos, output)
        self.send_json({"folder": str(folder), "videos": videos})

    def handle_get_video_info(self, query):
        video_path = query.get("path", [None])[0]
        if not video_path or not os.path.exists(video_path):
            self.send_json({"error": "Video không tồn tại"}, status=404)
            return
        try:
            fps, duration, width, height = _get_video_info(Path(video_path))
            self.send_json({
                "fps": round(fps, 2),
                "duration": round(duration, 2),
                "width": width,
                "height": height,
            })
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def handle_get_frame(self, query):
        video_path = query.get("path", [None])[0]
        t_sec = float(query.get("time", [0])[0])

        if not video_path or not os.path.exists(video_path):
            self.send_error(404)
            return

        try:
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t_sec * fps))
            ret, frame = cap.read()
            cap.release()

            if not ret or frame is None:
                self.send_error(500)
                return

            _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            body = jpeg.tobytes()

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(500)

    def handle_stream_video(self, query):
        video_path_str = query.get("path", [None])[0]
        if not video_path_str or not os.path.exists(video_path_str):
            self.send_error(404)
            return

        file_path = Path(video_path_str).resolve()
        if not file_path.is_file() or file_path.suffix.lower() != ".mp4":
            self.send_error(404)
            return
        file_size = file_path.stat().st_size
        range_header = self.headers.get("Range", None)

        content_type = mimetypes.guess_type(str(file_path))[0] or "video/mp4"

        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                with file_path.open("rb") as f:
                    f.seek(start)
                    chunk_size = 64 * 1024
                    remaining = length
                    while remaining > 0:
                        read_len = min(chunk_size, remaining)
                        data = f.read(read_len)
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
                return

        # Full video request
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with file_path.open("rb") as f:
            while chunk := f.read(64 * 1024):
                self.wfile.write(chunk)

    def handle_test_ocr(self, body):
        video_path = body.get("video")
        t_sec = float(body.get("time", 0.0))
        crop_arr = body.get("crop", [0.78, 0.96, 0.12, 0.88])

        if not video_path or not os.path.exists(video_path):
            self.send_json({"error": "Video không tồn tại"}, status=404)
            return

        try:
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t_sec * fps))
            ret, frame = cap.read()
            cap.release()

            if not ret or frame is None:
                self.send_json({"error": "Không đọc được khung hình video"}, status=500)
                return

            h, w = frame.shape[:2]
            crop_str = f"{crop_arr[0]},{crop_arr[1]},{crop_arr[2]},{crop_arr[3]}"
            y1, y2, x1, x2 = parse_crop_box(crop_str, w, h)

            config = parse_ocr_config(body['ocr_regions']) if body.get('ocr_regions') else None
            if config:
                frame = prepare_ocr_frame(frame, config)
                y1, y2, x1, x2 = 0, h, 0, w
            cropped = frame[y1:y2, x1:x2]
            _, thumb_jpg = cv2.imencode(".jpg", cropped, [cv2.IMWRITE_JPEG_QUALITY, 90])
            b64_thumb = base64.b64encode(thumb_jpg).decode("utf-8")

            ocr = _ocr_engine()
            if ocr is None:
                raise RuntimeError("Cần RapidOCR để thử nhận dạng")
            if body.get("ocr_only"):
                lines = read_white_horizontal_text(cropped, ocr)
                separator = "\n"
            else:
                results, _ = ocr(cropped)
                lines = [(text.strip(), float(conf)) for _, text, conf in results or []
                         if text.strip() and float(conf) > 0.35]
                separator = ""
            details = []
            for text, conf in lines:
                cleaned = clean_ocr_subtitle(text)
                if cleaned:
                    details.append({"text": cleaned, "conf": round(conf, 2)})
            ocr_text = separator.join(item["text"] for item in details)

            self.send_json({
                "ok": True,
                "text": ocr_text,
                "details": details,
                "crop_coords": [y1, y2, x1, x2],
                "crop_image": b64_thumb,
            })
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def handle_start_translate(self, body):
        try:
            if not isinstance(body, dict):
                raise ValueError("Yêu cầu không hợp lệ")
            values = body.get("videos", [body.get("video")])
            if not isinstance(values, list):
                raise ValueError("Danh sách video không hợp lệ")
            videos = [{"path": str(self.source_video(value, body.get("folder")))} for value in values]
            output = body.get("output") or "outputs"
            if not isinstance(output, str):
                raise ValueError("Thư mục đầu ra không hợp lệ")
            with runner.lock:
                if runner.status == "running":
                    raise ValueError("Một hàng đợi đang chạy hoặc đang dừng")
                if body.get("mode", "srt") == "srt":
                    mark_subtitled(videos, (ROOT / (output.strip() or "outputs")).resolve())
                    if any(video["subtitled"] for video in videos):
                        raise ValueError("Danh sách có video đã có phụ đề Việt. Hãy làm mới danh sách.")
                runner.start(body)
            self.send_json({"ok": True})
        except (ValueError, OSError) as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_stop_translate(self):
        runner.stop()
        self.send_json({"ok": True})

    def handle_reset_process_status(self):
        runner.stop()
        self.send_json({"ok": True})

    def handle_sync_preview(self, body):
        video = body.get("video")
        output = body.get("output", "outputs")
        if video and os.path.exists(video):
            update_preview_script(video, output)
            self.send_json({"ok": True, "message": "Đã cập nhật preview.sh thành công"})
        else:
            self.send_json({"error": "Video không hợp lệ"}, status=400)

    def handle_sse_logs(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = runner.subscribe()
        try:
            # First send current status
            self.wfile.write(f"data: {json.dumps(runner.snapshot())}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    item = q.get(timeout=20.0)
                    line = f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode("utf-8")
                    self.wfile.write(line)
                    self.wfile.flush()
                except queue.Empty:
                    # Keep-alive heartbeat
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            runner.unsubscribe(q)


def find_free_port(start_port=7860, max_attempts=20):
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start_port


def main():
    parser = argparse.ArgumentParser(description="Web UI cho Video Translate Super")
    parser.add_argument("--port", type=int, default=0, help="Cổng chạy server (mặc định: tự tìm cổng từ 7860)")
    parser.add_argument("--no-browser", action="store_true", help="Không tự động mở trình duyệt")
    args = parser.parse_args()

    port = args.port if args.port > 0 else find_free_port()
    server_address = ("127.0.0.1", port)

    from socketserver import ThreadingMixIn
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    httpd = ThreadedHTTPServer(server_address, AppRequestHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n=======================================================")
    print(f"  🎬 Video Translate Super - OCR Region Selector UI")
    print(f"  Truy cập: {url}")
    print(f"  Nhấn Ctrl+C để dừng server.")
    print(f"=======================================================\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang tắt server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
