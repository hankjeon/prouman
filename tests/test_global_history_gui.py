import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if importlib.util.find_spec('PySide6') is None:
    QApplication = None
else:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox
    from allocation.gui import MainWindow
from allocation import storage
from allocation.document import (ProjectList, ProjectEntry, YearEntry, PeopleDoc, PersonEntry,
                                 PlanDoc, PlanPersonEntry, dump_projects, dump_people, dump_plan, read_people, read_plan)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class GlobalHistoryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.actual_dir = self.root / 'actual'
        self.plan_dir = self.root / 'plan'
        self.actual_dir.mkdir()
        self.plan_dir.mkdir()
        project = ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2027-12-31',
                                 [YearEntry('2026', '2'), YearEntry('2027', '2')])])
        for directory in (self.actual_dir, self.plan_dir):
            (directory / 'projects.yaml').write_text(dump_projects(project), encoding='utf-8')
        self.people = [self.actual_dir / f'people_{y}.yaml' for y in (2026, 2027)]
        for path, year in zip(self.people, ('2026', '2027')):
            path.write_text(dump_people(PeopleDoc(year, [PersonEntry('가', {'A': '1'})])), encoding='utf-8')
        self.plan = self.plan_dir / 'plan_2026.yaml'
        self.plan.write_text(dump_plan(PlanDoc('2026', [PlanPersonEntry('나', {'A': '0.7'}, {}, {'A': '1'})])), encoding='utf-8')
        self.window = MainWindow(QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat))
        self.warnings = []
        for workspace in self.window.workspaces:
            workspace.warn = lambda *args: self.warnings.append(args)
        self.assertEqual(self.window.actual.import_files([str(self.actual_dir / 'projects.yaml'), *map(str, self.people)]), ([], []))
        self.assertEqual(self.window.plan.import_files([str(self.plan_dir / 'projects.yaml'), str(self.plan)]), ([], []))
        self.window.actual.select_year('2026')
        self.window.plan.select_year('2026')
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)

    def change_actual(self, year, value):
        self.window.set_mode(self.window.ACTUAL)
        w = self.window.actual
        w.select_year(year)
        w.people_editor.allocations.item(0, 1).setText(value)
        self.assertTrue(w.flush_autosave())

    def change_plan(self, value):
        self.window.set_mode(self.window.PLANNING)
        w = self.window.plan
        w.planning_page.priorities.item(0, 1).setText(value)
        self.assertTrue(w.flush_autosave())

    def files(self):
        return [p.read_bytes() for p in [*self.people, self.plan]]

    def test_shortcuts_and_native_editor_undo(self):
        self.change_actual('2026', '0.9')
        w = self.window
        w.actual.people_editor.allocations.setFocus()
        QTest.keyClick(w.actual.people_editor.allocations, Qt.Key_Z, Qt.ControlModifier)
        self.app.processEvents()
        self.assertEqual(read_people(self.people[0].read_text(encoding='utf-8'))[0].people[0].allocations['A'], '1')
        QTest.keyClick(w.actual.people_editor.allocations, Qt.Key_Y, Qt.ControlModifier)
        self.app.processEvents()
        self.assertEqual(read_people(self.people[0].read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.9')
        cursor = w.history.cursor
        table = w.actual.people_editor.allocations
        table.setCurrentCell(0, 1)
        table.editItem(table.item(0, 1))
        self.app.processEvents()
        editor = QApplication.focusWidget()
        self.assertIsInstance(editor, QLineEdit)
        editor.selectAll()
        QTest.keyClicks(editor, '0.8')
        QTest.keyClick(editor, Qt.Key_Z, Qt.ControlModifier)
        self.app.processEvents()
        self.assertEqual(w.history.cursor, cursor)
        self.assertEqual(editor.text(), '0.9')

    def test_cross_mode_multistep_one_bundle_and_redo(self):
        self.change_actual('2026', '0.9')
        self.change_plan('0.8')
        self.change_actual('2027', '0.6')
        w = self.window
        self.assertEqual(w.history.cursor, 3)
        with patch('allocation.global_history.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.populate_history_menu(False)
            w.undo_menu.actions()[2].trigger()
            self.assertEqual(write.call_count, 1)
            self.assertEqual(len(write.call_args.args[0]), 3)
        self.assertEqual(w.history.cursor, 0)
        self.assertEqual(w.mode_stack.currentIndex(), w.ACTUAL)
        self.assertEqual(w.actual.current_year, '2026')
        self.assertEqual(read_people(self.people[0].read_text(encoding='utf-8'))[0].people[0].allocations['A'], '1')
        self.assertEqual(read_people(self.people[1].read_text(encoding='utf-8'))[0].people[0].allocations['A'], '1')
        self.assertEqual(read_plan(self.plan.read_text(encoding='utf-8')).people[0].draft['A'], '0.7')
        self.assertTrue(w.move_history(3))
        self.assertEqual(w.history.cursor, 3)

    def test_cross_mode_failure_and_external_conflict_are_atomic(self):
        self.change_actual('2026', '0.9')
        self.change_plan('0.8')
        self.change_actual('2027', '0.6')
        w = self.window
        files, cursors = self.files(), [ws.autosave_session.history.cursor for ws in w.workspaces]
        states = [ws.autosave_session.state for ws in w.workspaces]
        real, calls = os.replace, []
        def fail_second(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('failure')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            self.assertFalse(w.move_history(-3))
        self.assertEqual(self.files(), files)
        self.assertEqual([ws.autosave_session.history.cursor for ws in w.workspaces], cursors)
        self.assertEqual([ws.autosave_session.state for ws in w.workspaces], states)
        self.assertEqual(w.history.cursor, 3)
        self.plan.write_bytes(self.plan.read_bytes() + b'\n# external\n')
        files = self.files()
        self.assertFalse(w.move_history(-3))
        self.assertEqual(self.files(), files)
        self.assertEqual(w.history.cursor, 3)

    def test_long_history_more_stale_menu_and_rebind(self):
        for i in range(25):
            self.change_actual('2026', f'0.{i:02d}')
        w = self.window
        self.assertEqual(len(w.history.entries), 25)
        w.populate_history_menu(False)
        self.assertEqual(len(w.undo_menu.actions()), 21)
        self.assertTrue(all(len(action.text()) < 80 for action in w.undo_menu.actions()))
        stale = w.undo_menu.actions()[0]
        self.change_plan('0.8')
        stale.trigger()
        self.assertEqual(w.history.cursor, 26)
        w.populate_history_menu(False)
        with patch('PySide6.QtWidgets.QInputDialog.getItem', return_value=('25. 선택한 작업', True)) as dialog:
            w.undo_menu.actions()[-1].trigger()
            self.assertEqual(len(dialog.call_args.args[3]), 26)
        self.assertEqual(w.history.cursor, 1)
        w.actual._bind_autosave()
        self.assertEqual(w.history.cursor, 0)
        self.assertEqual(w.history.entries, [])
        self.assertFalse(w.move_history(-1))

    def test_success_branch_only_discards_redo_and_noop_preserves(self):
        self.change_actual('2026', '0.9')
        self.change_plan('0.8')
        w = self.window
        self.assertTrue(w.move_history(-1))
        self.assertEqual(w.history.labels(True), ['계획·사람 입력 — 참고 점수 수정'])
        actual = w.actual
        actual.people_editor.allocations.item(0, 1).setText('0.8')
        actual.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertTrue(actual.flush_autosave())
        self.assertEqual(len(w.history.labels(True)), 1)
        with patch('allocation.autosave.storage.write_bundle', side_effect=OSError('fail')):
            actual.people_editor.allocations.item(0, 1).setText('0.7')
            self.assertFalse(actual.flush_autosave())
        self.assertEqual(len(w.history.labels(True)), 1)
        self.change_actual('2026', '0.6')
        self.assertEqual(w.history.labels(True), [])
        self.assertEqual(w.plan.autosave_session.history.redo_labels(), [])

    def test_paste_alias_pin_and_plan_calculation_are_single_entries(self):
        w = self.window
        actual = w.actual
        table = actual.people_editor.allocations
        table.setCurrentCell(0, 1)
        table.paste('0.9')
        self.assertTrue(actual.flush_autosave())
        self.assertEqual(w.history.cursor, 1)
        actual.project_editor.table.item(0, 1).setText('C')
        self.assertTrue(actual.flush_autosave())
        self.assertEqual(w.history.cursor, 2)
        self.assertIn('약칭 변경', w.history.entries[-1].label)
        table = actual.people_editor.allocations
        table.item(0, 1).setText('1')
        self.assertTrue(actual.flush_autosave())
        actual.show_page(actual.RESULT_PAGE)
        self.assertTrue(actual.toggle_person_pin('가'))
        self.assertEqual(w.history.cursor, 4)
        self.assertIn('개인상세 — 월별 고정', w.history.entries[-1].label)
        w.set_mode(w.PLANNING)
        w.plan.show_page(w.plan.RESULT_PAGE)
        with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QMessageBox.Yes):
            w.plan.planning_page.generate()
        self.assertTrue(w.plan.flush_autosave())
        self.assertEqual(w.history.cursor, 5)
        self.assertIn('계획·결과 — 재계산', w.history.entries[-1].label)

    def test_new_window_has_no_history_and_keeps_file(self):
        self.change_actual('2026', '0.9')
        self.assertEqual(self.window.history.cursor, 1)
        other = MainWindow(QSettings(str(self.root / 'fresh.ini'), QSettings.IniFormat))
        self.addCleanup(other.deleteLater)
        self.assertEqual(other.actual.import_files([str(self.actual_dir / 'projects.yaml'), str(self.people[0])]), ([], []))
        self.assertEqual(other.history.entries, [])
        self.assertEqual(read_people(self.people[0].read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.9')

    def test_new_or_close_invalidates_old_targets(self):
        self.change_actual('2026', '0.9')
        before = self.files()
        self.window.actual.close_year('2027')
        self.assertEqual(self.window.history.entries, [])
        self.assertFalse(self.window.move_history(-1))
        self.assertEqual(self.files(), before)
        self.change_actual('2026', '0.8')
        before = self.files()
        self.window.actual.new_all()
        self.assertEqual(self.window.history.entries, [])
        self.assertFalse(self.window.move_history(-1))
        self.assertEqual(self.files(), before)

    def test_plan_copy_keeps_history_and_explicit_rebind_clears_it(self):
        self.change_plan('0.8')
        before = self.plan.read_bytes()
        destination = self.root / 'new-plan'
        self.assertTrue(self.window.plan.save_plan(str(destination)))
        self.assertEqual(self.window.history.cursor, 1)
        self.assertEqual(self.window.plan.plan_sources['2026'], str(self.plan))
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertTrue((destination / 'plans' / 'plan_2026.yaml').exists())
        self.window.plan._bind_autosave()
        self.assertEqual(self.window.history.entries, [])
        self.assertFalse(self.window.move_history(-1))

    def test_shared_source_conflicting_session_fingerprints_are_rejected(self):
        w = self.window
        w.plan.new_all()
        self.assertEqual(w.plan.import_files([str(self.actual_dir / 'projects.yaml'), str(self.plan)]), ([], []))
        w.plan.select_year('2026')
        w.set_mode(w.ACTUAL)
        w.actual.project_editor.table.item(0, 0).setText('바뀐 이름')
        self.assertTrue(w.actual.flush_autosave())
        w.set_mode(w.PLANNING)
        w.plan.planning_page.priorities.item(0, 1).setText('0.8')
        self.assertFalse(w.plan.flush_autosave())
        before = self.files()
        before_project = (self.actual_dir / 'projects.yaml').read_bytes()
        self.assertEqual(self.files(), before)
        self.assertEqual((self.actual_dir / 'projects.yaml').read_bytes(), before_project)
        self.assertEqual(w.history.cursor, 1)
        self.assertTrue(self.warnings)

    def test_global_recovery_failure_blocks_both_workspaces(self):
        self.change_actual('2026', '0.9')
        self.change_plan('0.8')
        w = self.window
        real, calls = os.replace, []
        def fail_after_first(src, dst):
            calls.append(dst)
            if len(calls) >= 2:
                raise OSError('persistent')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_after_first):
            self.assertFalse(w.move_history(-2))
        self.assertFalse(w.actual.history_ready())
        self.assertFalse(w.plan.history_ready())
        self.assertTrue(list(self.root.rglob('.prouman-restore-*')))
        self.assertIn('복구 파일', self.warnings[-1][1])
        self.assertEqual(w.history.cursor, 2)

    def test_external_unchanged_dependency_blocks_history(self):
        self.change_actual('2026', '0.9')
        project = self.actual_dir / 'projects.yaml'
        project.write_bytes(project.read_bytes() + b'\n# external\n')
        before = self.files()
        self.assertFalse(self.window.move_history(-1))
        self.assertEqual(self.files(), before)
        self.assertEqual(self.window.history.cursor, 1)
        self.assertTrue(any('외부 변경' in text for _, text in self.warnings))


if __name__ == '__main__':
    unittest.main()
