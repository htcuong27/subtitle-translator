import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from subtitle_editor import edit_subtitles, validate_srt
from engine import burn_subtitle_to_video
from web_runner import build_jobs

SRT = '9\n00:00:01,000 --> 00:00:02,000\nXin chào\n'


class SubtitleEditorTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(validate_srt(SRT)[0]['index'], '1')
        for text in ('', 'broken', SRT.replace('02,000', '00,000'), SRT + '\ninvalid', SRT.replace('01,000', '61,000')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_srt(text)

    def test_save_read_and_invalidate_exports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            video = source / 'episode.mp4'
            video.write_bytes(b'video')
            body = {'video': str(video), 'videos': [str(video)], 'output': 'results', 'text': SRT}
            result = edit_subtitles(root, body, save=True)
            destination = root / 'results' / 'episode'
            for name in ('burn.done', 'soft.done'):
                (destination / name).touch()
            edit_subtitles(root, body, save=True)
            self.assertFalse((destination / 'burn.done').exists())
            self.assertFalse((destination / 'soft.done').exists())
            self.assertEqual(edit_subtitles(root, body)['text'], result['text'])
            self.assertEqual(json.loads((destination / 'source.json').read_text())['sha256'].__len__(), 64)
            video.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'thay đổi'):
                edit_subtitles(root, body, save=True)

    def test_output_lock(self):
        import fcntl
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source').mkdir()
            video = root / 'source' / 'v.mp4'
            video.touch()
            output = root / 'out'
            output.mkdir()
            with (output / '.translate.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'Hàng đợi'):
                    edit_subtitles(root, {'video': str(video), 'output': 'out'})

    def test_failed_export_invalidation_preserves_saved_subtitle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'source' / 'v.mp4'
            video.parent.mkdir()
            video.write_bytes(b'video')
            body = {'video': str(video), 'output': 'out', 'text': SRT}
            edit_subtitles(root, body, save=True)
            destination = root / 'out' / 'v'
            subtitle = destination / 'outputs' / 'v.vi.srt'
            original = subtitle.read_text()
            (destination / 'burn.done').touch()
            unlink = Path.unlink
            def fail_marker(path, *args, **kwargs):
                if path.name == 'burn.done':
                    raise OSError('cannot invalidate export')
                return unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', fail_marker):
                with self.assertRaisesRegex(OSError, 'cannot invalidate'):
                    edit_subtitles(root, {**body, 'text': SRT.replace('Xin chào', 'Đã sửa')}, save=True)
            self.assertEqual(subtitle.read_text(), original)





class SubtitleDiscoveryTests(unittest.TestCase):
    def test_find_matching_source_in_series_cache_without_copying(self):
        from translate import fingerprint
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            video = source / 'ep.mp4'
            video.write_bytes(b'correct video')
            for series, sha, text in [('right', fingerprint(video), SRT),
                                      ('wrong', 'different source', SRT.replace('Xin chào', 'Sai video'))]:
                work = root / 'outputs' / series / 'ep'
                cache = work / 'outputs'
                cache.mkdir(parents=True)
                (work / 'source.json').write_text(json.dumps({'sha256': sha}))
                (cache / 'ep.vi.srt').write_text(text)
            result = edit_subtitles(root, {'video': str(video), 'output': 'outputs'})
            self.assertEqual(result['text'], SRT)
            self.assertIn('/right/', result['loaded_path'])
            self.assertFalse(Path(result['path']).exists())
            video.write_bytes(b'not cached')
            self.assertEqual(edit_subtitles(root, {'video': str(video), 'output': 'outputs'})['text'], '')
