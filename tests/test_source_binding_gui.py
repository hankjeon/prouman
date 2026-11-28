import importlib.util
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if importlib.util.find_spec('PySide6') is None:
    QApplication = None
else:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from allocation.gui import MainWindow, WorkspaceWindow

from allocation.document import (ProjectList, ProjectEntry, YearEntry, PeopleDoc, PersonEntry,
                                 dump_projects, dump_people, read_projects, read_people)
from allocation import storage


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class SourceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.w = WorkspaceWindow(QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat))
        self.w.warn = lambda *args: None
        self.addCleanup(self.w.deleteLater)

    def test_project_only_source(self):
        path = self.root / 'project_source.yaml'
        path.write_text(dump_projects(ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2026-12-31',
                                                               [YearEntry('2026', '1')])])), encoding='utf-8')
        self.assertEqual(self.w.import_files([str(path)]), ([], []))
        self.assertIsNotNone(self.w.autosave_session)
        self.w.project_editor.table.item(0, 0).setText('수정')
        self.assertTrue(self.w.flush_autosave())
        self.assertEqual(read_projects(path.read_text(encoding='utf-8'))[0][0].name, '수정')
        self.assertFalse(list(self.root.glob('people*.yaml')))

    def test_people_only_source(self):
        path = self.root / 'people_2026.yaml'
        path.write_text(dump_people(PeopleDoc('2026', [PersonEntry('가', {'A': '1'})])), encoding='utf-8')
        self.assertEqual(self.w.import_files([str(path)]), ([], []))
        self.w.select_year('2026')
        self.assertIsNotNone(self.w.autosave_session)
        self.w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertTrue(self.w.flush_autosave())
        self.assertEqual(read_people(path.read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.9')
        self.assertFalse((self.root / 'projects.yaml').exists())

    def test_new_people_first_edit_and_cancel_preserves_project_binding(self):
        self.test_project_only_source()
        project = self.root / 'project_source.yaml'
        year = self.w.current_year
        self.w.choose_source_path = lambda *args: ''
        self.w.people_editor.add_person()
        self.assertFalse(self.w.flush_autosave())
        self.assertTrue(self.w.people_dirty[year])
        self.assertEqual(len(self.w.people_docs[year].people), 1)
        self.w.project_editor.table.item(0, 0).setText('다시 수정')
        self.assertTrue(self.w.flush_autosave())
        self.assertEqual(read_projects(project.read_text(encoding='utf-8'))[0][0].name, '다시 수정')
        self.assertTrue(self.w.people_dirty[year])
        target = self.root / 'named_people.yaml'
        self.w.choose_source_path = lambda *args: str(target)
        self.assertTrue(self.w.browse_bind_sources())
        self.assertTrue(target.exists())
        self.assertFalse(self.w.people_dirty[year])
        self.w.people_editor.people_list.item(0).setText('새 이름')
        self.assertTrue(self.w.flush_autosave())
        self.assertEqual(read_people(target.read_text(encoding='utf-8'))[0].people[0].name, '새 이름')

    def test_merge_metadata_and_order_preserves_originals(self):
        projects = [self.root / 'p1.yaml', self.root / 'p2.yaml']
        people = [self.root / 'h1.yaml', self.root / 'h2.yaml']
        for i, path in enumerate(projects):
            item = ProjectEntry(f'사업{i}', f'P{i}', '2026-01-01', '2026-12-31',
                                [YearEntry('2026', '1')], metadata={'item': str(i)})
            path.write_text(dump_projects(ProjectList([item], metadata={'same': str(i), 'list': [i, '001']})), encoding='utf-8')
        for i, path in enumerate(people):
            person = PersonEntry(f'사람{i}', {f'P{i}': '1'}, metadata={'code': f'00{i}'})
            path.write_text(dump_people(PeopleDoc('2026', [person], metadata={'same': str(i), 'list': [i, '001']})), encoding='utf-8')
        before = [p.read_bytes() for p in projects + people]
        self.assertEqual(self.w.import_files([*map(str, projects), *map(str, people)]), ([], []))
        self.w.select_year('2026')
        self.assertEqual([p.read_bytes() for p in projects + people], before)
        project_target = self.root / 'unified_projects.yaml'
        people_target = self.root / 'unified_people.yaml'
        self.w.choose_source_path = lambda kind, *args: str(project_target if kind == 'projects' else people_target)
        self.w.project_editor.reorder_project_rows([1, 0])
        self.assertTrue(self.w.flush_autosave())
        self.w.people_editor.reorder_people(['사람1', '사람0'])
        self.assertTrue(self.w.flush_autosave())
        self.assertEqual([p.read_bytes() for p in projects + people], before)
        merged_projects = read_projects(project_target.read_text(encoding='utf-8'))[0]
        merged_people = read_people(people_target.read_text(encoding='utf-8'))[0]
        self.assertEqual([p.alias for p in merged_projects], ['P1', 'P0'])
        self.assertEqual([p.name for p in merged_people.people], ['사람1', '사람0'])
        self.assertEqual(merged_projects[0].metadata, {'item': '1'})
        self.assertEqual(merged_people.people[0].metadata, {'code': '001'})
        project_meta = merged_projects.metadata['prouman_source_metadata']
        people_meta = merged_people.metadata['prouman_source_metadata']
        self.assertEqual([x['metadata']['same'] for x in project_meta], ['0', '1'])
        self.assertEqual([x['metadata']['list'][1] for x in people_meta], ['001', '001'])

    def test_new_document_explicit_paths_and_cancel(self):
        self.test_project_only_source()
        old_path = self.root / 'project_source.yaml'
        before = old_path.read_bytes()
        choices = iter([str(self.root / 'new_projects.yaml'), ''])
        self.w.choose_source_path = lambda *args: next(choices)
        self.assertFalse(self.w.browse_new())
        self.assertEqual(old_path.read_bytes(), before)
        self.assertEqual(self.w.project_editor.projects[0].name, '수정')
        self.assertFalse((self.root / 'new_projects.yaml').exists())
        choices = iter([str(self.root / 'new_projects.yaml'), str(self.root / 'new_people.yaml')])
        self.w.choose_source_path = lambda *args: next(choices)
        self.assertTrue(self.w.browse_new())
        self.assertEqual(old_path.read_bytes(), before)
        self.w.project_editor.add_project()
        self.w.project_editor.table.item(0, 0).setText('새 사업')
        self.assertTrue(self.w.flush_autosave())
        self.assertEqual(read_projects((self.root / 'new_projects.yaml').read_text(encoding='utf-8'))[0][0].name, '새 사업')

    def test_new_document_second_replace_failure_keeps_workspace(self):
        self.test_project_only_source()
        before = (self.root / 'project_source.yaml').read_bytes()
        choices = iter([str(self.root / 'new_projects.yaml'), str(self.root / 'new_people.yaml')])
        self.w.choose_source_path = lambda *args: next(choices)
        real, calls = os.replace, []
        def fail_second(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('failure')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            self.assertFalse(self.w.browse_new())
        self.assertEqual((self.root / 'project_source.yaml').read_bytes(), before)
        self.assertEqual(self.w.project_editor.projects[0].name, '수정')
        self.assertFalse((self.root / 'new_projects.yaml').exists())
        self.assertFalse((self.root / 'new_people.yaml').exists())

    def test_merge_failure_rolls_back_files_and_binding(self):
        project = self.root / 'projects_input.yaml'
        project.write_text(dump_projects(ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2026-12-31',
                                                                  [YearEntry('2026', '2')])])), encoding='utf-8')
        people = [self.root / 'a.yaml', self.root / 'b.yaml']
        for i, path in enumerate(people):
            path.write_text(dump_people(PeopleDoc('2026', [PersonEntry(f'사람{i}', {'A': '1'})])), encoding='utf-8')
        self.assertEqual(self.w.import_files([str(project), *map(str, people)]), ([], []))
        self.w.select_year('2026')
        before = [p.read_bytes() for p in [project, *people]]
        old_sources = deepcopy(self.w.people_sources)
        old_loaded = list(self.w.loaded_files)
        target = self.root / 'merged.yaml'
        self.w.choose_source_path = lambda *args: str(target)
        real, calls = os.replace, []
        def fail_second(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('failure')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            self.w.project_editor.table.item(0, 1).setText('B')
            self.assertFalse(self.w.flush_autosave())
        self.assertEqual([p.read_bytes() for p in [project, *people]], before)
        self.assertEqual(self.w.people_sources, old_sources)
        self.assertEqual(self.w.loaded_files, old_loaded)
        self.assertFalse(target.exists())
        self.assertEqual(self.w.people_docs['2026'].people[0].allocations, {'B': '1'})
        self.w.choose_source_path = lambda *args: ''
        self.w.project_editor.table.item(0, 0).setText('이름만 변경')
        self.assertFalse(self.w.flush_autosave())
        self.assertEqual([p.read_bytes() for p in [project, *people]], before)
        self.assertFalse(self.w.history_ready())

    def test_merge_rejects_original_path(self):
        for i in range(2):
            path = self.root / f'project_{i}.yaml'
            item = ProjectEntry(f'사업{i}', f'P{i}', '2026-01-01', '2026-12-31', [YearEntry('2026', '1')])
            path.write_text(dump_projects(ProjectList([item])), encoding='utf-8')
        paths = [self.root / 'project_0.yaml', self.root / 'project_1.yaml']
        before = [p.read_bytes() for p in paths]
        self.assertEqual(self.w.import_files(list(map(str, paths))), ([], []))
        self.w.choose_source_path = lambda *args: str(paths[0])
        self.w.project_editor.reorder_project_rows([1, 0])
        self.assertFalse(self.w.flush_autosave())
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertEqual(self.w.projects_sources, list(map(str, paths)))

    def test_year_edit_requires_new_path_and_cancel_keeps_old_source(self):
        path = self.root / 'people_2026.yaml'
        path.write_text(dump_people(PeopleDoc('2026', [PersonEntry('가', {'A': '1'})])), encoding='utf-8')
        self.assertEqual(self.w.import_files([str(path)]), ([], []))
        self.w.select_year('2026')
        before = path.read_bytes()
        self.w.people_editor.year_edited('2027')
        self.w.choose_source_path = lambda *args: ''
        self.assertFalse(self.w.flush_year_edit())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.w.current_year, '2026')
        self.assertEqual(self.w.people_editor.doc.year, '2027')
        target = self.root / 'new_2027.yaml'
        self.w.choose_source_path = lambda *args: str(target)
        self.assertTrue(self.w.flush_year_edit())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(read_people(target.read_text(encoding='utf-8'))[0].year, '2027')
        self.assertEqual(self.w.current_year, '2027')

    def test_cancel_keeps_global_history_and_unbound_content_during_undo(self):
        window = MainWindow(QSettings(str(self.root / 'main.ini'), QSettings.IniFormat))
        self.addCleanup(window.deleteLater)
        w = window.actual
        w.warn = lambda *args: None
        path = self.root / 'project.yaml'
        path.write_text(dump_projects(ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2026-12-31',
                                                               [YearEntry('2026', '1')])])), encoding='utf-8')
        self.assertEqual(w.import_files([str(path)]), ([], []))
        w.project_editor.table.item(0, 0).setText('변경')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(window.history.cursor, 1)
        w.choose_source_path = lambda *args: ''
        w.people_editor.add_person()
        self.assertFalse(w.flush_autosave())
        self.assertEqual(window.history.cursor, 1)
        w.project_editor.table.item(0, 0).setText('두 번째 변경')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(window.history.cursor, 2)
        self.assertTrue(window.move_history(-1))
        self.assertEqual(w.project_editor.projects[0].name, '변경')
        self.assertEqual(len(w.people_editor.doc.people), 1)
        self.assertTrue(w.people_dirty[w.current_year])

    def test_merge_precise_metadata_then_undo_redo_and_restart(self):
        window = MainWindow(QSettings(str(self.root / 'persist.ini'), QSettings.IniFormat))
        self.addCleanup(window.deleteLater)
        w = window.actual
        w.warn = lambda *args: None
        paths = [self.root / 'person_a.yaml', self.root / 'person_b.yaml']
        paths[0].write_text('year: 2026\nmetadata: {precision: 0.12345678901234567890, name: "001"}\n'
                            'people: [{name: 가, allocations: {A: "1"}, metadata: {code: "01"}}]\n', encoding='utf-8')
        paths[1].write_text('year: 2026\nmetadata: {precision: 0.9, name: "001"}\n'
                            'people: [{name: 나, allocations: {A: "1"}, metadata: {code: "02"}}]\n', encoding='utf-8')
        before = [p.read_bytes() for p in paths]
        self.assertEqual(w.import_files(list(map(str, paths))), ([], []))
        w.select_year('2026')
        target = self.root / 'combined.yaml'
        w.choose_source_path = lambda *args: str(target)
        w.people_editor.reorder_people(['나', '가'])
        self.assertTrue(w.flush_autosave())
        self.assertEqual([p.read_bytes() for p in paths], before)
        merged = read_people(target.read_text(encoding='utf-8'))[0]
        source_values = merged.metadata['prouman_source_metadata']
        self.assertEqual(str(source_values[0]['metadata']['precision']), '0.12345678901234567890')
        self.assertEqual(source_values[0]['metadata']['name'], '001')
        self.assertEqual([p.metadata['code'] for p in merged.people], ['02', '01'])
        w.people_editor.people_list.item(0).setText('변경')
        self.assertTrue(w.flush_autosave())
        self.assertTrue(window.move_history(-1))
        self.assertEqual(read_people(target.read_text(encoding='utf-8'))[0].people[0].name, '나')
        self.assertTrue(window.move_history(1))
        self.assertEqual(read_people(target.read_text(encoding='utf-8'))[0].people[0].name, '변경')
        window.settings.sync()
        reopened = MainWindow(QSettings(str(self.root / 'persist.ini'), QSettings.IniFormat))
        self.addCleanup(reopened.deleteLater)
        self.assertEqual([p.name for p in reopened.actual.people_docs['2026'].people], ['변경', '가'])
        self.assertEqual([Path(p) for p in reopened.actual.people_sources['2026']], [target])

    def test_rejects_other_mode_and_same_bundle_paths(self):
        window = MainWindow(QSettings(str(self.root / 'modes.ini'), QSettings.IniFormat))
        self.addCleanup(window.deleteLater)
        project = self.root / 'other_mode.yaml'
        project.write_text(dump_projects(ProjectList()), encoding='utf-8')
        self.assertEqual(window.plan.import_files([str(project)]), ([], []))
        w = window.actual
        w.warn = lambda *args: None
        w.choose_source_path = lambda *args: str(project)
        w.project_editor.add_project()
        self.assertFalse(w.flush_autosave())
        self.assertEqual(project.read_text(encoding='utf-8'), dump_projects(ProjectList()))
        paths = iter([str(self.root / 'same.yaml'), str(self.root / 'same.yaml')])
        w.choose_source_path = lambda *args: next(paths)
        self.assertFalse(w.browse_new())
        self.assertFalse((self.root / 'same.yaml').exists())

    def test_merge_external_change_and_encoding_cancel(self):
        paths = [self.root / 'first.yaml', self.root / 'second.yaml']
        for i, path in enumerate(paths):
            person = PersonEntry(f'사람{i}', {'A': '1'})
            text = dump_people(PeopleDoc('2026', [person]))
            path.write_bytes(text.encode('utf-8' if i == 0 else 'cp949'))
        self.assertEqual(self.w.import_files(list(map(str, paths))), ([], []))
        self.w.select_year('2026')
        target = self.root / 'merged.yaml'
        self.w.choose_source_path = lambda *args: str(target)
        self.w.choose_output_encoding = lambda *args: None
        before = [p.read_bytes() for p in paths]
        self.w.people_editor.reorder_people(['사람1', '사람0'])
        self.assertFalse(self.w.flush_autosave())
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertFalse(target.exists())
        self.w.choose_output_encoding = lambda *args: 'utf-8'
        paths[0].write_bytes(paths[0].read_bytes() + b'\n# external\n')
        before = [p.read_bytes() for p in paths]
        self.assertFalse(self.w.browse_bind_sources())
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertFalse(target.exists())

    def test_new_year_and_prepared_plan_first_edit(self):
        project = self.root / 'projects_input.yaml'
        project.write_text(dump_projects(ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2028-12-31',
                                                                  [YearEntry('2026', '2'), YearEntry('2027', '2')])])), encoding='utf-8')
        self.assertEqual(self.w.import_files([str(project)]), ([], []))
        self.w.ask_year = lambda: '2027'
        self.w.add_year()
        self.assertFalse((self.root / 'people_2027.yaml').exists())
        self.w.choose_source_path = lambda *args: str(self.root / 'people_2027.yaml')
        self.w.people_editor.add_person()
        self.assertTrue(self.w.flush_autosave())
        self.assertTrue((self.root / 'people_2027.yaml').exists())
        plan = WorkspaceWindow(QSettings(str(self.root / 'plan.ini'), QSettings.IniFormat), planning=True)
        self.addCleanup(plan.deleteLater)
        plan.warn = lambda *args: None
        self.assertEqual(plan.import_files([str(project)]), ([], []))
        self.assertTrue(plan.prepare_plan(PeopleDoc('2026', [PersonEntry('가', {'A': '1'})]), '2027'))
        plan.choose_source_path = lambda *args: str(self.root / 'plan_2027.yaml')
        plan.planning_page.priorities.item(0, 1).setText('0.8')
        self.assertTrue(plan.flush_autosave())
        from allocation.document import read_plan
        self.assertEqual(read_plan((self.root / 'plan_2027.yaml').read_text(encoding='utf-8')).people[0].draft['A'], '0.8')

    def test_original_change_during_preparation_blocks_merge(self):
        paths = [self.root / 'first.yaml', self.root / 'second.yaml']
        for i, path in enumerate(paths):
            path.write_text(dump_people(PeopleDoc('2026', [PersonEntry(f'사람{i}', {'A': '1'})])), encoding='utf-8')
        self.assertEqual(self.w.import_files(list(map(str, paths))), ([], []))
        self.w.select_year('2026')
        target = self.root / 'merged.yaml'
        self.w.choose_source_path = lambda *args: str(target)
        before_second = paths[1].read_bytes()
        def external_then_write(*args, **kwargs):
            paths[0].write_bytes(paths[0].read_bytes() + b'\n# external\n')
            return storage.write_bundle(*args, **kwargs)
        with patch('allocation.gui.write_bundle', side_effect=external_then_write):
            self.w.people_editor.reorder_people(['사람1', '사람0'])
            self.assertFalse(self.w.flush_autosave())
        self.assertFalse(target.exists())
        self.assertEqual(paths[1].read_bytes(), before_second)
        self.assertTrue(paths[0].read_bytes().endswith(b'# external\n'))
        self.assertEqual(self.w.people_sources['2026'], list(map(str, paths)))
