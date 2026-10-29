"""Regression tests for GUI callbacks produced by fast background jobs."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from companion import ui
from companion.settings import Settings


class FakeModel:
    def close(self):
        pass


class WorkerThreadSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.config = Settings(Path(self.folder.name) / 'settings.json')
        self.patches = [patch.object(ui, 'Settings', return_value=self.config),
                        patch.object(ui, 'LocalModels', return_value=FakeModel()),
                        patch.object(ui.QMessageBox, 'warning')]
        for p in self.patches:
            p.start()
        self.window = ui.MainWindow()

    def tearDown(self):
        for thread, worker in list(self.window.threads):
            thread.quit()
            thread.wait(2000)
        self.window.sweep_threads()
        self.window.close()
        self.app.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()

    def wait_until(self, predicate):
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        timer.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        timer.stop()
        self.assertTrue(predicate(), '后台任务未完成')

    def test_fragments_and_nested_results_run_on_gui_thread(self):
        gui_thread = threading.get_ident()
        seen = []
        done = []

        def fragment(text):
            seen.append(('fragment', threading.get_ident(), text))

        def voice_finished(value):
            seen.append(('voice', threading.get_ident(), value))
            done.append(True)

        def chat_finished(value):
            seen.append(('chat', threading.get_ident(), value))
            # A real chat completion starts another job for the voice reply.
            self.window.work(lambda emit: 'voice', voice_finished, '语音')

        def chat(emit):
            emit('一')
            emit('二')
            return 'reply'

        self.window.work(chat, chat_finished, '聊天', fragment)
        self.wait_until(lambda: bool(done) and not self.window.threads)
        self.assertEqual([x[0] for x in seen], ['fragment', 'fragment', 'chat', 'voice'])
        self.assertTrue(all(x[1] == gui_thread for x in seen))

    def test_callback_exception_is_reported_without_crashing(self):
        def fail(_):
            raise ValueError('故障测试')
        self.window.work(lambda emit: 'ok', fail, '测试')
        self.wait_until(lambda: not self.window.threads)
        warning = ui.QMessageBox.warning
        self.assertTrue(warning.called)
        self.assertIn('故障测试', warning.call_args.args[-1])


if __name__ == '__main__':
    unittest.main()
