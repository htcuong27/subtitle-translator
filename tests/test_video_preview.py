from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from video_preview import VideoPreview


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.files = TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.video = Path(self.files.name) / 'video.mp4'
        self.video.write_bytes(b'original')
        self.preview = VideoPreview()
        self.addCleanup(self.preview.directory.cleanup)

    @patch('video_preview._get_video_info', return_value=(25, 12, 320, 240))
    def test_uses_unsaved_subtitles_and_regions_without_changing_source(self, info):
        def burn(video, subtitle, output, **kwargs):
            self.assertEqual(video, self.video.resolve())
            self.assertIn('Chưa lưu', subtitle.read_text())
            self.assertEqual(kwargs, {'preview_start': 10.0, 'preview_duration': 2.0})
            output.write_bytes(b'preview')
        with patch('video_preview.burn_subtitle_to_video', side_effect=burn):
            result = self.preview.render({'video': str(self.video), 'time': 10, 'text': '1\n00:00:10,000 --> 00:00:11,000\nChưa lưu\n'})
        self.assertTrue(Path(result['path']).is_file())
        self.assertEqual(self.video.read_bytes(), b'original')

    @patch('video_preview._get_video_info', return_value=(25, 12, 320, 240))
    def test_validation_and_busy(self, info):
        body = {'video': str(self.video)}
        for time in (-1, float('nan'), float('inf'), 12):
            with self.subTest(time=time), self.assertRaises(ValueError):
                self.preview.render({**body, 'time': time})
        with self.assertRaises(ValueError):
            self.preview.render({**body, 'text': 'invalid'})
        self.preview.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'đang được tạo'):
                self.preview.render(body)
        finally:
            self.preview.lock.release()

    @patch('video_preview._get_video_info', return_value=(25, 12, 320, 240))
    def test_without_subtitles_and_failure_cleanup(self, info):
        def burn(video, subtitle, output, **kwargs):
            self.assertIsNone(subtitle)
            output.write_bytes(b'partial')
            raise RuntimeError('failed')
        with patch('video_preview.burn_subtitle_to_video', side_effect=burn):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                self.preview.render({'video': str(self.video)})
        self.assertEqual(list(Path(self.preview.directory.name).iterdir()), [])
