"""Focused checks for chat scrolling, the emoji picker, and the sidebar."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QAbstractSlider
from PySide6.QtCore import QEventLoop, QTimer
from companion import ui
from companion.settings import Settings


class DummyModels:
    def close(self): pass


class ViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings=Settings(Path(self.temp.name)/'settings.json')
        self.settings_patch=patch.object(ui,'Settings',return_value=self.settings)
        self.model_patch=patch.object(ui,'LocalModels',return_value=DummyModels())
        self.settings_patch.start(); self.model_patch.start()
        self.window=ui.MainWindow(); self.window.resize(850,580); self.window.show()
        self.persona=self.window.store.create_persona('测试角色')
        self.window.store.add_messages(self.persona['id'],[{'role':'assistant','content':f'第 {i} 条：'+'测试聊天内容 '*12} for i in range(60)])
        self.window.reload_personas(self.persona['id']); self.events()

    def events(self):
        loop=QEventLoop(); QTimer.singleShot(70,loop.quit); loop.exec()

    def tearDown(self):
        self.window.close(); self.app.processEvents()
        self.settings_patch.stop(); self.model_patch.stop(); self.temp.cleanup()

    def test_new_role_and_send_go_to_latest_but_manual_history_stays(self):
        bar=self.window.browser.verticalScrollBar()
        self.assertGreater(bar.maximum(),0)
        self.assertEqual(bar.value(),bar.maximum())
        bar.triggerAction(QAbstractSlider.SliderAction.SliderToMinimum); self.events()
        self.assertFalse(self.window._follow_latest)
        self.window.fragment('新的一部分'); self.events()
        self.assertLess(bar.value(),bar.maximum())
        self.window.reply=lambda pid,content: None
        self.window.entry.setPlainText('一条新消息'); self.window.send(); self.events()
        self.assertEqual(bar.value(),bar.maximum())
        self.assertTrue(self.window._follow_latest)

    def test_collapsible_settings_and_insert_emoji(self):
        self.assertFalse(self.window.settings_panel.isVisible())
        self.window.settings_toggle.click(); self.events()
        self.assertTrue(self.window.settings_panel.isVisible())
        self.window.settings_toggle.click(); self.events()
        self.assertFalse(self.window.settings_panel.isVisible())
        self.window.entry.setPlainText('你好')
        class FakeEmoji:
            choice='😊'
            def __init__(self,parent): pass
            def exec(self): return 1
        with patch.object(ui,'EmojiDialog',FakeEmoji): self.window.insert_emoji()
        self.assertIn('😊',self.window.entry.toPlainText())


if __name__=='__main__': unittest.main()
