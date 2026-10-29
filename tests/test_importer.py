import tempfile
import unittest
import zipfile
from pathlib import Path

from companion.importer import copy_import, generate_style_profile, inspect_file, iter_records


class ImporterTests(unittest.TestCase):
    def test_style_profile_samples_only_confirmed_speaker(self):
        records = [
            {'sender': 'Role', 'text': '哈哈，晚安！'},
            {'sender': 'Role', 'text': '嗯，好呀？'},
            {'sender': 'Me', 'text': 'UNIQUE USER MESSAGE'},
            {'sender': 'Role', 'text': '![image](photo.png)'},
        ]
        profile = generate_style_profile(records, 'Role')
        self.assertIn('2 条文字消息', profile)
        self.assertIn('晚安', profile)
        self.assertNotIn('UNIQUE USER MESSAGE', profile)
        self.assertNotIn('photo.png', profile)
        self.assertEqual(profile, generate_style_profile(iter(records), 'Role'))

    def test_markdown_mapping_and_missing_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media").mkdir()
            (root / "media" / "a.png").write_bytes(b"image")
            source = root / "chat.md"
            source.write_text("# header\n\n## 2025\\-08\\-23 08:47:19 A\\. B\n"
                              "Hi ![x](media/a.png) [web](https://example.com)\n\n"
                              "## 2025-08-24 09:00:00 You\n![x](media/missing.wav)\n", encoding="utf-8")
            info = inspect_file(source)
            self.assertEqual(info["message_count"], 2)
            self.assertEqual(info["senders"], {"A. B": 1, "You": 1})
            self.assertEqual(info["missing"], ["media/missing.wav"])
            records = list(iter_records(source))
            self.assertEqual(records[0]["timestamp"], "2025-08-23 08:47:19")
            result = copy_import(source, root / "destination")
            self.assertEqual(result["copied"], 2)
            self.assertEqual((Path(result["source"]).parent / "media" / "a.png").read_bytes(), b"image")
            self.assertEqual(copy_import(source, root / "destination")["skipped"], 2)
            self.assertTrue(source.exists())

    def test_path_traversal_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").mkdir()
            source = root / "source" / "notes.md"
            source.write_text("[private](../secret.txt)", encoding="utf-8")
            (root / "secret.txt").write_text("secret", encoding="utf-8")
            self.assertEqual(inspect_file(source)["missing"], ["../secret.txt"])
            result = copy_import(source, root / "destination", cancelled=lambda: True)
            self.assertTrue(result["cancelled"])
            self.assertFalse(Path(result["source"]).exists())

    def test_word_and_other_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "note.docx"
            with zipfile.ZipFile(source, "w") as document:
                document.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>')
            self.assertEqual(list(iter_records(source))[0]["text"], "Hello")
            media = root / "voice.wav"
            media.write_bytes(b"audio")
            self.assertEqual(list(iter_records(media))[0]["attachments"][0]["path"], "voice.wav")
            with self.assertRaisesRegex(ValueError, "docx"):
                list(iter_records(root / "old.doc"))


if __name__ == "__main__":
    unittest.main()
