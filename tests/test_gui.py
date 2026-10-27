import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from openpyxl import load_workbook

# 창을 띄우지 않고 검증한다. 사용자가 지정한 플랫폼이 있으면 그대로 쓴다.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))

from test_allocation import person, project

from allocation.loading import load_inputs

if QApplication is not None:
    from allocation.gui import (DEFAULT_FONT_SIZE, MAX_FONT_SIZE, MIN_FONT_SIZE, SEVERITY_COLORS, MainWindow,
                                SelectionDelegate, find_projects_file)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.settings = QSettings(str(self.path / 'settings.ini'), QSettings.IniFormat)
        self.warnings = []
        self.save_answers = []  # 저장하지 않은 변경 질문에 줄 답. 비면 'discard'
        self.confirm_answer = True
        self.save_paths = []  # 저장 위치 대화상자에 줄 경로

    def window(self):
        window = MainWindow(self.settings)
        window.warn = lambda title, text: self.warnings.append((title, text))
        window.ask_save = lambda kind: self.save_answers.pop(0) if self.save_answers else 'discard'
        window.confirm = lambda title, text: self.confirm_answer
        window.ask_save_path = lambda kind: self.save_paths.pop(0) if self.save_paths else ''
        self.addCleanup(window.deleteLater)
        return window

    def write(self, projects, people, year=2027):
        p, h = self.path / 'projects.yaml', self.path / 'people' / 'people.yaml'
        h.parent.mkdir(exist_ok=True)
        p.write_text(yaml.safe_dump({'projects': projects}, allow_unicode=True, sort_keys=False), encoding='utf-8')
        h.write_text(yaml.safe_dump({'year': year, 'people': people}, allow_unicode=True, sort_keys=False),
                     encoding='utf-8')
        return p, h

    def generated(self, projects, people):
        p, h = self.write(projects, people)
        window = self.window()
        window.choose_projects(str(p))
        window.choose_people(str(h))
        window.generate()
        return window

    @staticmethod
    def texts(view):
        model = view.model()
        return [[model.text(r, c) for c in range(model.columnCount())] for r in range(model.rowCount())]

    def test_T29_generate_views_copy_excel_and_settings(self):
        w = self.generated([project(), project('사업2')],
                           [person({'사업1': '0.60000', '사업2': '0.40000'}), person({'사업2': '1'}, name='김철수')])
        self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                         ['사업 입력', '사람 입력', '검증', '요약', '개인상세', '사업상세'])
        self.assertEqual(w.tabs.currentIndex(), w.SUMMARY_TAB)
        self.assertTrue(w.excel_button.isEnabled())
        self.assertEqual(w.status.text(), '관리 연도 2027 · ERROR 0건 / WARNING 0건')
        self.assertEqual(self.texts(w.issue_view), [])
        self.assertEqual(sorted(self.texts(w.people_summary_view)),
                         [['김철수', '1.00000', '0.00000', '1.00000'], ['홍길동', '1.00000', '0.00000', '1.00000']])
        self.assertEqual(self.texts(w.project_summary_view),
                         [['사업1', '사업1', '10.00000', '0.60000', '9.40000', ''],
                          ['사업2', '사업2', '10.00000', '1.40000', '8.60000', '']])
        projects = self.texts(w.project_view)
        self.assertEqual([len(row) for row in projects], [18, 18])
        self.assertEqual(projects[0][6:9], ['0.05000', '0.05000', '0.05000'])

        # 사람을 고르면 사업·공백·합계 표가 나오고 합계 행은 굵게 표시한다.
        self.assertEqual([w.person_list.item(i).text() for i in range(w.person_list.count())], ['김철수', '홍길동'])
        self.assertEqual(w.person_list.currentRow(), 0)
        self.assertEqual([row[0] for row in self.texts(w.person_view)], ['사업2', '공백', '합계'])
        w.person_list.setCurrentRow(1)
        detail = self.texts(w.person_view)
        self.assertEqual([row[0] for row in detail], ['사업1', '사업2', '공백', '합계'])
        self.assertEqual(detail[0][1:4], ['0.05000', '0.05000', '0.05000'])
        self.assertEqual(detail[3][-1], '1.00000')
        model = w.person_view.model()
        self.assertTrue(model.data(model.index(3, 1), Qt.FontRole).bold())
        self.assertIsNone(model.data(model.index(0, 1), Qt.FontRole))
        self.assertEqual(model.data(model.index(0, 1), Qt.TextAlignmentRole), int(Qt.AlignRight | Qt.AlignVCenter))
        self.assertIsNone(model.data(model.index(0, 0), Qt.TextAlignmentRole))

        # 선택한 범위를 탭 구분 텍스트로 복사하고, 선택하지 않은 칸은 비운다.
        view = w.person_view
        self.assertIsInstance(view.itemDelegate(), SelectionDelegate)
        select = QItemSelectionModel.SelectionFlag.Select
        view.selectionModel().select(view.model().index(0, 1), select)
        view.selectionModel().select(view.model().index(1, 2), select)
        self.assertEqual(view.copy_selection(), '0.05000\t\n\t0.03333')
        self.assertEqual(QGuiApplication.clipboard().text(), '0.05000\t\n\t0.03333')
        view.clearSelection()
        self.assertEqual(view.copy_selection(), '')

        self.assertTrue(w.save_excel(str(self.path / 'out.xlsx')))
        self.assertEqual(load_workbook(self.path / 'out.xlsx').sheetnames, ['검증', '요약', '개인상세', '사업상세'])
        (self.path / 'folder.xlsx').mkdir()
        self.assertFalse(w.save_excel(str(self.path / 'folder.xlsx')))
        self.assertEqual(self.warnings[-1][0], '엑셀 저장 실패')

        # 연 파일 경로를 기억해 다음 창에서 다시 연다.
        again = self.window()
        self.assertEqual(again.projects_edit.text(), str(self.path / 'projects.yaml'))
        self.assertEqual(again.people_edit.text(), str(self.path / 'people' / 'people.yaml'))
        self.assertEqual([p.alias for p in again.project_editor.projects], ['사업1', '사업2'])
        self.assertEqual(len(again.people_editor.doc.people), 2)

    def test_T29_errors_clear_results(self):
        w = self.generated([project()], [person()])
        self.assertTrue(w.excel_button.isEnabled())
        _, h = self.write([project()], [person({'사업1': '0.90000'})])
        w.choose_people(str(h))
        w.generate()
        self.assertEqual(w.tabs.currentIndex(), w.ISSUES_TAB)
        self.assertFalse(w.excel_button.isEnabled())
        self.assertIsNone(w.result)
        self.assertIn('오류로 결과를 만들지 않았습니다', w.status.text())
        issues = self.texts(w.issue_view)
        self.assertIn(['ERROR', 'E101'], [row[:2] for row in issues])
        model = w.issue_view.model()
        self.assertEqual(model.data(model.index(0, 0), Qt.ForegroundRole), SEVERITY_COLORS['ERROR'])
        for view in (w.people_summary_view, w.project_summary_view, w.person_view, w.project_view):
            self.assertEqual(view.model().rowCount(), 0)
        self.assertEqual(w.person_list.count(), 0)

        # 사업 입력이 비면 YAML 검증 오류(E003)와 미등록 약칭(E007)이 나온다.
        w.new_document('projects')
        w.generate()
        codes = [row[1] for row in self.texts(w.issue_view)]
        self.assertIn('E003', codes)
        self.assertIn('E007', codes)
        self.assertFalse(w.excel_button.isEnabled())

        w.choose_projects(str(self.path / '없는 파일.yaml'))
        self.assertEqual(self.warnings[-1][0], '사업 파일 열기 실패')

    def test_T29_warning_only_shows_results(self):
        w = self.generated([project(excluded=[4, 4]), project('사업2')],
                           [person({'사업1': '0.60000', '사업2': '0.40000'})])
        self.assertTrue(w.excel_button.isEnabled())
        self.assertEqual([row[:2] for row in self.texts(w.issue_view)], [['WARNING', 'W001']])
        model = w.issue_view.model()
        self.assertEqual(model.data(model.index(0, 0), Qt.ForegroundRole), SEVERITY_COLORS['WARNING'])
        self.assertEqual(w.people_summary_view.model().rowCount(), 1)

    def test_T29_font_size(self):
        w = self.generated([project()], [person()])
        self.assertEqual(DEFAULT_FONT_SIZE, 10)
        self.assertEqual(w.font().pointSize(), DEFAULT_FONT_SIZE)
        # 크기를 바꾸기 전에는 설정에 저장하지 않는다.
        self.assertFalse(self.settings.contains('font_size'))
        self.assertEqual(w.person_view.font().pointSize(), DEFAULT_FONT_SIZE)
        height = w.person_view.verticalHeader().defaultSectionSize()
        width = w.person_view.columnWidth(1)
        w.change_font_size(1)
        w.change_font_size(1)
        self.assertEqual(w.person_view.font().pointSize(), DEFAULT_FONT_SIZE + 2)
        # 행 높이와 열 너비가 새 글자 크기에 맞게 커진다.
        self.assertGreater(w.person_view.verticalHeader().defaultSectionSize(), height)
        self.assertGreater(w.person_view.columnWidth(1), width)
        # 합계 행의 굵은 글꼴도 창 글자 크기를 따른다.
        model = w.person_view.model()
        bold = model.data(model.index(model.rowCount() - 1, 1), Qt.FontRole).resolve(w.person_view.font())
        self.assertEqual((bold.pointSize(), bold.bold()), (DEFAULT_FONT_SIZE + 2, True))
        # 바꾼 크기는 다음 창에 이어지고, 범위를 벗어나지 않으며, Ctrl+0으로 기본값이 된다.
        self.assertEqual(self.window().font().pointSize(), DEFAULT_FONT_SIZE + 2)
        for _ in range(30):
            w.change_font_size(1)
        self.assertEqual(w.font_size, MAX_FONT_SIZE)
        for _ in range(30):
            w.change_font_size(-1)
        self.assertEqual(w.font_size, MIN_FONT_SIZE)
        w.change_font_size(0)
        self.assertEqual(w.font().pointSize(), DEFAULT_FONT_SIZE)
        menus = {a.text(): a.menu() for a in w.menuBar().actions()}
        self.assertEqual([a.text() for a in menus['보기'].actions()], ['글자 크게', '글자 작게', '기본 크기'])

    # --- T30 입력 편집기 ---

    def matrix_texts(self, w):
        m = w.people_editor.matrix
        return [[m.item(r, c).text() for c in range(m.columnCount())] for r in range(m.rowCount())]

    def test_T30_edit_tables_and_generate_unsaved(self):
        p, h = self.write([project(), project('사업2')], [person({'사업1': '0.60000', '사업2': '0.40000'})])
        w = self.window()
        w.choose_people(str(h))
        editor, matrix = w.people_editor, w.people_editor.matrix
        self.assertEqual([w.project_editor.table.item(0, c).text() for c in range(4)],
                         ['사업1', '사업1', '2027-01-01', '2027-12-31'])
        self.assertEqual([matrix.horizontalHeaderItem(c).text() for c in range(matrix.columnCount())],
                         ['이름', '사업1', '사업2', '사업 합계', '공백', '합계'])
        self.assertEqual(self.matrix_texts(w), [['홍길동', '0.60000', '0.40000', '1.00000', '0.00000', '1.00000']])
        self.assertEqual(editor.year_edit.text(), '2027')

        # 셀을 고치면 합계가 바로 바뀌고 1이 아니면 표시한다. 저장 전이어도 생성은 화면 내용으로 계산한다.
        matrix.item(0, 1).setText('0.5')
        self.assertEqual(editor.doc.people[0].allocations['사업1'], '0.5')
        self.assertEqual(self.matrix_texts(w)[0][3:], ['0.90000', '0.00000', '0.90000'])
        self.assertTrue(matrix.item(0, 5).toolTip())
        self.assertEqual(w.tabs.tabText(w.PEOPLE_TAB), '사람 입력 *')
        w.generate()
        self.assertIn('E101', [row[1] for row in self.texts(w.issue_view)])
        matrix.item(0, 2).setText('0.5')
        self.assertEqual(matrix.item(0, 5).toolTip(), '')
        w.generate()
        self.assertTrue(w.excel_button.isEnabled())
        self.assertIn('0.60000', h.read_text(encoding='utf-8'))  # 저장하기 전에는 파일이 그대로다.

        # 형식이 틀린 값은 칸을 표시하고 안내를 띄운다. 빈 칸은 할당 없음이다.
        matrix.item(0, 1).setText('0.5.1')
        self.assertTrue(matrix.item(0, 1).toolTip())
        self.assertEqual(self.matrix_texts(w)[0][3], '?')
        matrix.item(0, 1).setText('')
        self.assertNotIn('사업1', editor.doc.people[0].allocations)

        # 엑셀 범위 붙여넣기: 현재 칸부터 채우고 표 밖·계산 칸은 건너뛴다.
        matrix.setCurrentCell(0, 1)
        matrix.paste('0.25\t0.75\t9\n1\t1')
        self.assertEqual(self.matrix_texts(w)[0][1:6], ['0.25', '0.75', '1.00000', '0.00000', '1.00000'])
        matrix.setCurrentCell(0, 1)
        matrix.selectRow(0)
        self.assertTrue(matrix.copy_selection().startswith('홍길동\t0.25\t0.75'))

    def test_T30_absences_and_year(self):
        _, h = self.write([project(year=2026)], [person({'사업1': '0.95430'})], year=2026)
        w = self.window()
        w.choose_people(str(h))
        editor = w.people_editor
        self.assertEqual(self.matrix_texts(w)[0][2:], ['0.95430', '0.00000', '0.95430'])
        editor.add_absence()
        absences = editor.absences
        absences.item(0, 0).setText('2026-07-15')
        self.assertTrue(absences.item(0, 1).toolTip())  # 종료일이 비어 있음
        absences.item(0, 1).setText('2026.7.31')
        absences.item(0, 2).setText('출산휴가')
        self.assertEqual(self.matrix_texts(w)[0][2:], ['0.95430', '0.04570', '1.00000'])
        self.assertEqual(editor.doc.people[0].absences[0].reason, '출산휴가')
        editor.year_edit.setText('20x6')
        editor.year_edited('20x6')
        self.assertEqual(self.matrix_texts(w)[0][3], '?')
        self.assertTrue(editor.year_edit.toolTip())
        editor.year_edited('2026')
        w.generate()
        self.assertTrue(w.excel_button.isEnabled())

    def test_T30_project_editing_rename_and_remove(self):
        _, h = self.write([project(), project('사업2')], [person({'사업1': '0.60000', '사업2': '0.40000'})])
        w = self.window()
        w.choose_people(str(h))
        table, years = w.project_editor.table, w.project_editor.years
        # 약칭을 바꾸면 사람 참여율도 따라간다.
        table.item(0, 1).setText('연구')
        self.assertEqual(w.people_editor.doc.people[0].allocations, {'연구': '0.60000', '사업2': '0.40000'})
        self.assertEqual(w.tabs.tabText(w.PROJECTS_TAB), '사업 입력 *')
        self.assertEqual(w.tabs.tabText(w.PEOPLE_TAB), '사람 입력 *')
        # 입력 확인: 약칭 공백·중복, 날짜, 연도·배정·제외 월.
        # 다른 사업과 같은 약칭을 거쳐도 그 사업의 참여율이 옮겨지지 않는다.
        table.item(1, 1).setText('연구')
        self.assertTrue(table.item(1, 1).toolTip())
        table.item(1, 1).setText('사업 2')
        self.assertTrue(table.item(1, 1).toolTip())
        table.item(1, 1).setText('사업2')
        self.assertEqual(w.people_editor.doc.people[0].allocations, {'연구': '0.60000', '사업2': '0.40000'})
        table.item(1, 3).setText('2026-12-31')
        self.assertIn('시작일', table.item(1, 3).toolTip())
        table.item(1, 3).setText('2027-12-31')
        table.setCurrentCell(1, 0)
        years.item(0, 2).setText('4, 13')
        self.assertTrue(years.item(0, 2).toolTip())
        years.item(0, 2).setText('4, 5, 6')
        w.project_editor.add_year()
        self.assertEqual(w.project_editor.projects[1].years[1].year, '2028')
        years.item(1, 1).setText('')
        self.assertTrue(years.item(1, 1).toolTip())
        w.project_editor.remove_year()
        self.assertEqual(w.project_editor.projects[1].years[0].excluded, '4, 5, 6')
        # 사업2를 4~6월 제외하면 사업1(연구)이 그 달을 채운다.
        w.generate()
        self.assertTrue(w.excel_button.isEnabled(), self.texts(w.issue_view))
        # 참여율이 있는 사업을 지울 때는 확인하고, 취소하면 그대로 둔다.
        self.confirm_answer = False
        w.project_editor.remove_project()
        self.assertEqual(len(w.project_editor.projects), 2)
        self.confirm_answer = True
        w.project_editor.remove_project()
        self.assertEqual([p.alias for p in w.project_editor.projects], ['연구'])
        self.assertEqual(w.people_editor.doc.people[0].allocations, {'연구': '0.60000'})

    def test_T30_unknown_alias_and_dropped_fields(self):
        p, h = self.write([project()], [person({'사업1': '0.5', '없는사업': '0.5'})])
        text = p.read_text(encoding='utf-8').replace('- name: 사업1', '- id: 7\n  name: 사업1')
        p.write_text(text, encoding='utf-8')
        w = self.window()
        w.choose_people(str(h))
        self.assertEqual(self.warnings[-1][0], '지원하지 않는 항목')
        self.assertIn('projects[0].id', self.warnings[-1][1])
        headers = [w.people_editor.matrix.horizontalHeaderItem(c).text() for c in range(3)]
        self.assertEqual(headers, ['이름', '사업1', '없는사업 (미등록)'])
        self.assertTrue(w.people_editor.matrix.item(0, 2).toolTip())
        w.generate()
        self.assertIn('E007', [row[1] for row in self.texts(w.issue_view)])
        # 값을 지우면 미등록 열이 사라진다.
        w.people_editor.matrix.item(0, 2).setText('')
        w.people_editor.refresh()
        self.assertEqual(w.people_editor.matrix.columnCount(), 5)

    def test_T30_save_backup_and_new_documents(self):
        p, h = self.write([project()], [person()])
        original = p.read_text(encoding='utf-8')
        w = self.window()
        w.choose_people(str(h))
        w.project_editor.table.item(0, 0).setText('연구 지원 사업')
        self.assertTrue(w.save('projects'))
        self.assertEqual((self.path / 'projects.yaml.bak').read_text(encoding='utf-8'), original)
        self.assertEqual(w.tabs.tabText(w.PROJECTS_TAB), '사업 입력')
        self.assertIn('"연구 지원 사업"', p.read_text(encoding='utf-8'))
        # 같은 실행에서 다시 저장해도 첫 원본 백업은 그대로 둔다.
        w.project_editor.table.item(0, 0).setText('운영')
        self.assertTrue(w.save_all())
        self.assertEqual((self.path / 'projects.yaml.bak').read_text(encoding='utf-8'), original)

        # 새로 만들기 → 입력 → 다른 이름으로 저장한 파일은 CLI와 같은 규칙으로 읽힌다.
        w.new_document('projects')
        w.new_document('people')
        self.assertEqual(w.projects_edit.text(), '')
        pe, me = w.project_editor, w.people_editor
        pe.add_project()
        for column, value in enumerate(['새 사업', '새사업', '2026-01-01', '2026-12-31']):
            pe.table.item(0, column).setText(value)
        pe.years.item(0, 1).setText('2')
        me.year_edited('2026')
        me.add_person()
        me.matrix.item(0, 0).setText('이영희')
        me.matrix.item(0, 1).setText('1')
        w.generate()
        self.assertTrue(w.excel_button.isEnabled(), self.texts(w.issue_view))
        folder = self.path / 'new'
        folder.mkdir()
        self.save_paths = [str(folder / 'projects.yaml'), str(folder / 'people_2026.yaml')]
        self.assertTrue(w.save_all())
        inputs, issues = load_inputs(folder / 'projects.yaml', folder / 'people_2026.yaml')
        self.assertIsNotNone(inputs, issues)
        self.assertEqual(inputs.people['이영희'].targets, {'새사업': 100000})
        self.assertFalse((folder / 'projects.yaml.bak').exists())

        # 저장하지 않은 변경이 있으면 다른 파일을 열기 전에 묻는다.
        me.matrix.item(0, 1).setText('0.9')
        self.save_answers = ['cancel']
        w.choose_people(str(h))
        self.assertEqual(w.people_edit.text(), str(folder / 'people_2026.yaml'))
        self.save_answers = ['discard']
        w.choose_people(str(h))
        self.assertEqual(w.people_edit.text(), str(h))

    def test_T29_find_projects_file(self):
        p, h = self.write([project()], [person()])
        w = self.window()
        w.choose_people(str(h))
        self.assertEqual(w.projects_edit.text(), str(p.resolve()))
        # 자동으로 채운 값은 다른 사람 파일을 고를 때 다시 찾는다.
        (self.path / 'people' / 'projects.yaml').write_text('', encoding='utf-8')
        w.choose_people(str(h))
        self.assertEqual(w.projects_edit.text(), str((self.path / 'people' / 'projects.yaml').resolve()))
        # 직접 고른 사업 파일은 덮어쓰지 않는다.
        chosen = self.path / 'other' / 'projects.yaml'
        chosen.parent.mkdir()
        chosen.write_bytes(p.read_bytes())
        w.choose_projects(str(chosen))
        w.choose_people(str(h))
        self.assertEqual(w.projects_edit.text(), str(chosen))
        # 사람 파일 폴더와 바로 위 폴더만 찾고 그보다 위는 보지 않는다.
        empty = self.path / 'a' / 'b' / 'people.yaml'
        empty.parent.mkdir(parents=True)
        self.assertIsNone(find_projects_file(empty))


if __name__ == '__main__':
    unittest.main()
