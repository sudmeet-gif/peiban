import json
import tempfile
import unittest
from pathlib import Path

from companion.evolution import maybe_evolve
from companion.storage import Store


class StubModel:
    def __init__(self, answer, hook=None):
        self.answer = answer
        self.hook = hook

    def stream_chat(self, messages, cancel_event=None):
        if self.hook:
            self.hook()
        yield json.dumps(self.answer, ensure_ascii=False) if isinstance(self.answer, dict) else self.answer


class EvolutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        self.persona = self.store.create_persona('角色', profile='温柔')['id']
        self.other = self.store.create_persona('其他')['id']
        self.user_id = self.store.add_message(self.persona, 'user', '我喜欢下雨的时候喝茶')
        self.answer_id = self.store.add_message(self.persona, 'assistant', '那我以后陪你喝茶。')
        self.value = {
            'memories': [
                {'content': '喜欢雨天喝茶', 'evidence_message_id': self.user_id, 'evidence_quote': '下雨的时候喝茶'},
                {'content': '虚构的记忆', 'evidence_message_id': self.user_id, 'evidence_quote': '不存在的证据'},
                {'content': '不要引用角色说的话', 'evidence_message_id': self.answer_id, 'evidence_quote': '陪你喝茶'},
            ],
            'style': {'note': '在下雨时可以温柔地提起喝茶', 'evidence_message_id': self.answer_id, 'evidence_quote': '陪你喝茶'},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_evidence_role_isolation_and_single_application(self):
        result = maybe_evolve(self.store, StubModel(self.value), self.persona, every_n=1)
        self.assertEqual(result, {'ran': True, 'memories_added': 1, 'profile_changed': True})
        self.assertEqual(self.store.list_memories(self.persona)[0]['content'], '喜欢雨天喝茶')
        self.assertEqual(self.store.list_memories(self.other), [])
        self.assertIn('auto:message:', self.store.list_memories(self.persona)[0]['origin'])
        self.assertEqual(len(self.store.list_profile_versions(self.persona)), 2)
        self.assertFalse(maybe_evolve(self.store, StubModel(self.value), self.persona, every_n=1)['ran'])

    def test_bad_response_releases_batch_for_retry(self):
        with self.assertRaises(ValueError):
            maybe_evolve(self.store, StubModel('not json'), self.persona, every_n=1)
        self.assertTrue(maybe_evolve(self.store, StubModel(self.value), self.persona, every_n=1)['ran'])

    def test_manual_edit_during_inference_is_preserved(self):
        def edit():
            self.store.save_profile(self.persona, '手动新设定')
        result = maybe_evolve(self.store, StubModel(self.value, edit), self.persona, every_n=1)
        self.assertFalse(result['profile_changed'])
        self.assertEqual(self.store.get_persona(self.persona)['profile'], '手动新设定')


if __name__ == '__main__':
    unittest.main()
