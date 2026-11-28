import os
import sys
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from allocation.autosave import TransactionalSession
from allocation import storage
from allocation.document import ProjectList, dump_projects, read_projects


class TransactionalSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = [root / 'projects.yaml', root / 'people_2026.yaml', root / 'people_2027.yaml']
        self.initial = {'values': ['A', 'A', 'A'],
                        'metadata': {'ordered': ['first', 'second'], 'precise': Decimal('0.123456789')}}
        for path, data in self.serialize(self.initial).items():
            path.write_bytes(data)
        self.session = TransactionalSession(self.initial, self.serialize)

    def serialize(self, state):
        return {path: (f'alias: {value}\nmetadata: {state["metadata"]!s}\n').encode('utf-8')
                for path, value in zip(self.paths, state['values'])}

    def changed(self, name):
        state = self.session.state
        state['values'] = [name] * 3
        return state

    def contents(self):
        return [p.read_bytes() if p.exists() else None for p in self.paths]

    def test_initialization_does_not_write(self):
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in self.paths]
        TransactionalSession(self.initial, self.serialize)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in self.paths])
        self.assertFalse(list(Path(self.temp.name).glob('*.bak')))

    def test_alias_change_is_one_bundle_and_noop_is_no_write(self):
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            self.assertTrue(self.session.commit('약칭 변경', self.changed('B')))
            self.assertEqual(write.call_count, 1)
            self.assertEqual(len(write.call_args.args[0]), 3)
            self.assertFalse(self.session.commit('무변경', self.session.state))
            self.assertEqual(write.call_count, 1)
        self.assertEqual(self.contents(), list(self.serialize(self.changed('B')).values()))
        self.assertEqual(self.session.history.cursor, 1)

    def test_second_replace_failure_rolls_back_all_and_session(self):
        before, fingerprints = self.contents(), self.session.fingerprints
        real_replace, calls = os.replace, []
        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError('injected second replacement')
            real_replace(source, destination)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'injected'):
                self.session.commit('변경', self.changed('B'))
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.session.state, self.initial)
        self.assertEqual(self.session.history.cursor, 0)
        self.assertEqual(self.session.fingerprints, fingerprints)

    def test_external_modification_deletion_and_unexpected_creation(self):
        for mode in ('modify', 'delete', 'create'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                paths = [Path(directory) / 'one', Path(directory) / 'two']
                paths[0].write_bytes(b'old')
                if mode != 'create':
                    paths[1].write_bytes(b'old')
                serializer = lambda state: {p: state.encode() for p in paths}
                session = TransactionalSession('old', serializer)
                if mode == 'delete':
                    paths[1].unlink()
                else:
                    paths[1].write_bytes(b'external')
                before = [p.read_bytes() if p.exists() else None for p in paths]
                with self.assertRaisesRegex(OSError, '외부 변경 충돌'):
                    session.commit('change', 'new')
                self.assertEqual([p.read_bytes() if p.exists() else None for p in paths], before)
                self.assertEqual(session.state, 'old')
                self.assertEqual(session.history.cursor, 0)
                self.assertFalse(list(Path(directory).glob('*.bak')))

    def test_invalid_business_value_is_written(self):
        self.session.commit('오류 자료', self.changed('0.9'))
        self.assertIn(b'alias: 0.9', self.paths[0].read_bytes())

    def test_multistep_undo_redo_each_writes_one_final_bundle(self):
        for value in ('B', 'C', 'D'):
            self.session.commit(value, self.changed(value))
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            self.assertTrue(self.session.move(-3))
            self.assertEqual(write.call_count, 1)
            self.assertEqual(self.contents(), list(self.serialize(self.initial).values()))
            self.assertTrue(self.session.move(2))
            self.assertEqual(write.call_count, 2)
            self.assertEqual(self.session.state['values'], ['C'] * 3)
            self.assertEqual(self.contents(), list(self.serialize(self.session.state).values()))

    def test_move_failure_and_redo_branch(self):
        for value in ('B', 'C'):
            self.session.commit(value, self.changed(value))
        for delta in (-1,):
            before, state, fingerprints = self.contents(), self.session.state, self.session.fingerprints
            with patch('allocation.autosave.storage.write_bundle', side_effect=OSError('failure')):
                with self.assertRaises(OSError):
                    self.session.move(delta)
            self.assertEqual((self.contents(), self.session.state, self.session.fingerprints),
                             (before, state, fingerprints))
            self.assertEqual(self.session.history.cursor, 2)
        self.session.move(-1)
        before = self.contents()
        with patch('allocation.autosave.storage.write_bundle', side_effect=OSError('failure')):
            with self.assertRaises(OSError):
                self.session.move(1)
            with self.assertRaises(OSError):
                self.session.commit('failed', self.changed('D'))
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.session.history.cursor, 1)
        self.assertFalse(self.session.commit('noop', self.session.state))
        self.assertEqual(self.session.history.redo_labels(), ['C'])
        self.session.commit('D', self.changed('D'))
        self.assertEqual(self.session.history.redo_labels(), [])

    def test_copies_metadata_precision_and_order(self):
        original = deepcopy(self.initial)
        self.initial['metadata']['ordered'].reverse()
        detached = self.session.state
        detached['metadata']['ordered'].append('intrusion')
        self.assertEqual(self.session.state, original)
        self.session.commit('change', self.changed('B'))
        history = self.session.history
        history.entries[0].before['metadata']['ordered'].append('intrusion')
        self.session.move(-1)
        self.assertEqual(self.session.state, original)
        self.assertEqual(self.session.state['metadata']['precise'], Decimal('0.123456789'))

    def test_unknown_or_missing_path_rejected_without_creation(self):
        extra = Path(self.temp.name) / 'unknown.yaml'
        original = self.session._serialize
        self.session._serialize = lambda state: {**original(state), extra: b'new'}
        with self.assertRaisesRegex(ValueError, '등록되지'):
            self.session.commit('bad', self.changed('B'))
        self.assertFalse(extra.exists())
        self.assertEqual(self.session.history.cursor, 0)

    def test_serializer_and_deepcopy_failure_leave_redo(self):
        self.session.commit('B', self.changed('B'))
        self.session.move(-1)
        before, fingerprints = self.contents(), self.session.fingerprints
        self.session._serialize = lambda _: (_ for _ in ()).throw(UnicodeEncodeError('ascii', '가', 0, 1, 'bad'))
        with self.assertRaises(UnicodeEncodeError):
            self.session.commit('failure', self.changed('C'))
        class BadCopy:
            def __deepcopy__(self, memo):
                raise ValueError('copy failure')
        with self.assertRaisesRegex(ValueError, 'copy failure'):
            self.session.commit('failure', BadCopy())
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.session.fingerprints, fingerprints)
        self.assertEqual(self.session.history.redo_labels(), ['B'])

    def test_redo_mid_bundle_failure_restores_every_file(self):
        self.session.commit('B', self.changed('B'))
        self.session.move(-1)
        before, state, fingerprints = self.contents(), self.session.state, self.session.fingerprints
        real_replace, calls = os.replace, []
        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError('redo replacement failure')
            real_replace(source, destination)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'redo replacement failure'):
                self.session.move(1)
        self.assertEqual((self.contents(), self.session.state, self.session.fingerprints),
                         (before, state, fingerprints))
        self.assertEqual(self.session.history.cursor, 0)
        self.assertEqual(self.session.history.redo_labels(), ['B'])

    def test_project_list_metadata_only_roundtrip_and_history(self):
        path = Path(self.temp.name) / 'projects_metadata.yaml'
        original = ProjectList(metadata={'note': 'before'})
        serializer = lambda doc: {path: dump_projects(doc).encode('utf-8')}
        path.write_bytes(serializer(original)[path])
        session = TransactionalSession(original, serializer)
        changed = deepcopy(original)
        changed.metadata['note'] = 'after'
        self.assertEqual(original, changed)  # list equality ignores metadata
        self.assertTrue(session.commit('metadata', changed))
        self.assertEqual(read_projects(path.read_text(encoding='utf-8'))[0].metadata['note'], 'after')
        self.assertTrue(session.move(-1))
        self.assertEqual(read_projects(path.read_text(encoding='utf-8'))[0].metadata['note'], 'before')
        self.assertTrue(session.move(1))
        self.assertEqual(read_projects(path.read_text(encoding='utf-8'))[0].metadata['note'], 'after')

    def test_explicit_creation_of_initially_missing_file_checks_conflict(self):
        path = Path(self.temp.name) / 'chosen.yaml'
        session = TransactionalSession('value', lambda state: {path: state.encode()})
        self.assertFalse(path.exists())
        self.assertFalse(session.commit('unchanged', 'value'))
        self.assertTrue(session.create_missing())
        self.assertEqual(path.read_bytes(), b'value')
        self.assertFalse(session.create_missing())
        other = Path(self.temp.name) / 'other.yaml'
        session = TransactionalSession('value', lambda state: {other: state.encode()})
        other.write_bytes(b'external')
        with self.assertRaisesRegex(OSError, '외부 변경 충돌'):
            session.create_missing()
        self.assertEqual(other.read_bytes(), b'external')

    def test_unchanged_dependency_external_change_blocks_commit(self):
        before = self.contents()
        self.paths[0].write_bytes(b'external project')
        candidate = self.session.state
        candidate['values'][1] = 'B'
        with self.assertRaisesRegex(OSError, '외부 변경 충돌'):
            self.session.commit('person', candidate)
        self.assertEqual(self.paths[0].read_bytes(), b'external project')
        self.assertEqual(self.paths[1].read_bytes(), before[1])
        self.assertEqual(self.session.history.cursor, 0)


if __name__ == '__main__':
    unittest.main()
