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
    from PySide6.QtWidgets import QApplication
    from allocation.gui import WorkspaceWindow
from allocation import storage
from allocation.document import (ProjectList, ProjectEntry, YearEntry, PeopleDoc, PersonEntry,
                                 dump_projects, dump_people, read_projects, read_people)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class AutosaveGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'projects.yaml'
        self.people = [self.root / f'people_{year}.yaml' for year in (2026, 2027)]
        projects = ProjectList([ProjectEntry('첫 사업', 'A', '2026-01-01', '2027-12-31',
                            [YearEntry('2026', '2'), YearEntry('2027', '2')]),
                                ProjectEntry('둘 사업', 'B', '2026-01-01', '2027-12-31',
                            [YearEntry('2026', '2'), YearEntry('2027', '2')])], metadata={'note': '보존'})
        self.project.write_text(dump_projects(projects), encoding='utf-8')
        for path, year in zip(self.people, (2026, 2027)):
            doc = PeopleDoc(str(year), [PersonEntry('홍길동', {'A': '1'}, metadata={'code': '001'}),
                                        PersonEntry('김철수', {'B': '1'})])
            path.write_text(dump_people(doc), encoding='utf-8')
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        self.window = WorkspaceWindow(settings)
        self.window.warn = lambda *args: self.warnings.append(args)
        self.warnings = []

    def open(self):
        result = self.window.import_files([str(self.project), *map(str, self.people)])
        self.assertEqual(result, ([], []))
        self.window.select_year('2026')
        return self.window

    def contents(self):
        return [path.read_bytes() for path in (self.project, *self.people)]

    def test_import_and_rate_save(self):
        paths = [self.project, *self.people]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        w = self.open()
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths])
        self.assertFalse(list(self.root.glob('*.bak')))
        self.assertEqual(w._autosave.history.cursor, 0)
        w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertIn('⚠', w.people_editor.totals.text())
        self.app.processEvents()
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[0].allocations['A'], '0.9')
        self.assertFalse(w.people_dirty['2026'])
        self.assertNotIn('*', w.pages.tabText(w.INPUT_PAGE))
        w.ask_export = lambda _: self.fail('저장 질문이 없어야 합니다')
        self.assertTrue(w.keep_all())

    def test_empty_rate_and_project_delete_keep_metadata(self):
        w = self.open()
        w.people_editor.allocations.item(0, 1).setText('')
        self.assertTrue(w.flush_autosave())
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[0].allocations['A'], '')
        self.assertIn('참여율', w.people_editor.allocations.item(0, 1).toolTip())
        w.confirm = lambda *args: True
        w.project_editor.table.setCurrentCell(0, 0)
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.project_editor.remove_project()
            self.assertTrue(w.flush_autosave())
            self.assertEqual(write.call_count, 1)
            self.assertEqual(len(write.call_args.args[0]), 3)
        for path in self.people:
            doc, _ = read_people(path.read_text(encoding='utf-8'))
            self.assertNotIn('A', doc.people[0].allocations)
            self.assertEqual(doc.people[0].metadata, {'code': '001'})
        projects, _ = read_projects(self.project.read_text(encoding='utf-8'))
        self.assertEqual(projects.metadata, {'note': '보존'})

    def test_project_and_people_order_saved(self):
        w = self.open()
        w.project_editor.table.item(0, 0).setText('새 이름')
        w.project_editor.reorder_project_rows([1, 0])
        w.people_editor.reorder_people(['김철수', '홍길동'])
        self.assertTrue(w.flush_autosave())
        projects, _ = read_projects(self.project.read_text(encoding='utf-8'))
        people, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual([(p.alias, p.name) for p in projects], [('B', '둘 사업'), ('A', '새 이름')])
        self.assertEqual([p.name for p in people.people], ['김철수', '홍길동'])

    def test_alias_one_bundle_and_failure_restores(self):
        w = self.open()
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.project_editor.table.item(0, 1).setText('C')
            self.assertTrue(w.flush_autosave())
            self.assertEqual(write.call_count, 1)
            self.assertEqual(len(write.call_args.args[0]), 3)
        for path in self.people:
            doc, _ = read_people(path.read_text(encoding='utf-8'))
            self.assertIn('C', doc.people[0].allocations)
        before, cursor = self.contents(), w._autosave.history.cursor
        real_replace, calls = os.replace, []
        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError('injected failure')
            real_replace(source, destination)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            w.project_editor.table.item(0, 1).setText('D')
            self.assertFalse(w.flush_autosave())
        self.assertEqual(self.contents(), before)
        self.assertEqual(w.project_editor.table.item(0, 1).text(), 'C')
        self.assertEqual(w._autosave.history.cursor, cursor)
        self.assertTrue(self.warnings)

    def test_external_change_and_unsupported_block(self):
        w = self.open()
        self.people[0].write_text('external', encoding='utf-8')
        w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertFalse(w.flush_autosave())
        self.assertEqual(self.people[0].read_text(encoding='utf-8'), 'external')
        self.assertEqual(w.people_editor.allocations.item(0, 1).text(), '1')

    def test_unsupported_source_is_never_overwritten(self):
        text = self.people[0].read_text(encoding='utf-8').replace('stage: confirmed', 'stage: confirmed\nunknown_key: retained')
        self.people[0].write_text(text, encoding='utf-8')
        before = self.contents()
        errors, dropped = self.window.import_files([str(self.project), *map(str, self.people)])
        self.assertFalse(errors)
        self.assertTrue(dropped)
        self.window.select_year('2026')
        self.window.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertFalse(self.window.flush_autosave())
        self.assertEqual(self.contents(), before)
        self.assertIn('지원하지 않는', self.warnings[-1][1])

    def test_utf8_bom_preserved(self):
        self.people[0].write_bytes(b'\xef\xbb\xbf' + self.people[0].read_bytes())
        w = self.open()
        w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertTrue(w.flush_autosave())
        self.assertTrue(self.people[0].read_bytes().startswith(b'\xef\xbb\xbf'))
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8-sig'))
        self.assertEqual(doc.people[0].allocations['A'], '0.9')

    def test_cp949_unrepresentable_requires_confirmation(self):
        self.people[0].write_bytes(self.people[0].read_text(encoding='utf-8').encode('cp949'))
        w = self.open()
        before = self.people[0].read_bytes()
        w.confirm = lambda *args: False
        w.people_editor.people_list.item(0).setText('홍길동😀')
        self.assertFalse(w.flush_autosave())
        self.assertEqual(self.people[0].read_bytes(), before)
        self.assertEqual(w.people_editor.doc.people[0].name, '홍길동')
        w.confirm = lambda *args: True
        w.people_editor.people_list.item(0).setText('홍길동😀')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(read_people(self.people[0].read_text(encoding='utf-8'))[0].people[0].name, '홍길동😀')

    def test_rollback_failure_blocks_further_writes(self):
        w = self.open()
        real_replace, calls = os.replace, []
        def fail_after_first(source, destination):
            calls.append(destination)
            if len(calls) >= 2:
                raise OSError('persistent failure')
            real_replace(source, destination)
        with patch('allocation.storage.os.replace', side_effect=fail_after_first):
            w.project_editor.table.item(0, 1).setText('C')
            self.assertFalse(w.flush_autosave())
        self.assertTrue(w._autosave_blocked)
        self.assertIn('복구 파일', self.warnings[-1][1])
        self.assertTrue(list(self.root.glob('.prouman-restore-*')))
        self.assertFalse(w.flush_autosave())

    def test_failed_delete_restores_selected_person(self):
        w = self.open()
        w.people_editor.people_list.setCurrentRow(1)
        before = self.contents()
        with patch('allocation.autosave.storage.write_bundle', side_effect=OSError('실패')):
            w.people_editor.remove_person()
            self.assertFalse(w.flush_autosave())
        self.assertEqual(self.contents(), before)
        self.assertEqual(w.people_editor.current().name, '김철수')
        self.assertEqual([p.name for p in w.people_editor.doc.people], ['홍길동', '김철수'])

    def test_input_fixed_absence_project_grid_and_rename_empty(self):
        w = self.open()
        editor = w.people_editor
        editor.allocations.setCurrentCell(0, 0)
        editor.add_fixed()
        editor.fixed.item(0, 1).setText('0.1')
        editor.add_absence()
        editor.absences.item(0, 0).setText('2026-02-01')
        editor.absences.item(0, 1).setText('2026-02-10')
        editor.absences.item(0, 2).setText('휴직')
        self.assertTrue(w.flush_autosave())
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[0].fixed['A'][0], '0.1')
        self.assertEqual(doc.people[0].absences[0].reason, '휴직')
        grid = w.project_editor.grid
        col = w.project_editor.years_shown().index(2026)
        grid.item(0, col).setText('1.5')
        grid.item(0, w.project_editor.YEARS).setText('2, 3')
        self.assertTrue(w.flush_autosave())
        projects, _ = read_projects(self.project.read_text(encoding='utf-8'))
        self.assertEqual(projects[0].years[0].manpower, '1.5')
        self.assertEqual(projects[0].years[0].excluded, '2, 3')
        editor.allocations.item(0, 1).setText('')
        self.assertTrue(w.flush_autosave())
        w.project_editor.table.item(0, 1).setText('C')
        self.assertTrue(w.flush_autosave())
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[0].allocations['C'], '')
        self.assertIn('C', doc.people[0].fixed)

    def test_person_and_project_add_name_delete(self):
        w = self.open()
        editor = w.people_editor
        editor.add_person()
        self.assertTrue(w.flush_autosave())
        editor.people_list.currentItem().setText('추가 사람')
        self.assertTrue(w.flush_autosave())
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.people[-1].name, '추가 사람')
        editor.remove_person()
        self.assertTrue(w.flush_autosave())
        self.assertEqual(len(read_people(self.people[0].read_text(encoding='utf-8'))[0].people), 2)
        w.project_editor.add_project()
        self.assertTrue(w.flush_autosave())
        self.assertEqual(len(read_projects(self.project.read_text(encoding='utf-8'))[0]), 3)
        w.confirm = lambda *args: True
        w.project_editor.remove_project()
        self.assertTrue(w.flush_autosave())
        self.assertEqual(len(read_projects(self.project.read_text(encoding='utf-8'))[0]), 2)

    def test_noop_people_and_project_clear_dirty_preserve_redo(self):
        w = self.open()
        w.people_editor.allocations.item(0, 1).setText('0.8')
        self.assertTrue(w.flush_autosave())
        self.assertTrue(w._autosave.move(-1))
        w._restore_autosave(w._autosave.state, False, {'2026'})
        paths = [self.project, *self.people]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        with patch('allocation.autosave.storage.write_bundle', wraps=storage.write_bundle) as write:
            w.people_editor.allocations.item(0, 1).setText('0.9')
            w.people_editor.allocations.item(0, 1).setText('1')
            w.project_editor.table.item(0, 0).setText('임시')
            w.project_editor.table.item(0, 0).setText('첫 사업')
            self.assertTrue(w.flush_autosave())
            self.assertEqual(write.call_count, 0)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths])
        self.assertFalse(w.people_dirty['2026'])
        self.assertFalse(w.projects_dirty)
        self.assertNotIn('*', w.pages.tabText(w.INPUT_PAGE))
        self.assertEqual(w._autosave.history.cursor, 0)
        self.assertEqual(w._autosave.history.redo_labels(), ['실적·사람 입력 — 참여율 수정'])
        w.ask_export = lambda _: self.fail('무변경 종료 확인이 없어야 합니다')
        self.assertTrue(w.keep_all())

    def test_pin_and_input_share_session(self):
        w = self.open()
        w.project_editor.table.item(0, 0).setText('새 이름')
        self.assertTrue(w.flush_autosave())
        w.show_page(w.RESULT_PAGE)
        self.assertTrue(w.toggle_project_pin('홍길동', 'A'))
        w.project_editor.table.item(0, 0).setText('또 다른 이름')
        self.assertTrue(w.flush_autosave())
        self.assertTrue(w.toggle_project_pin('홍길동', 'A'))
        doc, _ = read_people(self.people[0].read_text(encoding='utf-8'))
        self.assertEqual(doc.metadata, None)
        self.assertEqual(doc.people[0].metadata, {'code': '001'})
        self.assertEqual(doc.people[0].fixed, {})
        self.assertFalse(self.warnings)

    def test_pin_rejects_external_project_dependency_change(self):
        w = self.open()
        w.show_page(w.RESULT_PAGE)
        before = self.people[0].read_bytes()
        changed = self.project.read_bytes() + b'\n# external project change\n'
        self.project.write_bytes(changed)
        self.assertFalse(w.toggle_project_pin('홍길동', 'A'))
        self.assertEqual(self.people[0].read_bytes(), before)
        self.assertEqual(self.project.read_bytes(), changed)
        self.assertEqual(w.people_docs['2026'].people[0].fixed, {})
        self.assertEqual(w._autosave.history.cursor, 0)
        self.assertTrue(any('외부 변경' in text for _, text in self.warnings))


if __name__ == '__main__':
    unittest.main()
