import tempfile
import unittest
from pathlib import Path
from threading import Event

from companion.media_jobs import confirm_recognition, list_pending, reject_recognition, run_media_jobs
from companion.storage import Store


class FakeModels:
    def __init__(self): self.calls = []
    def describe_image(self, path):
        self.calls.append(('image', path))
        return '图里有一棵树'
    def transcribe(self, path, cancel_event=None):
        self.calls.append(('audio', path))
        return '你好'


class MediaJobsTests(unittest.TestCase):
    def test_recognition_review_dedup_and_role_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'data')
            persona = store.create_persona('one')['id']
            other = store.create_persona('two')['id']
            photo = root / 'image.png'; photo.write_bytes(b'img')
            sound = root / 'sound.wav'; sound.write_bytes(b'wav')
            image_path = store.copy_attachment(persona, photo)
            audio_path = store.copy_attachment(persona, sound)
            source = store.add_source(persona, 'source.md', 'hash')
            store.add_message(persona, 'assistant', media_path=image_path, media_type='image', source_id=source)
            store.add_message(persona, 'user', media_path=audio_path, media_type='audio', source_id=source)
            models = FakeModels(); progress = []
            result = run_media_jobs(store, models, persona, source, progress=lambda *x: progress.append(x))
            self.assertEqual(result['processed'], 2)
            self.assertEqual(len(progress), 3)
            pending = list_pending(store, persona)
            self.assertEqual(len(pending), 2)
            self.assertTrue(all('尚未确认' in x['content'] for x in pending))
            self.assertEqual(list_pending(store, other), [])
            self.assertEqual(store.search_memories(persona, '棵树'), [])
            with self.assertRaises(KeyError):
                confirm_recognition(store, other, pending[0]['memory_id'], 'cannot')
            confirm_recognition(store, persona, pending[0]['memory_id'], '修正后的描述')
            reject_recognition(store, persona, pending[1]['memory_id'])
            self.assertEqual(list_pending(store, persona), [])
            memories = store.list_memories(persona)
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0]['origin'], 'machine-confirmed')
            self.assertIn('修正后的描述', memories[0]['content'])
            self.assertEqual(len(store.search_memories(persona, '修正后的描述')), 1)
            self.assertEqual(memories[0]['source_id'], source)
            self.assertTrue(store.resolve_attachment(audio_path).exists())
            again = run_media_jobs(store, models, persona, source)
            self.assertEqual(again['skipped'], 2)
            self.assertEqual(len(models.calls), 2)

    def test_single_media_source_cancellation_and_failure_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'data')
            persona = store.create_persona('one')['id']
            image = root / 'a.png'; image.write_bytes(b'image')
            rel = store.copy_attachment(persona, image)
            source = store.add_source(persona, 'a.png', 'hash', kind='.png', copied_path=rel)
            stop = Event(); stop.set()
            models = FakeModels()
            result = run_media_jobs(store, models, persona, source, cancel_event=stop)
            self.assertTrue(result['cancelled'])
            self.assertEqual(models.calls, [])
            stop.clear()
            class Broken(FakeModels):
                def describe_image(self, path): raise RuntimeError('模型未配置')
            result = run_media_jobs(store, Broken(), persona, source)
            self.assertEqual(result['failed'], 1)
            self.assertEqual(list_pending(store, persona), [])
            self.assertEqual(run_media_jobs(store, models, persona, source)['processed'], 1)


if __name__ == '__main__': unittest.main()
