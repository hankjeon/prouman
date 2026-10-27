"""PySide6 데스크톱 화면. 사업·사람 YAML을 골라 계산하고 결과를 그리드로 보여 준다."""
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableView, QTabWidget, QVBoxLayout,
                               QWidget)

from .engine import calculate
from .loading import load_inputs
from .models import amount
from .rendering import issue_counts
from .tables import (issue_table, people_summary_table, person_detail_table, project_detail_table,
                     project_summary_table)

YAML_FILTER = 'YAML 파일 (*.yaml *.yml);;모든 파일 (*)'
SELECTION_COLOR = QColor('#b7dcff')
CURRENT_CELL_COLOR = QColor('#1a73e8')
SEVERITY_COLORS = {'ERROR': QColor('#c62828'), 'WARNING': QColor('#b26a00')}
# Windows 기본 UI 글꼴(9pt)은 고해상도 모니터에서 작아서 기본값을 키운다.
DEFAULT_FONT_SIZE, MIN_FONT_SIZE, MAX_FONT_SIZE = 10, 7, 24


class ResultTableModel(QAbstractTableModel):
    """읽기 전용 표. 수량(int)은 5자리 고정 소수 문자열로 표시하고 오른쪽 정렬한다."""

    def __init__(self, headers=(), rows=(), bold_label=None, colors=None):
        super().__init__()
        self.headers, self.rows = list(headers), [list(row) for row in rows]
        self.bold_label, self.colors = bold_label, colors or {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def text(self, row, column):
        value = self.rows[row][column]
        return amount(value) if type(value) is int else str(value)

    def data(self, index, role=Qt.DisplayRole):
        # 모델을 바꾸는 동안 뷰가 이전 범위를 물을 수 있으므로 범위를 확인한다.
        if not index.isValid() or index.row() >= len(self.rows) or index.column() >= len(self.headers):
            return None
        row = self.rows[index.row()]
        value = row[index.column()]
        if role == Qt.DisplayRole:
            return self.text(index.row(), index.column())
        if role == Qt.ToolTipRole and type(value) is str and value:
            return value  # 잘린 긴 검증 메시지를 마우스를 올려 볼 수 있게 한다.
        if role == Qt.TextAlignmentRole and type(value) is int:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.FontRole and self.bold_label is not None and row[0] == self.bold_label:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ForegroundRole and row[0] in self.colors:
            return self.colors[row[0]]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        return None


class SelectionDelegate(QStyledItemDelegate):
    """선택 셀을 직접 칠한다.

    Windows 11 기본 스타일은 선택 셀을 거의 보이지 않는 회색으로, 현재 셀은 흰 바탕으로 그리고
    스타일시트로도 포커스 상태에 따라 일관되게 바뀌지 않는다. 포커스와 관계없이 선택 범위는 같은 색,
    현재 셀은 테두리로 표시한다. 글자 색(검증 행 색 등)은 그대로 둔다.
    """

    def paint(self, painter, option, index):
        state = option.state
        plain = QStyleOptionViewItem(option)
        plain.state = state & ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, SELECTION_COLOR)
        super().paint(painter, plain, index)
        if state & QStyle.StateFlag.State_HasFocus:
            painter.save()
            painter.setPen(CURRENT_CELL_COLOR)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()


class CopyTableView(QTableView):
    """선택한 셀 범위를 탭으로 구분한 텍스트로 복사한다. 선택하지 않은 칸은 비워 둔다."""

    def __init__(self):
        super().__init__()
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.fit_rows()
        self.setItemDelegate(SelectionDelegate(self))
        self.setModel(ResultTableModel())

    def fit_rows(self):
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)

    def changeEvent(self, event):
        # 글자 크기가 바뀌면 행 높이와 열 너비를 새 크기에 맞춘다.
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self.fit_rows()
            self.resizeColumnsToContents()

    def show_table(self, headers, rows, **options):
        self.setModel(ResultTableModel(headers, rows, **options))
        self.resizeColumnsToContents()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def copy_selection(self):
        indexes = self.selectedIndexes()
        if not indexes:
            return ''
        selected = {(i.row(), i.column()) for i in indexes}
        rows = range(min(r for r, _ in selected), max(r for r, _ in selected) + 1)
        columns = range(min(c for _, c in selected), max(c for _, c in selected) + 1)
        model = self.model()
        text = '\n'.join('\t'.join(model.text(r, c) if (r, c) in selected else '' for c in columns) for r in rows)
        QGuiApplication.clipboard().setText(text)
        return text


