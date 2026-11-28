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
    from PySide6.QtWidgets import QApplication, QLineEdit
    from allocation.gui import MainWindow, WorkspaceWindow

from allocation import storage
from allocation.document import (ProjectList, ProjectEntry, YearEntry, PeopleDoc, PersonEntry,
                                 PlanDoc, PlanPersonEntry, dump_projects, dump_people, dump_plan,
                                 read_projects, read_people, read_plan)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class CopyStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = self.root / 'project.yaml'
        self.h = self.root / 'people.yaml'
        self.plan = self.root / 'plan.yaml'
        self.p.write_text(dump_projects(ProjectList([ProjectEntry('사업', 'A', '2026-01-01', '2026-12-31',
                                         [YearEntry('2026', '2')], metadata={'code': '01'})], metadata={'owner': '가'})), encoding='utf-8')
        self.h.write_text(dump_people(PeopleDoc('2026', [PersonEntry('가', {'A': '1'}, metadata={'id': '001'})],
                                               metadata={'note': '원본'})), encoding='utf-8')
        self.plan.write_text(dump_plan(PlanDoc('2026', [PlanPersonEntry('가', {'A': '1'}, {}, {'A': '1'},
                                                                      metadata={'id': 'p001'})],
                                              metadata={'plan': '업무'})), encoding='utf-8')
        self.window = MainWindow(QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat))
        self.addCleanup(self.window.deleteLater)
        self.warnings = []
        for w in self.window.workspaces:
            w.warn = lambda *args: self.warnings.append(args)
            w.confirm = lambda *args: True
        self.assertEqual(self.window.actual.import_files([str(self.p), str(self.h)]), ([], []))
        self.window.actual.select_year('2026')
        self.assertEqual(self.window.plan.import_files([str(self.p), str(self.plan)]), ([], []))
        self.window.plan.select_year('2026')

    def metadata(self, w):
        return (list(w.loaded_files), list(w.projects_sources), dict(w.plan_sources),
                {k: list(v) for k, v in w.people_sources.items()},
                {k: set(v) for k, v in w.document_encodings.items()}, dict(w.file_encodings),
                dict(w.file_fingerprints), self.window.history.cursor, list(self.window.history.labels()))

    def test_menus_and_ctrl_s_do_not_export(self):
        w = self.window
        for menu in (w.menuBar().actions()[0].menu(), w.actual.menuBar().actions()[0].menu()):
            actions = menu.actions()
            self.assertNotIn('저장', [action.text() for action in actions])
            self.assertFalse(any(action.shortcut().toString() == 'Ctrl+S' for action in actions))
        self.assertEqual(w.plan.planning_page.save_button.text(), '작업자료 사본 내보내기')
        w.actual.ask_copy_kind = lambda: self.fail('Ctrl+S가 내보내기를 호출했습니다')
        w.show()
        self.app.processEvents()
        self.assertTrue(w.actual.save_status_label.isVisible())
        w.set_mode(w.PLANNING)
        self.assertTrue(w.plan.save_status_label.isVisible())
        w.set_mode(w.ACTUAL)
        QTest.keyClick(w, Qt.Key_S, Qt.ControlModifier)
        self.app.processEvents()

    def test_ui_copy_variants_preserve_source_and_history(self):
        w = self.window.actual
        w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertTrue(w.flush_autosave())
        before, source = self.metadata(w), self.h.read_bytes()
        folder = self.root / 'bundle'
        w.ask_copy_kind = lambda: '작업자료 묶음'
        w.ask_export_folder = lambda: str(folder)
        real = storage.write_bundle
        def spy(*args, **kwargs):
            self.assertEqual(w.save_status_label.text(), '저장 중')
            return real(*args, **kwargs)
        with patch('allocation.gui.write_bundle', side_effect=spy):
            self.assertTrue(w.browse_export())
        self.assertEqual(w.save_status_label.text(), '저장됨')
        self.assertEqual(read_people((folder / 'people/people_2026.yaml').read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.9')
        for choice, filename in [('사업 파일 사본', 'project-copy.yaml'), ('현재 연도 파일 사본', 'person-copy.yaml')]:
            w.ask_copy_kind = lambda choice=choice: choice
            w.ask_copy_file = lambda title, filename=filename: (str(self.root / filename), 'utf-8-sig')
            self.assertTrue(w.browse_export())
            self.assertTrue((self.root / filename).read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertEqual(self.metadata(w), before)
        self.assertEqual(self.h.read_bytes(), source)
        w.people_editor.allocations.item(0, 1).setText('0.8')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(read_people(self.h.read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.8')
        self.assertEqual(read_people((self.root / 'person-copy.yaml').read_text(encoding='utf-8-sig'))[0].people[0].allocations['A'], '0.9')

    def test_plan_copy_error_and_confirm_are_distinct(self):
        w = self.window.plan
        w.planning_page.priorities.item(0, 1).setText('')
        self.assertTrue(w.flush_autosave())
        before = self.metadata(w)
        folder = self.root / 'plan-copy'
        w.ask_copy_kind = lambda: '작업자료 묶음'
        w.ask_export_folder = lambda: str(folder)
        self.assertTrue(w.browse_export())
        doc = read_plan((folder / 'plans/plan_2026.yaml').read_text(encoding='utf-8'))
        self.assertEqual(doc.people[0].draft['A'], '')
        w.ask_copy_kind = lambda: '현재 연도 파일 사본'
        w.ask_copy_file = lambda _: (str(self.root / 'plan-copy.yaml'), 'utf-8')
        self.assertTrue(w.browse_export())
        self.assertEqual(read_plan((self.root / 'plan-copy.yaml').read_text(encoding='utf-8')).people[0].draft['A'], '')
        w.ask_copy_kind = lambda: '사업 파일 사본'
        w.ask_copy_file = lambda _: (str(self.root / 'plan-project.yaml'), 'utf-8')
        self.assertTrue(w.browse_export())
        self.assertEqual(read_projects((self.root / 'plan-project.yaml').read_text(encoding='utf-8'))[0].metadata, {'owner': '가'})
        self.assertFalse(w.confirm_plan(self.root / 'confirmed'))
        self.assertFalse((self.root / 'confirmed').exists())
        self.assertEqual(self.metadata(w), before)

    def test_confirmed_copy_preserves_metadata_and_sources(self):
        w = self.window.plan
        before = self.metadata(w)
        output = self.root / 'confirmed-valid'
        w.ask_copy_kind = lambda: '실적용 확정 묶음'
        w.ask_export_folder = lambda: str(output)
        self.assertTrue(w.browse_export())
        doc = read_people((output / 'people/people_2026.yaml').read_text(encoding='utf-8'))[0]
        self.assertEqual(doc.metadata, {'plan': '업무'})
        self.assertEqual(doc.people[0].metadata, {'id': 'p001'})
        self.assertEqual(read_projects((output / 'projects.yaml').read_text(encoding='utf-8'))[0].metadata, {'owner': '가'})
        self.assertEqual(self.metadata(w), before)

    def test_plan_bundle_includes_open_empty_year(self):
        w = self.window.plan
        w.ask_year = lambda: '2027'
        w.add_year()
        before = self.metadata(w)
        output = self.root / 'all-years'
        self.assertTrue(w.export_all(output))
        self.assertEqual(read_plan((output / 'plans/plan_2027.yaml').read_text(encoding='utf-8')).people, [])
        self.assertEqual(self.metadata(w), before)

    def test_plan_copy_button_and_excel_error_guard(self):
        w = self.window.plan
        before = self.metadata(w)
        folder = self.root / 'button-copy'
        w.ask_export_folder = lambda: str(folder)
        w.planning_page.save_button.click()
        self.assertTrue((folder / 'plans/plan_2026.yaml').exists())
        self.assertEqual(self.metadata(w), before)
        actual = self.window.actual
        actual.people_editor.allocations.item(0, 1).setText('0.9')
        actual.generate()
        self.assertFalse(actual.excel_button.isEnabled())
        self.assertFalse(actual.save_excel(str(self.root / 'invalid.xlsx')))
        self.assertFalse((self.root / 'invalid.xlsx').exists())
        actual.ask_export = lambda _: self.fail('정상 자동 저장 후 종료 질문이 없어야 합니다')
        self.assertTrue(actual.keep_all())

    def test_active_editor_commits_before_copy(self):
        window, w = self.window, self.window.actual
        window.show()
        w.input_tabs.setCurrentIndex(w.PEOPLE_TAB)
        self.app.processEvents()
        table = w.people_editor.allocations
        table.setCurrentCell(0, 1)
        table.editItem(table.item(0, 1))
        self.app.processEvents()
        editor = QApplication.focusWidget()
        self.assertIsInstance(editor, QLineEdit)
        editor.selectAll()
        QTest.keyClicks(editor, '0.8')
        folder = self.root / 'active-copy'
        w.ask_copy_kind = lambda: '작업자료 묶음'
        w.ask_export_folder = lambda: str(folder)
        self.assertTrue(w.browse_export())
        self.assertEqual(read_people(self.h.read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.8')
        self.assertEqual(read_people((folder / 'people/people_2026.yaml').read_text(encoding='utf-8'))[0].people[0].allocations['A'], '0.8')

    def test_unbound_copy_keeps_dirty_and_close_protection(self):
        w = WorkspaceWindow(QSettings(str(self.root / 'unbound.ini'), QSettings.IniFormat))
        self.addCleanup(w.deleteLater)
        w.warn = lambda *args: self.warnings.append(args)
        w.confirm = lambda *args: True
        w.choose_source_path = lambda *args: ''
        w.people_editor.add_person()
        folder = self.root / 'unbound-copy'
        w.ask_copy_kind = lambda: '작업자료 묶음'
        w.ask_export_folder = lambda: str(folder)
        self.assertTrue(w.browse_export())
        self.assertTrue((folder / 'people' / f'people_{w.current_year}.yaml').exists())
        self.assertTrue(w.people_dirty[w.current_year])
        self.assertEqual(w.save_status_label.text(), '저장 위치 필요')
        w.ask_export = lambda _: 'cancel'
        self.assertFalse(w.keep_all())

    def test_overwrite_conflict_and_rollback(self):
        w = self.window.actual
        before_state = self.metadata(w)
        self.assertFalse(w.export_one(self.h, 'current'))
        self.assertFalse(w.export_one(self.plan, 'current'))
        output = self.root / 'copy.yaml'
        output.write_bytes(b'old')
        w.confirm = lambda *args: False
        self.assertFalse(w.export_one(output, 'current'))
        self.assertEqual(output.read_bytes(), b'old')
        w.confirm = lambda *args: True
        def external_then_write(*args, **kwargs):
            output.write_bytes(b'external')
            return storage.write_bundle(*args, **kwargs)
        with patch('allocation.gui.write_bundle', side_effect=external_then_write):
            self.assertFalse(w.export_one(output, 'current'))
        self.assertEqual(output.read_bytes(), b'external')
        folder = self.root / 'existing'
        (folder / 'people').mkdir(parents=True)
        first, second = folder / 'projects.yaml', folder / 'people/people_2026.yaml'
        first.write_bytes(b'old project')
        second.write_bytes(b'old person')
        real, calls = os.replace, []
        def fail_second(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('failure')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail_second):
            self.assertFalse(w.export_all(folder))
        self.assertEqual((first.read_bytes(), second.read_bytes()), (b'old project', b'old person'))
        self.assertEqual(self.metadata(w), before_state)

    def test_unsupported_fields_block_relevant_copy(self):
        original = self.root / 'unknown.yaml'
        original.write_text(self.h.read_text(encoding='utf-8') + '\nunknown: retained\n', encoding='utf-8')
        w = WorkspaceWindow(QSettings(str(self.root / 'unknown.ini'), QSettings.IniFormat))
        self.addCleanup(w.deleteLater)
        w.warn = lambda *args: self.warnings.append(args)
        self.assertTrue(w.import_files([str(original)])[1])
        w.select_year('2026')
        self.assertFalse(w.export_one(self.root / 'unsafe-copy.yaml', 'current'))
        self.assertFalse((self.root / 'unsafe-copy.yaml').exists())

    def test_persistent_status_progress_failure_and_conflict(self):
        w = self.window.actual
        self.assertEqual(w.save_status_label.text(), '저장됨')
        real = storage.write_bundle
        def spy(*args, **kwargs):
            self.assertEqual(w.save_status_label.text(), '저장 중')
            return real(*args, **kwargs)
        w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertEqual(w.save_status_label.text(), '저장 중')
        with patch('allocation.autosave.storage.write_bundle', side_effect=spy):
            self.assertTrue(w.flush_autosave())
        self.assertEqual(w.save_status_label.text(), '저장됨')
        w.statusBar().showMessage('다른 메시지')
        self.assertEqual(w.save_status_label.text(), '저장됨')
        self.assertIn(str(self.h), w.save_status_label.toolTip())
        with patch('allocation.autosave.storage.write_bundle', side_effect=OSError('disk failure')):
            w.people_editor.allocations.item(0, 1).setText('0.8')
            self.assertFalse(w.flush_autosave())
        self.assertEqual(w.save_status_label.text(), '저장 실패')
        w.select_year('2026')
        self.assertEqual(w.save_status_label.text(), '저장 실패')
        self.h.write_bytes(self.h.read_bytes() + b'\n# external\n')
        w.people_editor.allocations.item(0, 1).setText('0.7')
        self.assertFalse(w.flush_autosave())
        self.assertEqual(w.save_status_label.text(), '외부 변경 충돌')

    def test_recovery_required_status(self):
        w = self.window.actual
        real, calls = os.replace, []
        def fail(src, dst):
            calls.append(dst)
            if len(calls) >= 2:
                raise OSError('persistent')
            real(src, dst)
        with patch('allocation.storage.os.replace', side_effect=fail):
            w.project_editor.table.item(0, 1).setText('B')
            self.assertFalse(w.flush_autosave())
        self.assertEqual(w.save_status_label.text(), '복구 필요')
        self.assertIn('복구 파일', w.save_status_label.toolTip())

    def test_encoding_transition_updates_source_not_copy(self):
        root = self.root / 'cp949'
        root.mkdir()
        source = root / 'people.yaml'
        source.write_bytes(dump_people(PeopleDoc('2026', [PersonEntry('홍길동', {'A': '1'})])).encode('cp949'))
        w = WorkspaceWindow(QSettings(str(root / 'settings.ini'), QSettings.IniFormat))
        self.addCleanup(w.deleteLater)
        w.warn = lambda *args: self.warnings.append(args)
        w.confirm = lambda *args: True
        self.assertEqual(w.import_files([str(source)]), ([], []))
        w.select_year('2026')
        w.people_editor.people_list.item(0).setText('홍길동😀')
        self.assertTrue(w.flush_autosave())
        self.assertEqual(w.file_encodings[str(source.resolve())][0], 'utf-8')
        self.assertEqual(w.read_encodings[str(source.resolve())], 'utf-8')
        self.assertEqual(w.document_encodings['people:2026'], {'utf-8'})
        self.assertIn('UTF-8', w.encoding_label.text())
        self.assertEqual(w.save_status_label.text(), '저장됨')
        before = dict(w.document_encodings)
        self.assertTrue(w.export_one(root / 'copy.yaml', 'current', 'utf-8-sig'))
        self.assertEqual(w.document_encodings, before)
        self.assertEqual(w.file_encodings[str(source.resolve())][0], 'utf-8')

    def test_history_labels_describe_actual_change(self):
        w = self.window.actual
        w.people_editor.people_list.item(0).setText('이름')
        self.assertTrue(w.flush_autosave())
        self.assertIn('사람 이름 수정', self.window.history.entries[-1].label)
        w.people_editor.add_absence()
        self.assertTrue(w.flush_autosave())
        self.assertIn('공백 기간 수정', self.window.history.entries[-1].label)
        w.people_editor.allocations.setCurrentCell(0, 0)
        w.people_editor.add_fixed()
        self.assertTrue(w.flush_autosave())
        self.assertIn('월별 고정', self.window.history.entries[-1].label)
        w.people_editor.allocations.item(0, 1).setText('0.9')
        self.assertTrue(w.flush_autosave())
        self.assertIn('참여율 수정', self.window.history.entries[-1].label)
        column = w.project_editor.years_shown().index(2026)
        w.project_editor.grid.item(0, column).setText('1.5')
        self.assertTrue(w.flush_autosave())
        self.assertIn('사업 배정 수정', self.window.history.entries[-1].label)


if __name__ == '__main__':
    unittest.main()
