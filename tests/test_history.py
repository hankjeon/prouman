import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from allocation.history import History


class HistoryTests(unittest.TestCase):
    def test_multi_step_is_one_apply_and_failure_keeps_cursor(self):
        history = History()
        for i in range(4):
            history.record(str(i), {'value': i}, {'value': i + 1})
        calls = []
        self.assertTrue(history.move(-3, lambda state: calls.append(state) or True))
        self.assertEqual(calls, [{'value': 1}])
        self.assertEqual(history.cursor, 1)
        self.assertFalse(history.move(2, lambda state: False))
        self.assertEqual(history.cursor, 1)
        self.assertEqual(history.redo_labels(), ['1', '2', '3'])
        self.assertTrue(history.move(2, lambda state: calls.append(state) or True))
        self.assertEqual(calls[-1], {'value': 3})

    def test_new_success_discards_redo_and_noop_does_not(self):
        history = History()
        history.record('one', 0, 1)
        history.record('two', 1, 2)
        history.move(-1, lambda _: True)
        self.assertFalse(history.record('no change', 1, 1))
        self.assertEqual(history.redo_labels(), ['two'])
        history.record('new', 1, 4)
        self.assertEqual(history.redo_labels(), [])
        self.assertEqual(history.undo_labels(), ['new', 'one'])
