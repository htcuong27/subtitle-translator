import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import translate


class BatchTest(unittest.TestCase):
    def test_colliding_video_names_are_rejected_before_processing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'source'
            source.mkdir()
            videos = [source / 'episode.mp4', source / 'EPISODE.MP4']
            with patch('translate.discover', return_value=videos), \
                 patch('sys.argv', ['translate.py', '--input', str(source), '--output', str(root / 'out'), '--list']):
                with self.assertRaises(SystemExit) as error:
                    translate.main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((root / 'out').exists())

    def test_discovery_keeps_series_and_ignores_partial_downloads(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ['a/ep-001.mp4', 'b/ep-001.MP4', 'b/ep-002.mp4.part', 'b/ep.vi.burned.mp4']:
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.touch()
            self.assertEqual([str(p.relative_to(root)) for p in translate.discover(root)],
                             ['a/ep-001.mp4', 'b/ep-001.MP4'])

    def test_multi_region_configuration_is_forwarded_to_engine(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'ep.mp4'
            source.write_bytes(b'video')
            config = '{"regions":[[0.1,0.3,0,1]],"exclude":[]}'
            args = argparse.Namespace(mode='srt', no_ocr=False, ocr_regions=config)
            def engine(command, cwd, check):
                self.assertEqual(command[command.index('--ocr-regions')+1], config)
                (cwd / 'outputs').mkdir()
                (cwd / 'outputs/ep.vi.srt').write_text('Vietnamese')
            with patch('translate.subprocess.run', side_effect=engine):
                translate.process(source, root / 'result', args)

    def test_completed_skipped_changed_source_reprocessed_existing_translation_kept(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'ep.mp4'
            source.write_bytes(b'first')
            destination = root / 'result'
            args = argparse.Namespace(mode='srt', no_ocr=True)

            def engine(command, cwd, check):
                cache = cwd / 'outputs'
                cache.mkdir(exist_ok=True)
                if run.call_count <= 2:
                    self.assertFalse((cache / 'ep.vi.srt').exists())
                else:
                    self.assertEqual((cache / 'ep.vi.srt').read_text(), 'Vietnamese')
                (cache / 'ep.vi.srt').write_text('Vietnamese')

            with patch('translate.subprocess.run', side_effect=engine) as run:
                translate.process(source, destination, args)
                transcript = destination / 'outputs/ep.zh.srt'
                transcript.write_text('1\n00:00:01,000 --> 00:00:02,000\n你好\n')
                translate.process(source, destination, args)
                self.assertTrue((destination / 'zh.done').is_file())
                self.assertEqual(run.call_count, 1)
                source.write_bytes(b'changed')
                translate.process(source, destination, args)
                self.assertEqual(run.call_count, 2)
                self.assertFalse((destination / 'zh.done').exists())
                self.assertFalse(transcript.exists())
                args.no_ocr = False
                with patch.dict('os.environ', {'GEMINI_MODEL': 'changed-model'}):
                    translate.process(source, destination, args)
                self.assertEqual(run.call_count, 2)
                (destination / 'srt.done').unlink()
                translate.process(source, destination, args)
                self.assertEqual(run.call_count, 3)




    def test_legacy_burn_cache_is_rebuilt_and_subtitle_preserved(self):
        import json
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / 'ep.mp4'
            video.write_bytes(b'video')
            destination = root / 'result'
            cache = destination / 'outputs'
            cache.mkdir(parents=True)
            subtitle = cache / 'ep.vi.srt'
            subtitle.write_text('existing subtitle')
            (cache / 'ep.vi.burned.mp4').write_bytes(b'old')
            (destination / 'burn.done').touch()
            (destination / 'source.json').write_text(json.dumps({'sha256': translate.fingerprint(video), 'remove_watermark': True}))
            def rebuild(command, cwd, check):
                self.assertEqual(subtitle.read_text(), 'existing subtitle')
                (cache / 'ep.vi.burned.mp4').write_bytes(b'new')
            with patch('translate.subprocess.run', side_effect=rebuild) as run:
                translate.process(video, destination, argparse.Namespace(mode='burn', no_ocr=True))
                self.assertEqual(run.call_count, 1)
            self.assertEqual((cache / 'ep.vi.burned.mp4').read_bytes(), b'new')


if __name__ == '__main__':
    unittest.main()
