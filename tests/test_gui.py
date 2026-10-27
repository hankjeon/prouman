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

    def window(self):
        window = MainWindow(self.settings)
        window.warn = lambda title, text: self.warnings.append((title, text))
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
        self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())], ['검증', '요약', '개인상세', '사업상세'])
        self.assertEqual(w.tabs.currentIndex(), 1)
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

        # 생성에 성공한 경로를 기억해 다음 창에 미리 채운다.
        again = self.window()
        self.assertEqual(again.projects_edit.text(), str(self.path / 'projects.yaml'))
        self.assertEqual(again.people_edit.text(), str(self.path / 'people' / 'people.yaml'))

    def test_T29_errors_clear_results(self):
        w = self.generated([project()], [person()])
        self.assertTrue(w.excel_button.isEnabled())
        self.write([project()], [person({'사업1': '0.90000'})])
        w.generate()
        self.assertEqual(w.tabs.currentIndex(), 0)
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

        (self.path / 'projects.yaml').unlink()
        w.generate()
        self.assertIn(['ERROR', 'E001'], [row[:2] for row in self.texts(w.issue_view)])
        self.assertFalse(w.excel_button.isEnabled())

        w.projects_edit.clear()
        w.generate()
        self.assertEqual(self.warnings[-1][0], '파일 선택')

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
        self.assertEqual([a.text() for a in w.menuBar().actions()[0].menu().actions()],
                         ['글자 크게', '글자 작게', '기본 크기'])

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
        w.choose_projects('직접.yaml')
        w.choose_people(str(h))
        self.assertEqual(w.projects_edit.text(), '직접.yaml')
        # 사람 파일 폴더와 바로 위 폴더만 찾고 그보다 위는 보지 않는다.
        empty = self.path / 'a' / 'b' / 'people.yaml'
        empty.parent.mkdir(parents=True)
        self.assertIsNone(find_projects_file(empty))


if __name__ == '__main__':
    unittest.main()
