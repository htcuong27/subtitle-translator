from pathlib import Path
import io
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from web_runner import RunnerState, build_jobs


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.videos = []
        for series in ('a', 'b'):
            video = self.root / 'input' / series / 'ep-001.mp4'
            video.parent.mkdir(parents=True)
            video.touch()
            self.videos.append(str(video))
        self.body = {'videos': self.videos, 'mode': 'srt_burn', 'output': 'results'}

    def test_jobs_preserve_series_names_and_options(self):
        jobs = build_jobs(self.root, dict(self.body, no_ocr=True, crop_box='0.7,0.9,0,1'))
        for series, (_, cmd) in zip(('a', 'b'), jobs):
            self.assertEqual(cmd[cmd.index('--output') + 1], str(self.root / 'results' / series))
            self.assertEqual(cmd[cmd.index('--mode') + 1], 'srt_burn')
            self.assertIn('--no-ocr', cmd)
            self.assertIn('--crop-box', cmd)
        self.assertEqual(len(build_jobs(self.root, dict(self.body, videos=self.videos * 2))), 2)

    def test_multiple_regions_reach_cli_per_video(self):
        import json
        config = {'regions': [[0.1,0.3,0,1], [0.7,0.9,0,1]], 'exclude': [[0.1,0.2,0.8,1]]}
        jobs = build_jobs(self.root, dict(self.body, video_ocr_regions={self.videos[0]: config}))
        self.assertEqual(json.loads(jobs[0][1][jobs[0][1].index('--ocr-regions')+1]), config)
        self.assertNotIn('--ocr-regions', jobs[1][1])

    def test_saved_video_crop_overrides_shared_fallback(self):
        jobs = build_jobs(self.root, dict(self.body, crop_box='0.7,0.9,0,1',
                          video_crops={self.videos[0]: '0.4,0.6,0.1,0.9'}))
        self.assertEqual(jobs[0][1][jobs[0][1].index('--crop-box') + 1], '0.4,0.6,0.1,0.9')
        self.assertEqual(jobs[1][1][jobs[1][1].index('--crop-box') + 1], '0.7,0.9,0,1')

    def test_invalid_input_mode_and_source_output_rejected(self):
        for change in ({'videos': []}, {'videos': ['missing.mp4']}, {'mode': 'invalid'},
                       {'output': str(self.root / 'input')}):
            with self.assertRaises(ValueError):
                build_jobs(self.root, dict(self.body, **change))

    def test_sequential_failure_continues_and_rejects_second_start(self):
        runner = RunnerState(self.root)
        gate = threading.Event()
        started = threading.Event()
        calls = []

        class Process:
            def __init__(self, command, **kwargs):
                calls.append(command)
                self.stdout = io.StringIO('log\n')
                started.set()
            def wait(self):
                gate.wait(2)
                return 1 if len(calls) == 1 else 0

        with patch('web_runner.subprocess.Popen', Process):
            runner.start(self.body)
            self.assertTrue(started.wait(2))
            with self.assertRaises(ValueError):
                runner.start(self.body)
            self.assertEqual(len(calls), 1)
            done = self.wait_done(runner, gate)
            self.assertTrue(done)
        self.assertEqual([i['status'] for i in runner.items], ['failed', 'completed'])
        self.assertEqual(runner.status, 'failed')

    def wait_done(self, runner, gate):
        gate.set()
        for _ in range(200):
            if runner.snapshot()['status'] != 'running':
                return True
            threading.Event().wait(.01)
        return False

    def test_stop_prevents_next_video(self):
        runner = RunnerState(self.root)
        started, released = threading.Event(), threading.Event()
        class Process:
            pid = 12345
            stdout = io.StringIO('')
            def __init__(self, *args, **kwargs):
                started.set()
            def wait(self, timeout=None):
                released.wait(2)
                return -15
        with patch('web_runner.subprocess.Popen', Process) as spawn, patch('web_runner.os.killpg', side_effect=lambda *args: released.set()):
            runner.start(self.body)
            self.assertTrue(started.wait(2))
            runner.stop()
            self.assertTrue(self.wait_done(runner, released))
        self.assertEqual(runner.status, 'stopped')
        self.assertEqual([i['status'] for i in runner.items], ['stopped', 'stopped'])


if __name__ == '__main__':
    unittest.main()
