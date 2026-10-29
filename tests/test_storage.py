import tempfile
import unittest
import zipfile
from pathlib import Path

from companion.storage import Store
from companion.settings import Settings


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Store(self.root / 'data')
        self.a = self.db.create_persona('甲')['id']
        self.b = self.db.create_persona('乙')['id']

    def tearDown(self):
        self.temp.cleanup()

    def test_character_isolation_and_memory_deletion(self):
        mid = self.db.add_memory(self.a, '喜欢苹果和散步')
        self.db.add_memory(self.b, '喜欢苹果')
        self.assertEqual([m['id'] for m in self.db.search_memories(self.a, '苹果')], [mid])
        self.assertFalse(self.db.delete_memory(self.b, mid))
        self.assertTrue(self.db.delete_memory(self.a, mid))
        self.assertEqual(self.db.search_memories(self.a, '苹果'), [])
        self.assertEqual(len(self.db.list_memories(self.b)), 1)

    def test_import_sources_and_profile_versions(self):
        source = self.db.add_source(self.a, '记录', 'hash')
        self.db.add_messages(self.a, [{'role': 'user', 'content': '你好'}, {'role': 'assistant', 'content': '你好啊'}], source)
        with self.assertRaises(ValueError):
            self.db.add_message(self.b, 'user', '错误', source_id=source)
        self.assertEqual(len(self.db.list_messages(self.a)), 2)
        self.assertEqual(self.db.list_messages(self.b), [])
        self.db.save_profile(self.a, '原始设定')
        version = self.db.list_profile_versions(self.a)[0]['id']
        self.db.save_profile(self.a, '新设定')
        self.db.restore_profile(self.a, version)
        self.assertEqual(self.db.get_persona(self.a)['profile'], '原始设定')
        self.db.delete_source(self.a, source, delete_messages=True)
        self.assertEqual(self.db.list_messages(self.a), [])

    def test_deleting_persona_removes_only_its_copied_files(self):
        source = self.root / 'file.txt'
        source.write_text('example', encoding='utf-8')
        a_copy = self.db.resolve_attachment(self.db.copy_attachment(self.a, source))
        b_copy = self.db.resolve_attachment(self.db.copy_attachment(self.b, source))
        self.db.delete_persona(self.a)
        self.assertFalse(a_copy.exists())
        self.assertTrue(b_copy.exists())
        self.assertTrue(source.exists())

    def test_backup_restore_and_untrusted_paths(self):
        source = self.root / 'photo.png'
        source.write_bytes(b'fixture')
        rel = self.db.copy_attachment(self.a, source)
        self.assertEqual(self.db.resolve_attachment(rel).read_bytes(), b'fixture')
        with self.assertRaises(ValueError):
            self.db.resolve_attachment('../photo.png')
        archive = self.root / 'backup.zip'
        self.db.backup(archive)
        restored = Store.restore_backup(archive, self.root / 'restored')
        self.assertEqual(restored.get_persona(self.a)['name'], '甲')
        self.assertEqual(restored.resolve_attachment(rel).read_bytes(), b'fixture')
        with self.assertRaises(ValueError):
            Store.restore_backup(archive, self.root / 'restored')
        malicious = self.root / 'bad.zip'
        with zipfile.ZipFile(malicious, 'w') as z:
            z.writestr('companion.sqlite3', b'bad')
            z.writestr('../escape', b'bad')
        with self.assertRaises(ValueError):
            Store.restore_backup(malicious, self.root / 'bad-restore')
        self.assertFalse((self.root / 'escape').exists())

    def test_settings_migration_preserves_original_and_checks_target(self):
        settings = Settings(self.root / 'config.json')
        settings.set('data_dir', str(self.db.data_dir))
        new_dir = self.root / 'new-data'
        moved = settings.migrate_data(new_dir)
        self.assertEqual(moved.get_persona(self.a)['name'], '甲')
        self.assertEqual(settings.data_dir, new_dir.resolve())
        self.assertTrue((self.db.data_dir / 'companion.sqlite3').exists())
        self.assertEqual(Settings(self.root / 'config.json').data_dir, new_dir.resolve())
        with self.assertRaises(ValueError):
            settings.migrate_data(self.db.data_dir)
        self.assertEqual(settings.data_dir, new_dir.resolve())


if __name__ == '__main__':
    unittest.main()
