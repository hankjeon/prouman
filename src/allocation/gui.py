"""PySide6 데스크톱 화면. 사업·사람 입력을 표로 편집하고, 계산 결과를 그리드로 보여 준다."""
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QSplitter,
                               QTableView, QTabWidget, QVBoxLayout, QWidget)

from .document import DocumentError, PeopleDoc, dump_people, dump_projects, read_people, read_projects
from .editor import PeopleEditor, ProjectEditor, SelectionDelegate
from .engine import calculate
from .loading import decode_text, load_inputs
from .models import amount
from .rendering import issue_counts
from .tables import (issue_table, people_summary_table, person_detail_table, project_detail_table,
                     project_summary_table)

YAML_FILTER = 'YAML 파일 (*.yaml *.yml);;모든 파일 (*)'
KINDS = {'projects': '사업', 'people': '사람'}
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
    # 탭 순서: 입력 탭 다음에 결과 탭을 둔다.
    PROJECTS_TAB, PEOPLE_TAB, ISSUES_TAB, SUMMARY_TAB = 0, 1, 2, 3

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('prouman', 'prouman')
        self.result = None
        self.auto_projects = None
        self.dirty = {'projects': False, 'people': False}
        self.backed_up = set()  # 이번 실행에서 .bak을 만든 파일. 첫 저장 직전의 원본만 백업한다.
        self.setWindowTitle('인력 배분')
        self.resize(1200, 760)

        self.projects_edit, self.people_edit = QLineEdit(), QLineEdit()
        self.edits = {'projects': self.projects_edit, 'people': self.people_edit}
        files = QGridLayout()
        for row, kind in enumerate(KINDS):
            edit = self.edits[kind]
            edit.setReadOnly(True)
            edit.setPlaceholderText('(새 파일 - 아직 저장하지 않음)')
            buttons = [('열기', self.browse_projects if kind == 'projects' else self.browse_people),
                       ('새로 만들기', lambda checked=False, kind=kind: self.new_document(kind)),
                       ('저장', lambda checked=False, kind=kind: self.save(kind)),
                       ('다른 이름으로', lambda checked=False, kind=kind: self.save_as(kind))]
            files.addWidget(QLabel(f'{KINDS[kind]} 파일'), row, 0)
            files.addWidget(edit, row, 1)
            for column, (text, slot) in enumerate(buttons, 2):
                button = QPushButton(text)
                button.clicked.connect(slot)
                files.addWidget(button, row, column)
        self.generate_button, self.excel_button = QPushButton('생성'), QPushButton('엑셀 저장')
        self.generate_button.setToolTip('입력 탭의 현재 내용으로 계산합니다. 저장하지 않아도 됩니다.')
        self.generate_button.clicked.connect(self.generate)
        self.excel_button.clicked.connect(self.browse_excel)
        self.excel_button.setEnabled(False)
        self.status = QLabel('사업·사람 파일을 열거나 새로 만들어 입력한 뒤 생성을 누르세요.')
        actions = QHBoxLayout()
        actions.addWidget(self.generate_button)
        actions.addWidget(self.excel_button)
        actions.addStretch()
        actions.addWidget(self.status)

        self.project_editor = ProjectEditor()
        self.people_editor = PeopleEditor(lambda: self.project_editor.projects)
        self.project_editor.default_year = lambda: self.people_editor.doc.year
        self.project_editor.allow_remove = self.allow_remove_project
        self.project_editor.changed.connect(self.projects_changed)
        self.project_editor.alias_renamed.connect(self.alias_renamed)
        self.project_editor.alias_removed.connect(self.people_editor.remove_alias)
        self.people_editor.changed.connect(lambda: self.set_dirty('people', True))

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
        for widget, title in ((self.project_editor, '사업 입력'), (self.people_editor, '사람 입력'),
                              (self.issue_view, '검증'), (summary, '요약'), (people, '개인상세'),
                              (self.project_view, '사업상세')):
            self.tabs.addTab(widget, title)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(files)
        layout.addLayout(actions)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu('파일')
        save_all = QAction('모두 저장', self)
        save_all.setShortcut(QKeySequence.Save)
        save_all.triggered.connect(self.save_all)
        file_menu.addAction(save_all)
        view_menu = self.menuBar().addMenu('보기')
        for text, keys, step in (('글자 크게', [QKeySequence.ZoomIn, QKeySequence('Ctrl+=')], 1),
                                 ('글자 작게', [QKeySequence.ZoomOut], -1),
                                 ('기본 크기', [QKeySequence('Ctrl+0')], 0)):
            action = QAction(text, self)
            action.setShortcuts(keys)
            action.triggered.connect(lambda checked=False, step=step: self.change_font_size(step))
            view_menu.addAction(action)
        self.set_font_size(self.settings.value('font_size', DEFAULT_FONT_SIZE, type=int), save=False)

        # 지난번 파일을 다시 연다. 열 수 없으면 조용히 빈 입력으로 시작한다.
        for kind in KINDS:
            path = self.settings.value(f'{kind}_file', '', type=str)
            if path and Path(path).is_file():
                self.load(kind, path, quiet=True)

    # --- 공통 대화상자 (테스트에서 바꿔 끼운다) ---

    def warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def ask_save(self, kind):
        """저장하지 않은 변경이 있을 때 'save' / 'discard' / 'cancel'을 돌려준다."""
        answer = QMessageBox.question(
            self, '저장하지 않은 변경', f'{KINDS[kind]} 입력에 저장하지 않은 변경이 있습니다. 저장할까요?',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return {QMessageBox.Save: 'save', QMessageBox.Discard: 'discard'}.get(answer, 'cancel')

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def ask_save_path(self, kind):
        default = 'projects.yaml' if kind == 'projects' else f'people_{self.people_editor.doc.year}.yaml'
        path, _ = QFileDialog.getSaveFileName(self, f'{KINDS[kind]} 파일 저장',
                                              str(Path(self.start_folder() or '.') / default), YAML_FILTER)
        return path

    # --- 글자 크기 ---

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

    # --- 입력 파일 열기·새로 만들기·저장 ---

    def set_dirty(self, kind, dirty):
        self.dirty[kind] = dirty
        index = self.PROJECTS_TAB if kind == 'projects' else self.PEOPLE_TAB
        self.tabs.setTabText(index, f'{KINDS[kind]} 입력' + (' *' if dirty else ''))

    def projects_changed(self):
        self.set_dirty('projects', True)
        self.people_editor.refresh()

    def alias_renamed(self, old, new):
        if self.people_editor.count_alias(old):
            self.set_dirty('people', True)
        self.people_editor.rename_alias(old, new)

    def allow_remove_project(self, project):
        count = self.people_editor.count_alias(project.alias)
        name = project.name or project.alias or '(이름 없음)'
        text = f'사업 "{name}"을(를) 삭제할까요?'
        if count:
            text += f'\n이 사업에 입력한 참여율 {count}건도 함께 지워집니다.'
        if not self.confirm('사업 삭제', text):
            return False
        if count:
            self.set_dirty('people', True)
        return True

    def keep_changes(self, kind):
        """다른 파일로 바꾸기 전에 저장하지 않은 변경을 처리한다. 진행해도 되면 True."""
        if not self.dirty[kind]:
            return True
        answer = self.ask_save(kind)
        return answer == 'discard' or answer == 'save' and self.save(kind)

    def start_folder(self):
        for text in (self.people_edit.text(), self.projects_edit.text()):
            if text and Path(text).parent.is_dir():
                return str(Path(text).parent)
        return ''

    def browse_projects(self):
        path, _ = QFileDialog.getOpenFileName(self, '사업 파일 열기', self.start_folder(), YAML_FILTER)
        if path:
            self.choose_projects(path)

    def browse_people(self):
        path, _ = QFileDialog.getOpenFileName(self, '사람 파일 열기', self.start_folder(), YAML_FILTER)
        if path:
            self.choose_people(path)

    def choose_projects(self, path):
        if self.keep_changes('projects') and self.load('projects', path):
            self.auto_projects = None

    def choose_people(self, path):
        if not self.keep_changes('people') or not self.load('people', path):
            return
        # 사업 파일을 직접 골랐거나 편집 중이면 바꾸지 않는다.
        current = self.projects_edit.text()
        if self.dirty['projects'] or current and current != self.auto_projects:
            return
        found = find_projects_file(path)
        if found and self.load('projects', found):
            self.auto_projects = found

    def load(self, kind, path, quiet=False):
        try:
            text = decode_text(Path(path).read_bytes())
            document, dropped = read_projects(text) if kind == 'projects' else read_people(text)
        except (OSError, UnicodeError, DocumentError) as exc:
            if quiet:
                self.status.setText(f'{KINDS[kind]} 파일을 열지 못했습니다: {path}')
            else:
                self.warn(f'{KINDS[kind]} 파일 열기 실패', f'{path}\n{exc}')
            return False
        if kind == 'projects':
            self.project_editor.set_projects(document)
            self.people_editor.refresh()
        else:
            self.people_editor.set_doc(document)
        self.edits[kind].setText(str(path))
        self.backed_up.discard(str(Path(path).resolve()))
        self.set_dirty(kind, False)
        self.settings.setValue(f'{kind}_file', str(path))
        if dropped and not quiet:
            self.warn('지원하지 않는 항목', '표 편집기에서 다루지 않는 다음 항목은 저장할 때 빠집니다.\n'
                      + '\n'.join(dropped[:20]) + ('\n...' if len(dropped) > 20 else ''))
        return True

    def new_document(self, kind):
        if not self.keep_changes(kind):
            return
        if kind == 'projects':
            self.project_editor.set_projects([])
            self.people_editor.refresh()
            self.auto_projects = None
        else:
            self.people_editor.set_doc(PeopleDoc())
        self.edits[kind].setText('')
        self.set_dirty(kind, False)
        self.tabs.setCurrentIndex(self.PROJECTS_TAB if kind == 'projects' else self.PEOPLE_TAB)

    def text(self, kind):
        if kind == 'projects':
            return dump_projects(self.project_editor.projects)
        return dump_people(self.people_editor.doc)

    def save(self, kind, path=None):
        """저장했으면 True. 경로가 없으면 저장 위치를 묻는다. 덮어쓰기 전 원본을 .bak으로 남긴다."""
        path = path or self.edits[kind].text() or self.ask_save_path(kind)
        if not path:
            return False
        target = Path(path)
        try:
            key = str(target.resolve())
            if target.exists() and key not in self.backed_up:
                shutil.copy2(target, target.with_name(target.name + '.bak'))
                self.backed_up.add(key)
            target.write_text(self.text(kind), encoding='utf-8')
        except OSError as exc:
            self.warn(f'{KINDS[kind]} 파일 저장 실패', f'{path}\n{exc.strerror or exc}')
            return False
        self.edits[kind].setText(str(path))
        self.set_dirty(kind, False)
        self.settings.setValue(f'{kind}_file', str(path))
        self.status.setText(f'{KINDS[kind]} 파일 저장: {path}')
        return True

    def save_as(self, kind):
        path = self.ask_save_path(kind)
        return bool(path) and self.save(kind, path)

    def save_all(self):
        return all(self.save(kind) for kind in KINDS if self.dirty[kind])

    def closeEvent(self, event):
        if all(self.keep_changes(kind) for kind in KINDS):
            event.accept()
        else:
            event.ignore()

    # --- 계산 결과 ---

    def clear_results(self):
        for view in (self.people_summary_view, self.project_summary_view, self.person_view, self.project_view):
            view.show_table([], [])
        self.person_list.clear()

    def generate(self):
        """입력 탭의 현재 내용(저장 전이어도)을 파일과 같은 규칙으로 검증하고 계산한다."""
        labels = [self.edits[kind].text() or f'(새 {KINDS[kind]} 파일)' for kind in KINDS]
        self.result = None
        self.excel_button.setEnabled(False)
        self.clear_results()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            inputs, issues = load_inputs(*labels, texts={kind: self.text(kind) for kind in KINDS})
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
            self.tabs.setCurrentIndex(self.ISSUES_TAB)
            return

        self.result = result
        self.status.setText(f'관리 연도 {result.inputs.year} · {counts}')
        self.people_summary_view.show_table(*people_summary_table(result))
        self.project_summary_view.show_table(*project_summary_table(result))
        self.project_view.show_table(*project_detail_table(result))
        self.person_list.addItems(list(result.inputs.people))
        self.person_list.setCurrentRow(0)
        self.excel_button.setEnabled(True)
        self.tabs.setCurrentIndex(self.SUMMARY_TAB)

    def show_person(self, name):
        if self.result is not None and name in self.result.inputs.people:
            self.person_view.show_table(*person_detail_table(self.result, name), bold_label='합계')
        else:
            self.person_view.show_table([], [])

    def browse_excel(self):
        if self.result is None:
            return
        folder = Path(self.start_folder() or '.').resolve()
        default = folder / f'결과_{self.result.inputs.year}.xlsx'
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
