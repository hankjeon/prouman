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
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from allocation.gui import MainWindow, WorkspaceWindow
from allocation import storage
from allocation.document import (ProjectList, ProjectEntry, YearEntry, PeopleDoc, PersonEntry,
                                 PlanDoc, PlanPersonEntry, dump_projects, dump_people, dump_plan, read_plan)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class PlanningAutosaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.root = root
        self.project = root / 'projects.yaml'
        self.plans = [root / f'plan_{year}.yaml' for year in (2026, 2027)]
        projects = ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2027-12-31',
                               [YearEntry('2026', '2'), YearEntry('2027', '2')]),
                                ProjectEntry('추가 사업', 'B', '2026-01-01', '2027-12-31',
                               [YearEntry('2026', '2'), YearEntry('2027', '2')])])
        self.project.write_text(dump_projects(projects), encoding='utf-8')
        for path, year in zip(self.plans, ('2026', '2027')):
            doc = PlanDoc(year, [PlanPersonEntry('홍길동', {'A': '0.7'}, {'A': '1'}, {'A': '1'},
                                                 metadata={'code': '001'})], metadata={'note': '보존'})
            path.write_text(dump_plan(doc), encoding='utf-8')
        self.window = WorkspaceWindow(QSettings(str(root / 'settings.ini'), QSettings.IniFormat), planning=True)
        self.warnings = []
        self.window.warn = lambda *args: self.warnings.append(args)

    def open(self):
        self.assertEqual(self.window.import_files([str(self.project), *map(str, self.plans)]), ([], []))
        self.window.select_year('2026')
        self.assertIsNotNone(self.window._autosave)
        return self.window

    def contents(self):
        return [path.read_bytes() for path in [self.project, *self.plans]]

    def test_import_navigation_no_write_or_recalculation(self):
        paths = [self.project, *self.plans]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        w = self.open()
        w.show_page(w.RESULT_PAGE)
        w.select_year('2027')
        w.select_year('2026')
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths])
        self.assertFalse(list(self.root.glob('*.bak')))
        self.assertEqual(w._autosave.history.cursor, 0)
        self.assertEqual(w.plan_docs['2026'].people[0].allocations, {'A': '1'})

    def test_main_window_mode_switch_does_not_write(self):
        window = MainWindow(QSettings(str(self.root / 'main.ini'), QSettings.IniFormat))
        window.plan.warn = lambda *args: self.warnings.append(args)
        self.assertEqual(window.plan.import_files([str(self.project), *map(str, self.plans)]), ([], []))
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in [self.project, *self.plans]]
        window.set_mode(window.PLANNING)
        window.plan.show_page(window.plan.RESULT_PAGE)
        window.set_mode(window.ACTUAL)
        window.set_mode(window.PLANNING)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in [self.project, *self.plans]])
        self.assertEqual(window.plan._autosave.history.cursor, 0)
        self.assertEqual(window.plan.plan_docs['2026'].people[0].allocations, {'A': '1'})

    def test_edit_input_matrix_and_empty_error_roundtrip(self):
        w = self.open()
        page = w.planning_page
        page.priorities.item(0, 1).setText('0.8')
        page.priorities.item(0, 2).setText('2')
        page.people_list.item(0).setText('이름 변경')
        page.add_absence()
        page.absences.item(0, 0).setText('2026-02-01')
        page.absences.item(0, 1).setText('2026-02-10')
        page.absences.item(0, 2).setText('휴직')
        page.add_project()
        self.assertTrue(w.flush_autosave())
        doc = read_plan(self.plans[0].read_text(encoding='utf-8'))
        p = doc.people[0]
        self.assertEqual((p.name, p.draft['A'], p.priorities['A'], p.absences[0].reason), ('이름 변경', '0.8', '2', '휴직'))
        self.assertIn('B', p.draft)
        page.matrix.item(0, 1).setText('')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(read_plan(self.plans[0].read_text(encoding='utf-8')).people[0].allocations['A'], '')
        self.assertFalse(page.confirm_button.isEnabled())
        self.assertFalse(w.confirm_plan(str(self.root / 'export')))
        self.assertTrue(self.warnings)

    def test_generate_and_cancel(self):
        w = self.open()
        page = w.planning_page
        before = self.contents()
        with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QMessageBox.No):
            self.assertFalse(page.generate())
        self.assertEqual(before, self.contents())
        self.assertEqual(w._autosave.history.cursor, 0)
        with patch('PySide6.QtWidgets.QMessageBox.question', return_value=QMessageBox.Yes):
            page.generate()
        self.assertTrue(w.flush_autosave())
        doc = read_plan(self.plans[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[0].draft, {'A': '0.7'})
        self.assertEqual(doc.people[0].priorities, {'A': '1'})
        self.assertNotEqual(doc.people[0].allocations, {'A': '1'})
        self.assertEqual(w._autosave.history.cursor, 1)

    def test_alias_bundle_and_failure_restores(self):
        w = self.open()
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.project_editor.table.item(0, 1).setText('C')
            self.assertTrue(w.flush_autosave())
            self.assertEqual(write.call_count, 1)
            self.assertEqual(len(write.call_args.args[0]), 3)
        for path in self.plans:
            person = read_plan(path.read_text(encoding='utf-8')).people[0]
            self.assertEqual((list(person.draft), list(person.priorities), list(person.allocations)), (['C'], ['C'], ['C']))
        before, cursor = self.contents(), w._autosave.history.cursor
        real, calls = os.replace, []
        def fail_second(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('injected')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            w.project_editor.table.item(0, 1).setText('D')
            self.assertFalse(w.flush_autosave())
        self.assertEqual(self.contents(), before)
        self.assertEqual(w.project_editor.table.item(0, 1).text(), 'C')
        self.assertEqual(w.plan_docs['2026'].people[0].draft, {'C': '0.7'})
        self.assertEqual(w._autosave.history.cursor, cursor)

    def test_external_change_and_cross_workspace_independence(self):
        w = self.open()
        people = self.root / 'people.yaml'
        people.write_text(dump_people(PeopleDoc('2026', [PersonEntry('가', {'A': '1'})])), encoding='utf-8')
        actual = WorkspaceWindow(QSettings(str(self.root / 'actual.ini'), QSettings.IniFormat))
        actual.warn = lambda *args: None
        self.assertEqual(actual.import_files([str(self.project), str(people)])[0], [])
        actual.select_year('2026')
        original_people = people.read_bytes()
        actual.project_editor.table.item(0, 0).setText('외부 변경')
        self.assertTrue(actual.flush_autosave())
        actual_cursor = actual._autosave.history.cursor
        w.project_editor.table.item(0, 0).setText('계획 변경')
        self.assertFalse(w.flush_autosave())
        self.assertEqual(w.project_editor.table.item(0, 0).text(), '사업')
        self.assertEqual(people.read_bytes(), original_people)
        self.assertEqual(actual._autosave.history.cursor, actual_cursor)
        self.assertTrue(self.warnings)

    def test_noop_plan_and_alias_keep_redo(self):
        w = self.open()
        w.planning_page.priorities.item(0, 1).setText('0.8')
        self.assertTrue(w.flush_autosave())
        self.assertTrue(w._autosave.move(-1))
        w._restore_autosave(w._autosave.state, False, {'2026'})
        paths = [self.project, *self.plans]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.planning_page.priorities.item(0, 1).setText('0.9')
            w.planning_page.priorities.item(0, 1).setText('0.7')
            w.project_editor.table.item(0, 1).setText('C')
            w.project_editor.table.item(0, 1).setText('A')
            self.assertTrue(w.flush_autosave())
            self.assertEqual(write.call_count, 0)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths])
        self.assertFalse(any(w.plan_dirty.values()))
        self.assertFalse(w.projects_dirty)
        self.assertEqual(w._autosave.history.cursor, 0)
        self.assertEqual(w._autosave.history.redo_labels(), ['계획·사람 입력 — 참고 점수 수정'])
        w.ask_export = lambda _: self.fail('무변경 저장 확인이 없어야 합니다')
        self.assertTrue(w.keep_all())

    def test_unsupported_input_and_encoding_cancellation(self):
        content = self.plans[0].read_text(encoding='utf-8') + '\nunknown_field: kept\n'
        self.plans[0].write_text(content, encoding='utf-8')
        errors, _ = self.window.import_files([str(self.project), *map(str, self.plans)])
        self.assertTrue(any('지원하지 않는' in error for error in errors))
        self.assertEqual(self.plans[0].read_text(encoding='utf-8'), content)
        # 별도 계획 원본은 CP949에서 표현할 수 없는 변경을 취소해 보존한다.
        path = self.plans[1]
        path.write_bytes(path.read_text(encoding='utf-8').encode('cp949'))
        other = WorkspaceWindow(QSettings(str(self.root / 'other.ini'), QSettings.IniFormat), planning=True)
        other.warn = lambda *args: self.warnings.append(args)
        self.assertEqual(other.import_files([str(self.project), str(path)])[0], [])
        other.select_year('2027')
        before = path.read_bytes()
        other.confirm = lambda *args: False
        other.planning_page.people_list.item(0).setText('홍길동😀')
        self.assertFalse(other.flush_autosave())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(other.planning_page.people_list.item(0).text(), '홍길동')

    def test_person_project_add_remove(self):
        w = self.open()
        page = w.planning_page
        page.add_person()
        self.assertTrue(w.flush_autosave())
        page.people_list.currentItem().setText('새 이름')
        page.add_project()
        self.assertTrue(w.flush_autosave())
        doc = read_plan(self.plans[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[-1].name, '새 이름')
        self.assertTrue(doc.people[-1].draft)
        page.priorities.setCurrentCell(0, 0)
        page.remove_project()
        self.assertTrue(w.flush_autosave())
        self.assertEqual(read_plan(self.plans[0].read_text(encoding='utf-8')).people[-1].draft, {})
        page.remove_person()
        self.assertTrue(w.flush_autosave())
        self.assertEqual(len(read_plan(self.plans[0].read_text(encoding='utf-8')).people), 1)

    def test_external_plan_conflict_restores_matrix(self):
        w = self.open()
        before = self.plans[0].read_bytes() + b'\n# external\n'
        self.plans[0].write_bytes(before)
        page = w.planning_page
        page.matrix.setCurrentCell(0, 1)
        page.matrix.item(0, 1).setText('0.5')
        self.assertFalse(w.flush_autosave())
        self.assertEqual(self.plans[0].read_bytes(), before)
        self.assertEqual(w.plan_docs['2026'].people[0].allocations['A'], '1')
        self.assertEqual(page.matrix.item(0, 1).text(), '1')
        self.assertEqual(page.matrix.currentRow(), 0)
        self.assertEqual(page.matrix.currentColumn(), 1)

    def test_empty_draft_preserved_as_error(self):
        w = self.open()
        w.planning_page.priorities.item(0, 1).setText('')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(read_plan(self.plans[0].read_text(encoding='utf-8')).people[0].draft['A'], '')
        self.assertFalse(w.planning_page.confirm_button.isEnabled())

    def test_project_delete_removes_all_plan_references_in_one_bundle(self):
        w = self.open()
        w.confirm = lambda *args: True
        w.project_editor.table.setCurrentCell(0, 0)
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.project_editor.remove_project()
            self.assertTrue(w.flush_autosave())
            self.assertEqual(write.call_count, 1)
            self.assertEqual(len(write.call_args.args[0]), 3)
        for path in self.plans:
            person = read_plan(path.read_text(encoding='utf-8')).people[0]
            self.assertNotIn('A', person.draft)
            self.assertNotIn('A', person.priorities)
            self.assertNotIn('A', person.allocations)

    def test_new_unbound_plan_keeps_work_without_creating_files(self):
        w = self.open()
        before = self.contents()
        people = PeopleDoc('2026', [PersonEntry('새 사람', {'A': '1'})])
        self.assertTrue(w.prepare_plan(people, '2028'))
        self.assertIsNotNone(w._autosave)
        w.choose_source_path = lambda *args: ''
        w.planning_page.priorities.item(0, 1).setText('0.8')
        self.app.processEvents()
        self.assertEqual(self.contents(), before)
        self.assertEqual(w.plan_docs['2028'].people[0].draft['A'], '0.8')
        self.assertTrue(w.plan_dirty['2028'])
        self.assertFalse((self.root / 'plan_2028.yaml').exists())

    def test_recovery_failure_allows_only_confirmed_exit(self):
        from PySide6.QtGui import QCloseEvent
        w = self.open()
        real, calls = os.replace, []
        def fail_after_first(src, dst):
            calls.append(dst)
            if len(calls) >= 2:
                raise OSError('persistent')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_after_first):
            w.project_editor.table.item(0, 1).setText('C')
            self.assertFalse(w.flush_autosave())
        self.assertTrue(w._autosave_blocked)
        self.assertTrue(list(self.root.glob('.prouman-restore-*')))
        self.assertIn('복구 파일', self.warnings[-1][1])
        w.confirm = lambda *args: False
        event = QCloseEvent()
        w.closeEvent(event)
        self.assertFalse(event.isAccepted())
        w.confirm = lambda *args: True
        event = QCloseEvent()
        w.closeEvent(event)
        self.assertTrue(event.isAccepted())


if __name__ == '__main__':
    unittest.main()
