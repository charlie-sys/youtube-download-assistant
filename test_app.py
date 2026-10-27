import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app


class Tests(unittest.TestCase):
    def job(self, folder):
        return dict(id='test', url='https://www.youtube.com/watch?v=abc&list=xyz', folder=folder,
                    mode='视频 + 音频', quality='1080p', language='中文 + 英文', status='排队中', file='')

    def test_command_spaces_url_and_subtitles(self):
        j = self.job('D:\\My Videos\\中文 文件')
        args = app.command(j)
        self.assertEqual(args[args.index('-P') + 1], j['folder'])
        self.assertEqual(args[-1], j['url'])
        self.assertIn('bv*[height<=1080]+ba/b[height<=1080]', args)
        j['mode'] = '仅字幕'
        args = app.command(j)
        self.assertIn('--skip-download', args)
        self.assertIn('--write-auto-subs', args)
        self.assertNotIn('-f', args)

    def test_url_validation(self):
        self.assertTrue(app.valid_url('https://youtu.be/abc'))
        for url in ['file:///a', 'https://youtube.com.evil.com/x', 'https://evil.com/?youtube.com', 'javascript:abc']:
            self.assertFalse(app.valid_url(url))

    def test_history_recovery(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'ROOT', Path(folder)):
            m = app.Manager()
            j = self.job(folder)
            j['status'] = '下载中'
            m.jobs.append(j)
            m.save()
            restored = app.Manager()
            self.assertEqual(restored.jobs[0]['status'], '已暂停')
            self.assertEqual(restored.jobs[0]['folder'], folder)

    def test_process_output_success_failure_and_empty_subtitles(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'ROOT', Path(folder)):
            output = Path(folder) / 'example.mkv'
            output.write_bytes(b'test')
            class Process:
                stdout = ['PROGRESS| 65.0%|1MiB/s|00:03\n', 'FILE|' + str(output) + '\n']
                def wait(self): return 0
            m = app.Manager()
            j = self.job(folder)
            m.jobs.append(j)
            with patch.object(app.subprocess, 'Popen', return_value=Process()):
                m.download(j)
            self.assertEqual(j['status'], '已完成')
            self.assertEqual(j['file'], str(output))
            class Failed(Process):
                stdout = ['ERROR: connection failed\n']
                def wait(self): return 1
            with patch.object(app.subprocess, 'Popen', return_value=Failed()):
                m.download(j)
            self.assertEqual(j['status'], '失败')
            self.assertIn('connection failed', j['detail'])
            j['mode'] = '仅字幕'
            class Empty(Process): stdout = ['There are no subtitles\n']
            with patch.object(app.subprocess, 'Popen', return_value=Empty()):
                m.download(j)
            self.assertEqual(j['status'], '需检查')

    def test_pause_and_resume_queue(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'ROOT', Path(folder)):
            m = app.Manager()
            j = self.job(folder)
            m.jobs.append(j)
            m.stop(j, '已暂停')
            self.assertEqual(j['status'], '已暂停')
            m.active = j
            class Process:
                @property
                def stdout(self):
                    m.stop_reason = '已暂停'
                    return iter(['partial download\n'])
                def wait(self): return 1
            with patch.object(app.subprocess, 'Popen', return_value=Process()):
                m.download(j)
            self.assertEqual(j['status'], '已暂停')


if __name__ == '__main__':
    unittest.main(verbosity=2)
