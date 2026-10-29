"""Offscreen integration checks; never touch the user's configured data."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

from companion.settings import Settings
from companion import ui


class FakeLocalModels:
    def __init__(self):
        self.calls = []
        self.closed = False

    def stream_chat(self, messages, cancel_event=None):
        self.calls.append(messages)
        yield '模拟'
        yield '回复'

    def close(self):
        self.closed = True


class UIIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.temp.name) / 'settings.json')
        self.model = FakeLocalModels()
        self.settings_patch = patch.object(ui, 'Settings', return_value=self.settings)
        self.models_patch = patch.object(ui, 'LocalModels', return_value=self.model)
        self.warning_patch = patch.object(ui.QMessageBox, 'warning')
        self.settings_patch.start()
        self.models_patch.start()
        self.warning = self.warning_patch.start()
        self.window = ui.MainWindow()
        self.finished_calls = []
        original_finished = self.window.finished
        def traced_finished(thread):
            self.finished_calls.append(id(thread))
            original_finished(thread)
        self.window.finished = traced_finished

    def tearDown(self):
        # Drain deferred QThread cleanup before closing or releasing the fixture.
        for thread, _ in self.window.threads:
            thread.quit()
            thread.wait(2000)
        self.window.threads.clear()
        self.window.close()
        self.app.processEvents()
        self.warning_patch.stop()
        self.models_patch.stop()
        self.settings_patch.stop()
        self.temp.cleanup()

    def _wait_workers(self):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        idle_since = [None]
        def check():
            if self.window.threads:
                idle_since[0] = None
            elif idle_since[0] is None:
                idle_since[0] = time.monotonic()
            elif time.monotonic() - idle_since[0] >= 0.25:
                loop.quit()
        poll.timeout.connect(check)
        poll.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll.stop()
        states = [(id(thread), id(worker), thread.isRunning(), thread.isFinished()) for thread, worker in self.window.threads]
        self.assertFalse(self.window.threads, f'后台任务未结束：{states}，完成回调 {self.finished_calls}，模型调用 {len(self.model.calls)} 次')

    def _new_persona(self, name):
        with patch.object(ui.QInputDialog, 'getText', return_value=(name, True)):
            self.window.add_persona()
        self.assertEqual(self.window.persona['name'], name)
        return self.window.persona['id']

    def test_new_characters_chat_and_background_cleanup(self):
        first = self._new_persona('甲角色')
        self.window.entry.setPlainText('甲角色专属消息')
        self.window.send()
        self._wait_workers()
        first_messages = self.window.store.list_messages(first)
        self.assertEqual([(m['role'], m['content']) for m in first_messages],
                         [('user', '甲角色专属消息'), ('assistant', '模拟回复')])
        self.assertTrue(self.window.personas.isEnabled())

        second = self._new_persona('乙角色')
        self.assertNotEqual(first, second)
        self.window.entry.setPlainText('乙角色专属消息')
        self.window.send()
        self._wait_workers()
        self.assertEqual([(m['role'], m['content']) for m in self.window.store.list_messages(second)],
                         [('user', '乙角色专属消息'), ('assistant', '模拟回复')])
        self.assertEqual(self.window.store.list_messages(first), first_messages)
        self.assertNotIn('甲角色专属消息', repr(self.model.calls[-1]))
        self.assertIn('乙角色专属消息', repr(self.model.calls[-1]))
        self.warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
