import unittest
from contextlib import chdir
from unittest.mock import Mock, patch
import engine
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

from engine import copy_srt_timing
from recognition import _ocr_crop_at, _split_whisper_by_gaps, prepare_ocr_frame, read_white_horizontal_text
from subtitles import format_timestamp_srt


class SubtitleTimingTest(unittest.TestCase):

    def test_ocr_only_passes_regions_and_writes_subtitles(self):
        import numpy as np
        from recognition import transcribe_video_ocr

        cap = Mock()
        cap.get.return_value = 10
        cap.read.return_value = (True, np.zeros((100, 100, 3), dtype=np.uint8))
        config = {'regions': [[0.7, 0.9, 0.1, 0.9]]}
        with TemporaryDirectory() as directory, \
             patch('recognition._ocr_engine', return_value=Mock()), \
             patch('recognition._get_video_info', return_value=(10, 1, 100, 100)), \
             patch('cv2.VideoCapture', return_value=cap), \
             patch('recognition.read_white_horizontal_text', return_value=[('你好', 0.9)]):
            output = Path(directory) / 'out.srt'
            transcribe_video_ocr(Path('input.mp4'), output, ocr_config=config)
            self.assertIn('你好', output.read_text())
            self.assertIn('00:00:00,000 --> 00:00:01,000', output.read_text())
            cap.release.assert_called_once()



    def test_multiple_ocr_regions_mask_outside_without_changing_source(self):
        import numpy as np
        frame = np.full((100, 100, 3), 255, dtype=np.uint8)
        config = engine.parse_ocr_config({'regions': [[0.1,0.3,0.1,0.9], [0.7,0.9,0.1,0.9]]})
        masked = prepare_ocr_frame(frame, config)
        self.assertTrue((masked[12:18,72:88] == 255).all())
        self.assertTrue((masked[72:88,12:88] == 255).all())
        self.assertTrue((masked[40:60] == 0).all())
        self.assertTrue((frame == 255).all())
        for config in ({'regions': []}, {'regions': [[0.5,0.1,0,1]]},
                       {'regions': [[0,1,0,2]]}):
            with self.assertRaises(ValueError):
                engine.parse_ocr_config(config)

    def test_existing_translation_skips_recognition_and_api_for_all_modes(self):
        with TemporaryDirectory() as directory, chdir(directory):
            Path('ep.mp4').touch()
            Path('outputs').mkdir()
            subtitle = Path('outputs/ep.vi.srt')
            content = '1\n00:00:01,000 --> 00:00:02,000\nXin chào\n'
            subtitle.write_text(content)
            def export(video, srt, output, **options):
                output.write_bytes(b'exported')

            with patch('engine.check_binary'), patch('engine.probe_subtitle_streams') as probe, \
                 patch('engine.translate_srt') as translate, \
                 patch('engine.add_soft_subtitle_to_video', side_effect=export) as soft, \
                 patch('engine.burn_subtitle_to_video', side_effect=export) as burn:
                for mode in ['srt', 'soft', 'burn', 'srt_burn']:
                    with patch('sys.argv', ['engine.py', '--input', 'ep.mp4', '--mode', mode]):
                        engine.main()
                probe.assert_not_called()
                translate.assert_not_called()
                soft.assert_called_once()
                self.assertEqual(burn.call_count, 2)
                self.assertEqual(subtitle.read_text(), content)

    def test_existing_chinese_srt_survives_failed_translation_and_ocr_only_retry(self):
        with TemporaryDirectory() as directory, chdir(directory):
            Path('ep.mp4').touch()
            Path('outputs').mkdir()
            source = Path('outputs/ep.zh.srt')
            content = '1\n00:00:01,000 --> 00:00:02,000\n你好\n'
            source.write_text(content)
            argv = ['engine.py', '--input', 'ep.mp4', '--ocr-only']
            with patch('sys.argv', argv), patch('engine.check_binary'), \
                 patch('engine.transcribe_video_ocr') as ocr, patch('engine.translate_batch') as api:
                api.side_effect = RuntimeError('API error')
                with self.assertRaisesRegex(RuntimeError, 'API error'):
                    engine.main()
                self.assertTrue(Path('zh.done').is_file())
                self.assertFalse(Path('srt.done').exists())
                api.side_effect = None
                api.return_value = ['Xin chào']
                engine.main()
                engine.main()
                self.assertEqual(api.call_count, 2)
                ocr.assert_not_called()
                self.assertTrue(Path('srt.done').is_file())
                self.assertEqual(source.read_text(), content)



    def test_translation_preserves_source_text(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'zh.srt'
            output = Path(directory) / 'vi.srt'
            source.write_text('1\n00:00:01,000 --> 00:00:02,000\n你好 example.test\n')
            with patch('engine.translate_batch', return_value=['Xin chào example.test']) as api:
                engine.translate_srt(source, output)
            self.assertEqual(api.call_args.args[0][0]['text'], '你好 example.test')
            self.assertIn('Xin chào example.test', output.read_text())

    def test_interrupted_srt_write_does_not_publish_partial_translation(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'zh.srt'
            output = Path(directory) / 'vi.srt'
            source.write_text('1\n00:00:01,000 --> 00:00:02,000\n你好\n')

            def interrupted_write(items, path):
                path.write_text('partial')
                raise OSError('interrupted')

            with patch('engine.translate_batch', return_value=['Xin chào']), \
                 patch('engine.write_srt', side_effect=interrupted_write):
                with self.assertRaises(OSError):
                    engine.translate_srt(source, output)
            self.assertFalse(output.exists())

    def test_splits_long_silence_between_words(self):
        segment = SimpleNamespace(start=0, end=8, text="你好", words=[
            SimpleNamespace(start=1, end=2, word="你"),
            SimpleNamespace(start=5, end=6, word="好"),
        ])
        self.assertEqual(_split_whisper_by_gaps([segment]), [
            {"start": 1, "end": 2, "text": "你"},
            {"start": 5, "end": 6, "text": "好"},
        ])

    def test_rounds_srt_timestamp_without_losing_a_millisecond(self):
        self.assertEqual(format_timestamp_srt(1.9996), "00:00:02,000")

    def test_updates_timing_without_changing_translation(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.srt"
            translated = Path(directory) / "translated.srt"
            source.write_text("1\n00:00:02,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
            translated.write_text("1\n00:00:01,000 --> 00:00:09,000\nXin chào\n", encoding="utf-8")

            copy_srt_timing(source, translated)

            self.assertEqual(
                translated.read_text(encoding="utf-8"),
                "1\n00:00:02,000 --> 00:00:03,000\nXin chào\n\n",
            )

    def test_parse_crop_box_ratios_and_pixels(self):
        # Default fallback
        y1, y2, x1, x2 = engine.parse_crop_box(None, 1000, 2000)
        self.assertEqual((y1, y2, x1, x2), (1560, 1920, 120, 880))

        # Percentage ratios
        y1, y2, x1, x2 = engine.parse_crop_box("0.5, 0.8, 0.2, 0.9", 1000, 2000)
        self.assertEqual((y1, y2, x1, x2), (1000, 1600, 200, 900))

        # Absolute pixels
        y1, y2, x1, x2 = engine.parse_crop_box("100, 500, 50, 400", 1000, 2000)
        self.assertEqual((y1, y2, x1, x2), (100, 500, 50, 400))


if __name__ == "__main__":
    unittest.main()
