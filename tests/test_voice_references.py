import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from companion.settings import Settings
from companion.storage import Store
from companion.voice_references import decode_references, encode_references
from companion import ui


def wav(path, frequency=16000):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(frequency)
        output.writeframes(b'\0\0' * frequency)


class VoiceReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_legacy_and_multiple_reference_roundtrip(self):
        self.assertEqual(decode_references('attachments/old.wav'), ['attachments/old.wav'])
        self.assertEqual(decode_references(encode_references(['a.wav','b.wav','a.wav'])), ['a.wav','b.wav'])
        self.assertEqual(decode_references(encode_references([])), [])

    def test_add_multiple_and_preserve_existing_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); store=Store(root/'data'); persona=store.create_persona('测试')
            a=root/'a.wav'; b=root/'b.wav'; wav(a); wav(b,22050)
            old=store.copy_attachment(persona['id'],a); store.update_persona(persona['id'],voice_ref=old)
            persona=store.get_persona(persona['id']); dialog=ui.CharacterDialog(store,persona)
            self.assertEqual(dialog.reference.count(),1)
            with patch.object(ui.QFileDialog,'getOpenFileNames',return_value=([str(a),str(b)],'')):
                dialog.pick_voice()
            self.assertEqual(dialog.reference.count(),2)
            dialog.voice.setCurrentIndex(dialog.voice.findData('clone')); dialog.consent.setChecked(True); dialog.save()
            saved=decode_references(store.get_persona(persona['id'])['voice_ref'])
            self.assertEqual(len(saved),2); self.assertTrue(all(store.resolve_attachment(x).is_file() for x in saved))
            reopened=ui.CharacterDialog(store,store.get_persona(persona['id']))
            self.assertEqual(reopened.reference.count(),2)
            self.assertEqual(reopened.reference.item(1).text(),'b.wav'); reopened.close()

    def test_audio_link_uses_in_app_player(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); settings=Settings(root/'settings.json')
            with patch.object(ui,'Settings',return_value=settings):
                window=ui.MainWindow(); person=window.store.create_persona('测试')
                sample=root/'clip.wav'; wav(sample)
                path=window.store.copy_attachment(person['id'],sample); url=QUrl.fromLocalFile(str(window.store.resolve_attachment(path)))
                with patch.object(ui.QDesktopServices,'openUrl') as external:
                    window.open_link(url)
                    self.assertEqual(window.audio_player.source(),url)
                    external.assert_not_called()
                window.audio_player.stop(); window.audio_player.setSource(QUrl()); window.close()
                self.app.processEvents()


if __name__ == '__main__': unittest.main()
