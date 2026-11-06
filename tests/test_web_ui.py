import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from urllib.parse import quote

from web_ui import AppRequestHandler, choose_local_folder, discover_videos, mark_subtitled
from translate import fingerprint


class LocalFolderTests(unittest.TestCase):
    def test_folder_picker_path_and_cancel(self):
        with TemporaryDirectory(prefix='folder with spaces ') as directory:
            with patch('web_ui.subprocess.run', return_value=Mock(returncode=0, stdout=directory + '/\n')):
                self.assertEqual(choose_local_folder(), str(Path(directory).resolve()))
            with patch('web_ui.subprocess.run', return_value=Mock(returncode=0, stdout='\n')):
                self.assertEqual(choose_local_folder(), '')
        with patch('web_ui.subprocess.run', return_value=Mock(returncode=1)):
            with self.assertRaises(RuntimeError):
                choose_local_folder()

    def test_selected_folder_and_completed_source_fingerprint(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'sources'
            output = root / 'output'
            for name in ('a', 'b'):
                episode = source / name / 'ep.MP4'
                episode.parent.mkdir(parents=True)
                episode.write_bytes(name.encode())
            outside = root / 'outside.mp4'
            outside.touch()
            (source / 'link.mp4').symlink_to(outside)
            (source / 'ep.vi.burned.mp4').touch()
            videos = discover_videos([source])
            self.assertEqual([video['rel_path'] for video in videos], ['a/ep.MP4', 'b/ep.MP4'])
            with self.assertRaises(ValueError):
                AppRequestHandler.source_video(str(source / 'link.mp4'), str(source))

            cache = output / 'previous' / 'ep'
            (cache / 'outputs').mkdir(parents=True)
            (cache / 'source.json').write_text(json.dumps({'sha256': fingerprint(source / 'a' / 'ep.MP4')}))
            (cache / 'outputs' / 'ep.vi.srt').write_text('Sub')
            mark_subtitled(videos, output)
            self.assertEqual([video['subtitled'] for video in videos], [False, False])
            (cache / 'srt.done').touch()
            mark_subtitled(videos, output)
            self.assertEqual([video['subtitled'] for video in videos], [True, False])
            (source / 'a' / 'ep.MP4').write_bytes(b'changed')
            mark_subtitled(videos, output)
            self.assertEqual([video['subtitled'] for video in videos], [False, False])


class VideoStreamTests(unittest.TestCase):
    def test_request_disconnect_is_handled_but_other_errors_propagate(self):
        handler = object.__new__(AppRequestHandler)
        for error in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error), patch('http.server.SimpleHTTPRequestHandler.handle', side_effect=error):
                handler.close_connection = False
                handler.handle()
                self.assertTrue(handler.close_connection)
        with patch('http.server.SimpleHTTPRequestHandler.handle', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                handler.handle()

    def test_stream_response_and_client_disconnect(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / 'video.mp4'
            video.write_bytes(b'0123456789')
            for headers, status, body in [({}, 200, b'0123456789'),
                                           ({'Range': 'bytes=2-5'}, 206, b'2345')]:
                for failure_at in (None, 'headers', 'body'):
                    for error in (BrokenPipeError, ConnectionResetError):
                        with self.subTest(headers=headers, failure_at=failure_at, error=error):
                            handler = object.__new__(AppRequestHandler)
                            handler.path = '/api/stream?path=' + quote(str(video))
                            handler.headers = headers
                            handler.local_request = Mock(return_value=True)
                            handler.close_connection = False
                            handler.send_response = Mock()
                            handler.send_header = Mock()
                            handler.end_headers = Mock()
                            handler.wfile = io.BytesIO()
                            if failure_at == 'headers':
                                handler.end_headers.side_effect = error()
                            elif failure_at == 'body':
                                handler.wfile = Mock()
                                handler.wfile.write.side_effect = error()
                            handler.do_GET()
                            handler.send_response.assert_called_once_with(status)
                            self.assertEqual(handler.close_connection, failure_at is not None)
                            if failure_at is None:
                                self.assertEqual(handler.wfile.getvalue(), body)

    def test_stream_rejects_non_video_and_symlinks_to_non_video(self):
        with TemporaryDirectory() as directory:
            secret = Path(directory) / ".ai-settings.json"
            secret.write_text("{}")
            link = Path(directory) / "video.mp4"
            link.symlink_to(secret)
            handler = object.__new__(AppRequestHandler)
            handler.send_error = Mock()
            handler.handle_stream_video({"path": [str(link)]})
            handler.send_error.assert_called_once_with(404)

    def test_stream_does_not_hide_other_io_errors(self):
        handler = object.__new__(AppRequestHandler)
        handler.path = '/api/stream?path=video.mp4'
        handler.local_request = Mock(return_value=True)
        handler.handle_stream_video = Mock(side_effect=PermissionError('denied'))
        with self.assertRaises(PermissionError):
            handler.do_GET()

    def test_api_rejects_external_origins_and_hosts(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), AppRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/api/status'
        with urlopen(url) as response:
            self.assertEqual(response.status, 200)
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
        for headers in ({'Origin': 'https://example.com'}, {'Host': 'example.com'}):
            for data in (None, b'{}'):
                with self.subTest(headers=headers, method='POST' if data else 'GET'):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(url, data=data, headers=headers))
                    self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()


class ModelAssetDiscoveryTests(unittest.TestCase):
    def test_model_demo_videos_are_not_offered_as_user_sources(self):
        from web_ui import discover_videos
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'source.mp4'
            source.touch()
            demo = root / 'models' / 'example' / 'demo.mp4'
            demo.parent.mkdir(parents=True)
            demo.touch()
            with patch('web_ui.ROOT', root):
                self.assertEqual([v['path'] for v in discover_videos([root])], [str(source.resolve())])
