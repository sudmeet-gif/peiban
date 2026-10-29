import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from companion.importer import inspect_file
from companion.import_ui import ImportDialog, ImportWorker
from companion.storage import Store


class ImportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_keeps_roles_sources_and_attachments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / 'source'
            source_dir.mkdir()
            (source_dir / 'photo.png').write_bytes(b'test')
            source = source_dir / 'chat.md'
            source.write_text('## 2025-01-01 12:00:00 Other\nhello ![x](photo.png)\n'
                              '## 2025-01-01 12:01:00 Me\nreply\n', encoding='utf-8')
            store = Store(root / 'data')
            persona = store.create_persona('Role')['id']
            dialog = ImportDialog(store, persona)
            self.assertIsNotNone(dialog)
            worker = ImportWorker(store, persona, source, inspect_file(source), 'Other', 'Me')
            outcomes, errors = [], []
            worker.completed.connect(outcomes.append)
            worker.failed.connect(errors.append)
            worker.run()
            self.assertFalse(errors)
            self.assertEqual(outcomes[0]['count'], 2)
            messages = store.list_messages(persona)
            self.assertEqual([m['role'] for m in messages], ['assistant', 'user'])
            self.assertEqual(messages[0]['media_type'], 'image')
            self.assertTrue(store.resolve_attachment(messages[0]['media_path']).exists())
            self.assertEqual(messages[0]['source_id'], outcomes[0]['source_id'])
            duplicate = ImportWorker(store, persona, source, inspect_file(source), 'Other', 'Me')
            duplicate.failed.connect(errors.append)
            duplicate.run()
            self.assertTrue(errors)
            self.assertEqual(len(store.list_messages(persona)), 2)

    def test_unassigned_text_is_memory_not_chat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'notes.txt'
            source.write_text('Some reference material.', encoding='utf-8')
            store = Store(root / 'data')
            persona = store.create_persona('Role')['id']
            worker = ImportWorker(store, persona, source, inspect_file(source), None, None)
            errors = []
            worker.failed.connect(errors.append)
            worker.run()
            self.assertFalse(errors)
            self.assertEqual(store.list_messages(persona), [])
            self.assertEqual(store.list_memories(persona)[0]['content'], 'Some reference material.')

    def test_style_draft_is_appended_and_versioned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'chat.md'
            source.write_text('## 2025-01-01 12:00:00 Other\n哈哈，晚安\n'
                              '## 2025-01-01 12:01:00 Me\nPRIVATE USER EXAMPLE\n', encoding='utf-8')
            store = Store(root / 'data')
            persona = store.create_persona('Role', profile='原有设定')['id']
            dialog = ImportDialog(store, persona)
            worker = ImportWorker(store, persona, source, inspect_file(source), 'Other', 'Me')
            worker.completed.connect(dialog.on_completed)
            worker.run()
            profile = store.get_persona(persona)['profile']
            self.assertTrue(profile.startswith('原有设定'))
            self.assertIn('哈哈', profile)
            self.assertNotIn('PRIVATE USER EXAMPLE', profile)
            self.assertEqual(len(store.list_profile_versions(persona)), 2)


if __name__ == '__main__':
    unittest.main()