def find_projects_file(people_path):
    # 사람 파일 폴더와 그 상위 폴더 순서로 찾는다. 권장 구조는 작업폴더/people/people_연도.yaml이다.
    folder = Path(people_path).resolve().parent
    for candidate in (folder / 'projects.yaml', folder.parent / 'projects.yaml'):
        if candidate.is_file():
            return str(candidate)
    return None


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('prouman', 'prouman')
        self.result = None
        self.auto_projects = None
        self.setWindowTitle('인력 배분')
        self.resize(1200, 760)

        self.projects_edit, self.people_edit = QLineEdit(), QLineEdit()
        projects_button, people_button = QPushButton('찾기'), QPushButton('찾기')
        projects_button.clicked.connect(self.browse_projects)
        people_button.clicked.connect(self.browse_people)
        self.generate_button, self.excel_button = QPushButton('생성'), QPushButton('엑셀 저장')
        self.generate_button.clicked.connect(self.generate)
        self.excel_button.clicked.connect(self.browse_excel)
        self.excel_button.setEnabled(False)
        self.status = QLabel('사업 파일과 사람 파일을 고른 뒤 생성을 누르세요.')

        files = QGridLayout()
        files.addWidget(QLabel('사업 파일'), 0, 0)
        files.addWidget(self.projects_edit, 0, 1)
        files.addWidget(projects_button, 0, 2)
        files.addWidget(QLabel('사람 파일'), 1, 0)
        files.addWidget(self.people_edit, 1, 1)
        files.addWidget(people_button, 1, 2)
        actions = QHBoxLayout()
        actions.addWidget(self.generate_button)
        actions.addWidget(self.excel_button)
        actions.addStretch()
        actions.addWidget(self.status)

        self.issue_view = CopyTableView()
        self.people_summary_view, self.project_summary_view = CopyTableView(), CopyTableView()
        summary = QSplitter(Qt.Horizontal)
        for title, view in (('개인별 연간 요약', self.people_summary_view),
                            ('사업별 연간 요약', self.project_summary_view)):
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(QLabel(title))
            layout.addWidget(view)
            summary.addWidget(box)
        # 개인별 요약은 4열, 사업별 요약은 사업명이 있어 더 넓게 둔다.
        summary.setSizes([400, 800])
        self.person_list = QListWidget()
        self.person_list.currentTextChanged.connect(self.show_person)
        self.person_view = CopyTableView()
        people = QSplitter(Qt.Horizontal)
        people.addWidget(self.person_list)
        people.addWidget(self.person_view)
        people.setSizes([180, 1000])
        self.project_view = CopyTableView()
        self.tabs = QTabWidget()
        for widget, title in ((self.issue_view, '검증'), (summary, '요약'), (people, '개인상세'),
                              (self.project_view, '사업상세')):
            self.tabs.addTab(widget, title)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(files)
        layout.addLayout(actions)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        view_menu = self.menuBar().addMenu('보기')
        for text, keys, step in (('글자 크게', [QKeySequence.ZoomIn, QKeySequence('Ctrl+=')], 1),
                                 ('글자 작게', [QKeySequence.ZoomOut], -1),
                                 ('기본 크기', [QKeySequence('Ctrl+0')], 0)):
            action = QAction(text, self)
            action.setShortcuts(keys)
            action.triggered.connect(lambda checked=False, step=step: self.change_font_size(step))
            view_menu.addAction(action)
        self.set_font_size(self.settings.value('font_size', DEFAULT_FONT_SIZE, type=int), save=False)

        self.projects_edit.setText(self.settings.value('projects_file', '', type=str))
        self.people_edit.setText(self.settings.value('people_file', '', type=str))

    def warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def set_font_size(self, size, save=True):
        # 창 글꼴만 바꾸면 하위 위젯 전체에 전파된다. 사용자가 바꾼 크기만 기억해 다음 실행에 쓴다.
        # 시작할 때 저장하지 않아야 나중에 기본값을 바꿔도 크기를 바꾼 적 없는 사용자에게 적용된다.
        self.font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        font = QFont(self.font())
        font.setPointSize(self.font_size)
        self.setFont(font)
        if save:
            self.settings.setValue('font_size', self.font_size)

    def change_font_size(self, step):
        self.set_font_size(self.font_size + step if step else DEFAULT_FONT_SIZE)

    def start_folder(self):
        for text in (self.people_edit.text(), self.projects_edit.text()):
            if text and Path(text).parent.is_dir():
                return str(Path(text).parent)
        return ''

    def browse_projects(self):
        path, _ = QFileDialog.getOpenFileName(self, '사업 파일 선택', self.start_folder(), YAML_FILTER)
        if path:
            self.choose_projects(path)

    def browse_people(self):
        path, _ = QFileDialog.getOpenFileName(self, '사람 파일 선택', self.start_folder(), YAML_FILTER)
        if path:
            self.choose_people(path)

    def choose_projects(self, path):
        self.projects_edit.setText(path)
        self.auto_projects = None

    def choose_people(self, path):
        self.people_edit.setText(path)
        # 직접 고른 사업 파일은 덮어쓰지 않는다.
        current = self.projects_edit.text()
        if current and current != self.auto_projects:
            return
        found = find_projects_file(path)
        if found:
            self.projects_edit.setText(found)
            self.auto_projects = found

    def clear_results(self):
        for view in (self.people_summary_view, self.project_summary_view, self.person_view, self.project_view):
            view.show_table([], [])
        self.person_list.clear()

    def generate(self):
        projects_path, people_path = self.projects_edit.text().strip(), self.people_edit.text().strip()
        if not projects_path or not people_path:
            self.warn('파일 선택', '사업 파일과 사람 파일을 모두 고르세요.')
            return
        self.result = None
        self.excel_button.setEnabled(False)
        self.clear_results()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            inputs, issues = load_inputs(projects_path, people_path)
            result = calculate(inputs, issues) if inputs is not None else None
        except Exception as exc:  # 예상하지 못한 실패도 창을 닫지 않고 알린다.
            self.issue_view.show_table([], [])
            self.status.setText('계산 실패')
            self.warn('계산 실패', f'{type(exc).__name__}: {exc}')
            return
        finally:
            QApplication.restoreOverrideCursor()

        issues = result.issues if result is not None else issues
        self.issue_view.show_table(*issue_table(issues), colors=SEVERITY_COLORS)
        errors, warnings = issue_counts(issues)
        counts = f'ERROR {errors}건 / WARNING {warnings}건'
        if result is None or result.has_errors:
            self.status.setText(f'{counts} · 오류로 결과를 만들지 않았습니다')
            self.tabs.setCurrentIndex(0)
            return

        self.result = result
        self.status.setText(f'관리 연도 {result.inputs.year} · {counts}')
        self.people_summary_view.show_table(*people_summary_table(result))
        self.project_summary_view.show_table(*project_summary_table(result))
        self.project_view.show_table(*project_detail_table(result))
        self.person_list.addItems(list(result.inputs.people))
        self.person_list.setCurrentRow(0)
        self.excel_button.setEnabled(True)
        self.tabs.setCurrentIndex(1)
        self.settings.setValue('projects_file', projects_path)
        self.settings.setValue('people_file', people_path)
        self.settings.sync()

    def show_person(self, name):
        if self.result is not None and name in self.result.inputs.people:
            self.person_view.show_table(*person_detail_table(self.result, name), bold_label='합계')
        else:
            self.person_view.show_table([], [])

    def browse_excel(self):
        if self.result is None:
            return
        default = Path(self.people_edit.text()).resolve().parent / f'결과_{self.result.inputs.year}.xlsx'
        path, _ = QFileDialog.getSaveFileName(self, '엑셀 저장', str(default), '엑셀 파일 (*.xlsx)')
        if path:
            self.save_excel(path if path.lower().endswith('.xlsx') else path + '.xlsx')

    def save_excel(self, path):
        # openpyxl은 엑셀 저장에만 필요하므로 이때 불러온다.
        from .excel import write_workbook
        try:
            write_workbook(self.result, path)
        except OSError as exc:
            self.warn('엑셀 저장 실패', f'{path}\n{exc.strerror or exc}\n파일이 열려 있으면 닫고 다시 저장하세요.')
            return False
        self.status.setText(f'엑셀 저장: {path}')
        return True


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
