import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if importlib.util.find_spec('PySide6') is None:
    QApplication = None
else:
    from PySide6.QtCore import QPointF, QSettings, Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from allocation.gui import MainWindow

from allocation.document import (PeopleDoc, PersonEntry, PlanDoc, PlanPersonEntry, ProjectEntry, ProjectList,
                                 YearEntry, dump_people, dump_plan, dump_projects)
from allocation.yaml_editor import (clean_yaml_text, extract_error_line, find_item_line, token_at_position,
                                    yaml_diff_summary, yaml_fold_ranges, yaml_outline_items, yaml_section_at)


class YamlEditorHelpersTests(unittest.TestCase):
    def test_fold_ranges_and_outline_for_projects_and_people(self):
        projects_yaml = (
            'projects:\n'
            '  - name: 연구 사업\n'
            '    alias: 연구\n'
            '    period: "2026-01-01~2026-12-31"\n'
            '    yearly:\n'
            '      2026:\n'
            '        allocated_manpower: "1.00000"\n'
            '        excluded_months: []\n'
            '  - name: 운영 사업\n'
            '    alias: 운영\n'
        )
        lines = projects_yaml.splitlines()
        ranges = yaml_fold_ranges(lines)
        self.assertEqual(ranges[0], 9)
        self.assertEqual(ranges[1], 7)
        self.assertEqual(ranges[4], 7)
        outline = yaml_outline_items(lines, 'projects')
        self.assertEqual([(i.label, i.level, i.line) for i in outline],
                         [('연구 — 연구 사업', 0, 1), ('2026년', 1, 5), ('운영 — 운영 사업', 0, 8)])

        people_yaml = (
            'year: 2026\n'
            'people:\n'
            '  - name: 홍길동\n'
            '    allocations:\n'
            '      연구: "0.60000"\n'
            '    absences:\n'
            '      - period: "2026-07-01~2026-07-10"\n'
        )
        p_lines = people_yaml.splitlines()
        self.assertEqual(yaml_section_at(p_lines, 4), 'allocations')
        self.assertEqual(token_at_position(p_lines[4], 7), '연구')
        p_outline = yaml_outline_items(p_lines, 'people')
        self.assertEqual([(i.label, i.level) for i in p_outline],
                         [('홍길동', 0), ('allocations', 1), ('absences', 1)])
        self.assertEqual(find_item_line(lines, 'projects', '연구'), 2)
        self.assertEqual(find_item_line(p_lines, 'people', '홍길동'), 3)

    def test_clean_diff_and_error_line(self):
        self.assertEqual(clean_yaml_text('year: 2026   \n\tname: 가  \n\n'), 'year: 2026\n  name: 가\n')
        added, removed, diff = yaml_diff_summary('year: 2026\na: 1\n', 'year: 2026\na: 2\nb: 3\n', 'test')
        self.assertEqual((added, removed), (2, 1))
        self.assertIn('+b: 3', diff)
        self.assertEqual(extract_error_line('people.yaml line 14: 중복 키'), 14)
        self.assertEqual(extract_error_line('7번째 줄: 탭 금지'), 7)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class YamlEditorGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = self.root / 'projects.yaml'
        self.h = self.root / 'people_2026.yaml'
        self.plan = self.root / 'plan_2026.yaml'
        self.p.write_text(dump_projects(ProjectList([
            ProjectEntry('연구 사업', 'A', '2026-01-01', '2026-12-31', [YearEntry('2026', '2.00000')]),
            ProjectEntry('운영 사업', 'B', '2026-01-01', '2026-12-31', [YearEntry('2026', '2.00000')]),
        ])), encoding='utf-8')
        self.h.write_text(dump_people(PeopleDoc('2026', [
            PersonEntry('가', {'A': '1.00000'}),
            PersonEntry('나', {'B': '1.00000'}),
        ])), encoding='utf-8')
        self.plan.write_text(dump_plan(PlanDoc('2026', [
            PlanPersonEntry('가', {'A': '1.00000'}, {}, {'A': '1.00000'}),
        ])), encoding='utf-8')
        self.window = MainWindow(QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat))
        self.addCleanup(self.window.deleteLater)
        self.warnings = []
        self.ask_answers = []
        for w in self.window.workspaces:
            w.warn = lambda title, text: self.warnings.append((title, text))
            w.confirm = lambda *args: True
        self.assertEqual(self.window.actual.import_files([str(self.p), str(self.h)]), ([], []))
        self.window.actual.select_year('2026')
        self.assertEqual(self.window.plan.import_files([str(self.p), str(self.plan)]), ([], []))
        self.window.plan.select_year('2026')

    def _open_yaml_tab(self, workspace=None, key='people:2026'):
        w = workspace or self.window.actual
        w.show_page(w.YAML_PAGE)
        self.assertEqual(w.pages.currentIndex(), w.YAML_PAGE)
        self.assertTrue(w.yaml_panel.select_key(key, prompt_if_dirty=False))
        return w.yaml_panel

    def test_editor_theme_font_indent_comment_move_and_folding(self):
        panel = self._open_yaml_tab(key='people:2026')
        ed = panel.editor
        self.assertTrue(ed.font().fixedPitch())
        self.assertIn('#1E1E1E', ed.styleSheet().upper())

        ed.setPlainText('people:\n- name: 홍길동\nallocations:\n')
        # Tab / Shift+Tab 다중 줄 들여쓰기 및 내어쓰기 (하드 탭 금지)
        cursor = ed.textCursor()
        cursor.setPosition(ed.document().findBlockByNumber(1).position())
        cursor.setPosition(ed.document().findBlockByNumber(2).position() + 4, QTextCursor.KeepAnchor)
        ed.setTextCursor(cursor)
        QTest.keyClick(ed, Qt.Key_Tab)
        self.assertNotIn('\t', ed.toPlainText())
        self.assertEqual(ed.toPlainText().splitlines()[1], '  - name: 홍길동')
        self.assertEqual(ed.toPlainText().splitlines()[2], '  allocations:')

        QTest.keyClick(ed, Qt.Key_Backtab, Qt.ShiftModifier)
        self.assertEqual(ed.toPlainText().splitlines()[1], '- name: 홍길동')

        # Enter 자동 들여쓰기 (`:` 끝 줄)
        c2 = ed.textCursor()
        c2.setPosition(ed.document().findBlockByNumber(2).position() + len('allocations:'))
        ed.setTextCursor(c2)
        QTest.keyClick(ed, Qt.Key_Return)
        self.assertEqual(ed.toPlainText().splitlines()[3], '  ')

        # Ctrl+/ 주석 토글
        c3 = ed.textCursor()
        c3.setPosition(ed.document().findBlockByNumber(1).position())
        ed.setTextCursor(c3)
        QTest.keyClick(ed, Qt.Key_Slash, Qt.ControlModifier)
        self.assertTrue(ed.toPlainText().splitlines()[1].startswith('# '))
        QTest.keyClick(ed, Qt.Key_Slash, Qt.ControlModifier)
        self.assertEqual(ed.toPlainText().splitlines()[1], '- name: 홍길동')

        # Alt+Up / Alt+Down 줄 이동
        c4 = ed.textCursor()
        c4.setPosition(ed.document().findBlockByNumber(2).position())
        ed.setTextCursor(c4)
        QTest.keyClick(ed, Qt.Key_Up, Qt.AltModifier)
        self.assertEqual(ed.toPlainText().splitlines()[1], 'allocations:')
        QTest.keyClick(ed, Qt.Key_Down, Qt.AltModifier)
        self.assertEqual(ed.toPlainText().splitlines()[1], '- name: 홍길동')

        # 폴딩 / 언폴딩 / 1단계 보기
        panel.revert_current()
        ed.fold_level_one()
        # `people:`(0)과 `- name: 가`(2)는 보이고, `allocations:` 내부(4)는 접혀야 함
        self.assertTrue(ed.document().findBlockByNumber(2).isVisible())
        alloc_line = next(i for i, l in enumerate(ed.toPlainText().splitlines()) if l.strip() == 'allocations:')
        self.assertFalse(ed.document().findBlockByNumber(alloc_line + 1).isVisible())
        ed.jump_to_line(alloc_line + 2)
        self.assertTrue(ed.document().findBlockByNumber(alloc_line + 1).isVisible())
        ed.unfold_all()

    def test_manual_ctrl_s_no_autosave_and_full_roundtrip(self):
        w = self.window.actual
        panel = self._open_yaml_tab(w, 'people:2026')
        ed = panel.editor
        disk_before = self.h.read_text(encoding='utf-8')

        # 편집 중에는 자동 저장되지 않고 표 편집기도 바뀌지 않는다.
        updated = dump_people(PeopleDoc('2026', [
            PersonEntry('가', {'A': '0.50000', 'B': '0.50000'}),
            PersonEntry('나', {'B': '1.00000'}),
        ]))
        ed.setPlainText(updated)
        self.app.processEvents()
        self.assertTrue(panel.has_dirty())
        self.assertEqual(w.pages.tabText(w.YAML_PAGE), 'YAML 편집 *')
        self.assertEqual(self.h.read_text(encoding='utf-8'), disk_before)
        self.assertEqual(w.people_docs['2026'].people[0].allocations, {'A': '1.00000'})

        # Ctrl+S 저장 시 디스크, 표, 전역 이력에 모두 반영된다.
        QTest.keyClick(ed, Qt.Key_S, Qt.ControlModifier)
        self.app.processEvents()
        self.assertFalse(panel.has_dirty())
        self.assertEqual(w.pages.tabText(w.YAML_PAGE), 'YAML 편집')
        self.assertIn('B', w.people_docs['2026'].people[0].allocations)
        self.assertIn('"B":', self.h.read_text(encoding='utf-8'))
        self.assertTrue((self.root / 'people_2026.yaml.bak').exists())
        self.assertGreaterEqual(len(self.window.history.entries), 1)

        # Ctrl+Z 전역 이력 취소 시 YAML 편집기 내용도 복구된다.
        self.window.move_history(-1)
        self.assertNotIn('B', w.people_docs['2026'].people[0].allocations)
        self.assertNotIn('B:', panel.editor.toPlainText())

    def test_syntax_and_duplicate_key_error_blocks_save_and_shows_diagnostics(self):
        w = self.window.actual
        panel = self._open_yaml_tab(w, 'people:2026')
        ed = panel.editor
        disk_before = self.h.read_text(encoding='utf-8')

        bad_yaml = (
            'year: 2026\n'
            'people:\n'
            '  - name: 가\n'
            '    allocations:\n'
            '      A: "0.50000"\n'
            '      A: "0.50000"\n'
        )
        ed.setPlainText(bad_yaml)
        panel.run_validation()
        self.assertGreater(panel.diagnostics_list.count(), 0)
        texts = [panel.diagnostics_list.item(i).text() for i in range(panel.diagnostics_list.count())]
        self.assertTrue(any('E002' in t or '중복' in t for t in texts))

        # 저장 시도 시 차단되고 원본 유지
        self.warnings.clear()
        self.assertFalse(panel.save_current())
        self.assertTrue(self.warnings)
        self.assertEqual(self.h.read_text(encoding='utf-8'), disk_before)

        # 소수 6자리 / 미등록 약칭 실시간 검증 및 클릭 점프
        ed.setPlainText(
            'year: 2026\n'
            'people:\n'
            '  - name: 가\n'
            '    allocations:\n'
            '      없는사업: "0.500000"\n'
        )
        panel.run_validation()
        texts = [panel.diagnostics_list.item(i).text() for i in range(panel.diagnostics_list.count())]
        self.assertTrue(any('E005' in t for t in texts))
        self.assertTrue(any('E007' in t for t in texts))
        panel._jump_diagnostic(panel.diagnostics_list.item(0))

    def test_leave_tab_page_mode_year_and_close_prompts(self):
        w = self.window.actual
        panel = self._open_yaml_tab(w, 'people:2026')
        ed = panel.editor
        ed.setPlainText(ed.toPlainText().replace('나', '다'))
        self.assertTrue(panel.has_dirty())

        # 1. 취소 -> 그대로 머무름
        w.ask_export = lambda what: 'cancel'
        self.assertFalse(panel.select_key('projects'))
        self.assertEqual(panel.current_key(), 'people:2026')
        w.show_page(w.INPUT_PAGE)
        self.assertEqual(w.pages.currentIndex(), w.YAML_PAGE)
        self.window.set_mode(self.window.PLANNING)
        self.assertEqual(self.window.mode_stack.currentIndex(), self.window.ACTUAL)

        # 2. 버리기 -> 원래대로 복구되고 이동 성공
        w.ask_export = lambda what: 'discard'
        w.show_page(w.INPUT_PAGE)
        self.assertEqual(w.pages.currentIndex(), w.INPUT_PAGE)
        self.assertFalse(panel.has_dirty())

        # 3. 저장 후 이동
        panel = self._open_yaml_tab(w, 'people:2026')
        ed.setPlainText(ed.toPlainText().replace('나', '다'))
        w.ask_export = lambda what: 'export'
        self.assertTrue(self.window.set_mode(self.window.PLANNING))
        self.assertEqual(self.window.mode_stack.currentIndex(), self.window.PLANNING)
        self.assertIn('다', [p.name for p in w.people_docs['2026'].people])

    def test_extensions_completion_tooltip_outline_find_replace_diff_and_jump_from_table(self):
        w = self.window.actual
        # 표 편집기에서 YAML로 바로가기
        w.open_yaml_for('projects', 'B')
        self.assertEqual(w.pages.currentIndex(), w.YAML_PAGE)
        panel = w.yaml_panel
        self.assertEqual(panel.current_key(), 'projects')
        self.assertGreater(panel.editor.textCursor().blockNumber(), 0)

        # 아웃라인 필터 및 점프
        panel.select_key('people:2026', prompt_if_dirty=False)
        panel.outline_filter.setText('나')
        self.assertEqual(panel.outline_tree.topLevelItemCount(), 1)
        panel._jump_outline_item(panel.outline_tree.topLevelItem(0))
        self.assertIn('나', panel.editor.textCursor().block().text())

        # 자동완성 후보 & 호버 툴팁 (allocations 내부 줄로 커서 이동)
        alloc_line = next(i for i, l in enumerate(panel.editor.toPlainText().splitlines())
                          if l.strip().startswith(('B:', '"B":')))
        c_alloc = panel.editor.textCursor()
        c_alloc.setPosition(panel.editor.document().findBlockByNumber(alloc_line).position() + 6)
        panel.editor.setTextCursor(c_alloc)
        self.assertIn('A: ', panel.editor._completion_candidates())
        tip = panel._project_tooltip('A', 'allocations')
        self.assertIn('연구 사업', tip)
        self.assertIn('2.00000', tip)

        # 스니펫 삽입 & YAML 정리 & Alt+Up 이동
        panel._insert_snippet('      # 메모\n')
        self.assertIn('# 메모', panel.editor.toPlainText())
        panel.editor.setPlainText(panel.editor.toPlainText() + '\t# 탭\n')
        panel.clean_current()
        self.assertNotIn('\t', panel.editor.toPlainText())
        panel.revert_current()

        # 찾기/바꾸기 & Diff 요약 & YAML 정리
        panel.open_find_bar(show_replace=True)
        panel.find_input.setText('나')
        panel.replace_input.setText('라')
        panel.replace_all()
        self.assertIn('라', panel.editor.toPlainText())
        added, removed, diff_text = yaml_diff_summary(
            panel._buffers['people:2026'].saved_text, panel.editor.toPlainText(), 'people'
        )
        self.assertGreater(added, 0)
        self.assertIn('+  - name: "라"', diff_text)
        panel.close_find_bar()
        panel.revert_current()

    def test_planning_mode_yaml_save_and_close_event_protection(self):
        self.window.set_mode(self.window.PLANNING)
        w = self.window.plan
        panel = self._open_yaml_tab(w, 'plan:2026')
        ed = panel.editor
        new_plan = dump_plan(PlanDoc('2026', [
            PlanPersonEntry('가', {'A': '1.00000', 'B': '0.50000'}, {'A': 1}, {'A': '0.60000', 'B': '0.40000'}),
        ]))
        ed.setPlainText(new_plan)
        self.assertTrue(panel.save_current())
        self.assertIn('B', w.plan_docs['2026'].people[0].draft)
        self.assertIn('"B":', self.plan.read_text(encoding='utf-8'))

        # 창 닫기 시 미저장 YAML 변경 보호
        ed.setPlainText(new_plan.replace('가', '다'))
        w.ask_export = lambda what: 'cancel'
        self.assertFalse(self.window.close())
        self.assertTrue(panel.has_dirty())
        w.ask_export = lambda what: 'discard'
        self.assertTrue(self.window.close())


if __name__ == '__main__':
    unittest.main()
