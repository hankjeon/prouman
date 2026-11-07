"""PySide6 데스크톱 화면. 사업·사람 입력을 표로 편집하고, 계산 결과를 그리드로 보여 준다."""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QEvent, QModelIndex, QSettings, Qt,
                            qInstallMessageHandler)
from PySide6.QtGui import QAction, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton,
                               QSizePolicy, QSplitter, QStackedWidget, QTableView, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme
from .document import (DocumentError, PeopleDoc, PlanDoc, detect_kind, dump_people, dump_plan, dump_projects,
                       read_people, read_plan, read_projects, valid_year)
from .editor import PeopleEditor, ProjectEditor, SelectionDelegate
from .engine import calculate
from .loading import decode_text, load_inputs
from .models import amount
from .planning_gui import PlanningPage
from .rendering import issue_counts, project_summary
from .tables import (issue_table, people_summary_table, person_detail_table, project_people_table,
                     project_summary_table)

YAML_FILTER = 'YAML 파일 (*.yaml *.yml);;모든 파일 (*)'
KINDS = {'projects': '사업', 'people': '사람'}


def severity_colors():
    """검증 표의 행 색. 호출 때마다 현재 테마에 맞게 구한다."""
    return {'ERROR': theme.error_text(), 'WARNING': theme.warning_text()}
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
    """왼쪽 메뉴로 입력 화면(파일·표 편집)과 결과 화면(계산 결과·엑셀 저장)을 바꾼다.

    결과 화면으로 가면 입력 화면의 현재 내용을 바로 계산해 보여 준다."""

    INPUT_PAGE, RESULT_PAGE, PLAN_PAGE = 0, 1, 2
    PROJECTS_TAB, PEOPLE_TAB, OVERVIEW_TAB = 0, 1, 2  # input_tabs
    ISSUES_TAB, SUMMARY_TAB = 0, 1  # result_tabs

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('prouman', 'prouman')
        self.result = None
        self.issues = []
        self.generated_at = None  # 마지막 계산 시각. 결과 화면을 열 때마다 새로 계산한다.
        self.generated_from = ()  # 계산할 때의 사업·사람 파일 표시 이름
        self.people_docs = {}  # 연도 -> PeopleDoc. 같은 연도는 합쳐서 하나로, 다른 연도는 따로 둔다.
        self.plan_docs = {}  # 연도 -> PlanDoc. 확정 사람 입력과 독립적으로 보관한다.
        self.plan_dirty = {}
        self.plan_sources = {}
        self.people_dirty = {}  # 연도 -> 미내보내기 변경 여부
        self.people_sources = {}  # 연도 -> 불러온 파일들
        self.projects_sources = []  # 합친 사업 파일들
        self.projects_dirty = False
        self.loaded_files = []  # 다시 시작할 때 불러올 경로. 불러온 순서대로 둔다.
        self.current_year = None  # 고른 연도. 편집·계산 대상이다.
        self.backed_up = set()  # 이번 실행에서 .bak을 만든 파일. 첫 내보내기 직전의 원본만 백업한다.
        self.setWindowTitle('인력 배분')
        self.resize(1280, 800)

        # 입력 화면: 불러오기·내보내기·새로 만들기, 열린 문서 목록, 사업·사람 입력 탭
        import_button = QPushButton('불러오기')
        import_button.clicked.connect(self.browse_import)
        export_button = QPushButton('내보내기')
        export_button.clicked.connect(self.browse_export)
        new_button = QPushButton('새로 만들기')
        new_button.clicked.connect(self.new_all)
        self.projects_label = QLabel()
        files_row = QHBoxLayout()
        files_row.addWidget(import_button)
        files_row.addWidget(export_button)
        files_row.addWidget(new_button)
        files_row.addStretch()
        files_row.addWidget(self.projects_label)
        self.year_combo = QComboBox()
        self.year_combo.currentIndexChanged.connect(self.year_combo_changed)
        self.year_combo.activated.connect(lambda index: self.project_editor.show_selected_year())
        add_year_button = QPushButton('연도 추가')
        add_year_button.clicked.connect(self.add_year)
        close_year_button = QPushButton('연도 닫기')
        close_year_button.clicked.connect(lambda checked=False: self.close_year())
        year_row = QHBoxLayout()
        year_row.addWidget(QLabel('연도'))
        year_row.addWidget(self.year_combo)
        year_row.addWidget(add_year_button)
        year_row.addWidget(close_year_button)
        year_row.addStretch()
        self.project_editor = ProjectEditor()
        self.people_editor = PeopleEditor(lambda: self.project_editor.projects)
        self.planning_page = PlanningPage(lambda: self.project_editor.projects)
        self.planning_page.changed.connect(self.plan_changed)
        self.planning_page.save_requested.connect(self.browse_save_plan)
        self.planning_page.confirm_requested.connect(self.browse_confirm_plan)
        self.project_editor.managed_year = lambda: self.people_editor.doc.year
        self.people_editor.year_changed.connect(self.project_editor.refresh_grid)
        self.project_editor.allow_remove = self.allow_remove_project
        self.project_editor.changed.connect(self.projects_changed)
        self.project_editor.alias_renamed.connect(self.alias_renamed)
        self.project_editor.alias_removed.connect(self.remove_alias_everywhere)
        self.people_editor.changed.connect(self.people_changed)
        self.input_tabs = QTabWidget()
        self.input_tabs.addTab(self.project_editor, '사업 입력')
        self.input_tabs.addTab(self.people_editor, '사람 입력')
        self.input_tabs.addTab(self.people_editor.overview, '종합')
        self.people_editor.overview.person_requested.connect(self.open_person)
        input_page = QWidget()
        layout = QVBoxLayout(input_page)
        layout.addLayout(files_row)
        layout.addLayout(year_row)
        layout.addWidget(self.input_tabs)

        # 결과 화면: 머리 줄(계산 정보·엑셀 저장), 결과 탭
        self.result_header = QLabel()
        self.excel_button = QPushButton('엑셀 저장')
        self.excel_button.clicked.connect(self.browse_excel)
        self.excel_button.setEnabled(False)
        header = QHBoxLayout()
        header.addWidget(self.result_header, 1)
        header.addWidget(self.excel_button)

        self.issue_view = CopyTableView()
        self.people_summary_view, self.project_summary_view = CopyTableView(), CopyTableView()
        summary = QSplitter(Qt.Horizontal)
        for title, view in (('개인별 연간 요약', self.people_summary_view),
                            ('사업별 연간 요약', self.project_summary_view)):
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.addWidget(QLabel(title))
            box_layout.addWidget(view)
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
        self.project_list = QListWidget()
        self.project_list.currentTextChanged.connect(self.show_project_people)
        self.project_people_view = CopyTableView()
        self.project_detail_label = QLabel()
        participant_panel = QWidget()
        participant_layout = QVBoxLayout(participant_panel)
        participant_layout.setContentsMargins(0, 0, 0, 0)
        participant_layout.addWidget(self.project_detail_label)
        participant_layout.addWidget(self.project_people_view)
        project_details = QSplitter(Qt.Horizontal)
        project_details.addWidget(self.project_list)
        project_details.addWidget(participant_panel)
        project_details.setSizes([180, 1000])
        self.result_tabs = QTabWidget()
        for widget, title in ((self.issue_view, '검증'), (summary, '요약'), (people, '개인상세'),
                              (project_details, '사업상세')):
            self.result_tabs.addTab(widget, title)
        result_page = QWidget()
        layout = QVBoxLayout(result_page)
        layout.addLayout(header)
        layout.addWidget(self.result_tabs)

        # 왼쪽 메뉴와 화면 전환
        self.pages = QStackedWidget()
        self.pages.addWidget(input_page)
        self.pages.addWidget(result_page)
        self.pages.addWidget(self.planning_page)
        nav = QFrame()
        nav.setObjectName('nav')
        # 밝은 회색 고정은 다크 테마에서 따로 논다. 팔레트에서 뽑은 값으로 맞춘다.
        nav.setStyleSheet(theme.nav_stylesheet())
        self.nav = nav
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(4, 8, 4, 8)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = []
        for page, text in ((self.INPUT_PAGE, '✎  입력'), (self.RESULT_PAGE, '▦  결과'),
                           (self.PLAN_PAGE, '▤  계획')):
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda checked=False, page=page: self.show_page(page))
            self.nav_group.addButton(button, page)
            self.nav_buttons.append(button)
            nav_layout.addWidget(button)
        nav_layout.addStretch()
        nav.setMinimumWidth(110)
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu('파일')
        for text, shortcut, slot in (('새로 만들기', QKeySequence.New, self.new_all),
                                    ('불러오기', QKeySequence.Open, self.browse_import),
                                    ('내보내기', QKeySequence.Save, self.browse_export),
                                    ('종료', QKeySequence.Quit, self.close)):
            action = QAction(text, self)
            if shortcut is not None:
                action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, slot=slot: slot())
            file_menu.addAction(action)
        view_menu = self.menuBar().addMenu('보기')
        for page, text, key in ((self.INPUT_PAGE, '입력 화면', 'Ctrl+1'),
                                (self.RESULT_PAGE, '결과 화면', 'Ctrl+2'), (self.PLAN_PAGE, '계획 화면', 'Ctrl+3')):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda checked=False, page=page: self.show_page(page))
            view_menu.addAction(action)
        view_menu.addSeparator()
        for text, keys, step in (('글자 크게', [QKeySequence.ZoomIn, QKeySequence('Ctrl+=')], 1),
                                 ('글자 작게', [QKeySequence.ZoomOut], -1),
                                 ('기본 크기', [QKeySequence('Ctrl+0')], 0)):
            action = QAction(text, self)
            action.setShortcuts(keys)
            action.triggered.connect(lambda checked=False, step=step: self.change_font_size(step))
            view_menu.addAction(action)
        self.set_font_size(self.settings.value('font_size', DEFAULT_FONT_SIZE, type=int), save=False)
        self.show_page(self.INPUT_PAGE)
        self.show_result_header()

        self.restore_or_default()

    # --- 공통 대화상자 (테스트에서 바꿔 끼운다) ---

    def warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def ask_export(self, desc):
        """내보내지 않은 변경이 있을 때 'export' / 'discard' / 'cancel'을 돌려준다."""
        answer = QMessageBox.question(
            self, '내보내지 않은 변경', f'{desc}에 내보내지 않은 변경이 있습니다. 내보낼까요?',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return {QMessageBox.Save: 'export', QMessageBox.Discard: 'discard'}.get(answer, 'cancel')

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def ask_export_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '내보내기 폴더 선택', self.start_folder('export') or '.')
        return folder

    def ask_year(self):
        text, ok = QInputDialog.getText(self, '연도 추가', '연도 (4자리):')
        return text if ok else ''

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

    # --- 화면 전환 ---

    def open_person(self, row):
        """종합 탭에서 고른 사람의 입력 화면으로 간다."""
        self.input_tabs.setCurrentIndex(self.PEOPLE_TAB)
        self.people_editor.select_person(row)

    def show_page(self, page):
        """결과 화면으로 가면 입력 화면의 현재 내용을 바로 계산해 보여 준다."""
        if page == self.RESULT_PAGE:
            self.generate()
            return
        if page == self.PLAN_PAGE:
            self.planning_page.refresh_review()
        self.pages.setCurrentIndex(page)
        self.nav_buttons[page].setChecked(True)

    def show_result_header(self, extra=''):
        if self.generated_at is None:
            self.result_header.setText('결과 없음')
            return
        errors, warnings = issue_counts(self.issues)
        year = f'관리 연도 {self.result.inputs.year} · ' if self.result is not None else ''
        names = ' / '.join(label if label.startswith('(') else Path(label).name for label in self.generated_from)
        self.result_header.setText(f'{year}ERROR {errors}건 / WARNING {warnings}건 · '
                                   f'{self.generated_at:%H:%M:%S} 계산 · {names}{extra}')

    def apply_theme_styles(self):
        """시스템 테마가 바뀌면 메뉴·결과 표 색을 다시 칠한다."""
        self.nav.setStyleSheet(theme.nav_stylesheet())
        self.project_editor.apply_theme()
        self.people_editor.apply_theme()
        self.planning_page.refresh_review()
        fresh = severity_colors()
        for view in (self.issue_view, self.people_summary_view, self.project_summary_view,
                     self.person_view, self.project_people_view):
            model = view.model()
            if isinstance(model, ResultTableModel) and model.colors:
                model.colors = fresh
                model.layoutChanged.emit()
            view.viewport().update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ApplicationPaletteChange:
            self.apply_theme_styles()

    def set_projects_dirty(self, dirty):
        self.projects_dirty = dirty
        self.refresh_marks()

    def set_people_dirty(self, year, dirty):
        if year in self.people_dirty:
            self.people_dirty[year] = dirty
        self.refresh_marks()

    def people_changed(self):
        self.set_people_dirty(self.current_year, True)

    def plan_changed(self):
        if self.current_year in self.plan_dirty:
            self.plan_dirty[self.current_year] = True
        self.refresh_marks()

    def refresh_marks(self):
        """탭·메뉴·연도 목록·사업 줄에 미내보내기 표시(*)를 다시 그린다."""
        self.input_tabs.setTabText(self.PROJECTS_TAB, '사업 입력' + (' *' if self.projects_dirty else ''))
        any_people = any(self.people_dirty.values())
        any_plan = any(self.plan_dirty.values())
        self.input_tabs.setTabText(self.PEOPLE_TAB, '사람 입력' + (' *' if any_people else ''))
        self.nav_buttons[self.INPUT_PAGE].setText('✎  입력' + (' *' if self.projects_dirty or any_people else ''))
        self.nav_buttons[self.PLAN_PAGE].setText('▤  계획' + (' *' if any_plan else ''))
        self.refresh_year_combo()
        self.refresh_projects_label()

    def refresh_year_combo(self):
        combo = self.year_combo
        combo.blockSignals(True)
        combo.clear()
        for year in sorted(self.people_docs):
            combo.addItem(year + (' *' if self.people_dirty.get(year) or self.plan_dirty.get(year) else ''), year)
        if self.current_year in self.people_docs:
            combo.setCurrentIndex(combo.findData(self.current_year))
        combo.blockSignals(False)

    def refresh_projects_label(self):
        text = f'사업 {len(self.project_editor.projects)}개'
        if len(self.projects_sources) == 1:
            text += f' · {Path(self.projects_sources[0]).name}'
        elif self.projects_sources:
            text += f' · {len(self.projects_sources)}개 파일 합침'
        if self.projects_dirty:
            text += ' *'
        self.projects_label.setText(text)
        self.projects_label.setToolTip('\n'.join(self.projects_sources) if self.projects_sources else '불러온 사업 파일 없음')

    def projects_changed(self):
        self.set_projects_dirty(True)
        self.people_editor.refresh()
        self.planning_page.refresh_review()

    def alias_renamed(self, old, new):
        for year, doc in self.people_docs.items():
            if self.people_editor.count_alias(old, doc):
                self.people_editor.rename_alias(old, new, doc)
                self.set_people_dirty(year, True)
        for year, doc in self.plan_docs.items():
            changed = False
            for person in doc.people:
                if old in person.priorities:
                    person.priorities = {new if key == old else key: value
                                         for key, value in person.priorities.items()}
                    if person.draft_allocations is not None:
                        person.draft_allocations = {new if key == old else key: value
                                                    for key, value in person.draft_allocations.items()}
                    changed = True
            if changed:
                self.plan_dirty[year] = True
        self.planning_page.refresh()
        self.refresh_marks()

    def remove_alias_everywhere(self, alias):
        for year, doc in self.people_docs.items():
            if any(alias in person.allocations for person in doc.people):
                self.people_editor.remove_alias(alias, doc)
                self.set_people_dirty(year, True)
        for year, doc in self.plan_docs.items():
            changed = False
            for person in doc.people:
                if alias in person.priorities:
                    person.priorities.pop(alias)
                    if person.draft_allocations is not None:
                        person.draft_allocations.pop(alias, None)
                    changed = True
            if changed:
                self.plan_dirty[year] = True
        self.planning_page.refresh()
        self.refresh_marks()

    def allow_remove_project(self, project):
        count = sum(self.people_editor.count_alias(project.alias, doc) for doc in self.people_docs.values())
        plan_count = sum(project.alias in person.priorities for doc in self.plan_docs.values() for person in doc.people)
        name = project.name or project.alias or '(이름 없음)'
        text = f'사업 "{name}"을(를) 삭제할까요?'
        if count:
            text += f'\n이 사업에 입력한 참여율 {count}건도 함께 지워집니다.'
        if plan_count:
            text += f'\n계획에서 선택한 사업 {plan_count}건도 함께 지워집니다.'
        if not self.confirm('사업 삭제', text):
            return False
        if count:
            for year, doc in self.people_docs.items():
                if self.people_editor.count_alias(project.alias, doc):
                    self.set_people_dirty(year, True)
        return True

    def keep_all(self, what='입력'):
        """내보내지 않은 변경을 처리한다. 진행해도 되면 True."""
        if not (self.projects_dirty or any(self.people_dirty.values()) or any(self.plan_dirty.values())):
            return True
        answer = self.ask_export(what)
        if answer == 'discard':
            return True
        if answer != 'export':
            return False
        if (self.projects_dirty or any(self.people_dirty.values())) and not self.browse_export():
            return False
        return not any(self.plan_dirty.values()) or self.browse_save_plan()

    def keep_year(self, year):
        """해당 연도의 변경을 처리한다. 진행해도 되면 True."""
        if not (self.people_dirty.get(year) or self.plan_dirty.get(year)):
            return True
        answer = self.ask_export(f'{year}년 사람 입력')
        if answer == 'discard':
            return True
        if answer != 'export':
            return False
        if self.people_dirty.get(year) and not self.browse_export():
            return False
        return not self.plan_dirty.get(year) or self.browse_save_plan()

    def start_folder(self, which='import'):
        if which == 'export':
            candidates = [self.settings.value('export_folder', '', type=str),
                          self.settings.value('import_folder', '', type=str)]
        else:
            candidates = [self.settings.value('import_folder', '', type=str),
                          self.settings.value('export_folder', '', type=str)]
        if self.loaded_files:
            candidates.append(str(Path(self.loaded_files[-1]).parent))
        for folder in candidates:
            if folder and Path(folder).is_dir():
                return folder
        return ''

    def browse_import(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '불러오기', self.start_folder('import'), YAML_FILTER)
        if paths:
            self.settings.setValue('import_folder', str(Path(paths[0]).parent))
            self.run_import(paths)

    def run_import(self, paths):
        """불러온 뒤 실패·지원 외 항목을 모아 한 번씩만 알린다."""
        errors, dropped = self.import_files(paths)
        if errors:
            self.warn('불러오기', '다음 파일은 열지 못했습니다:\n' + '\n'.join(errors))
        if dropped:
            lines = [f'{name}: {entry}' for name, entries in dropped for entry in entries]
            self.warn('지원하지 않는 항목', '표 편집기에서 다루지 않는 다음 항목은 내보낼 때 빠집니다.\n'
                      + '\n'.join(lines[:20]) + ('\n...' if len(lines) > 20 else ''))

    def import_files(self, paths):
        """파일을 추가로 불러온다. 성공한 것만 반영하고 (실패 설명, 지원 외 항목)을 돌려준다."""
        errors, dropped, imported_people = [], [], []
        for path in paths:
            try:
                text = decode_text(Path(path).read_bytes())
                kind = detect_kind(text)
                if kind is None:
                    raise DocumentError('사업 파일도 사람 파일도 아닙니다.')
                if kind == 'projects':
                    document, file_dropped = read_projects(text)
                elif kind == 'plan':
                    document, file_dropped = read_plan(text), []
                else:
                    document, file_dropped = read_people(text)
            except (OSError, UnicodeError, DocumentError) as exc:
                errors.append(f'{Path(path).name}: {exc}')
                continue
            if file_dropped:
                dropped.append((Path(path).name, file_dropped))
            if kind == 'projects':
                dupes = sorted({p.alias.strip() for p in document}
                               & {p.alias.strip() for p in self.project_editor.projects})
                if dupes:
                    errors.append(f'{Path(path).name}: 이미 열린 사업 약칭과 겹칩니다: '
                                  + ', '.join(dupes[:5]) + (' 외' if len(dupes) > 5 else ''))
                    continue
                pristine = (not self.project_editor.projects and not self.projects_dirty
                           and not self.projects_sources)
                self.project_editor.projects.extend(document)
                self.projects_sources.append(str(path))
                self.project_editor.refresh()
                if self.project_editor.table.currentRow() < 0 and self.project_editor.projects:
                    self.project_editor.table.setCurrentCell(0, 0)
                self.people_editor.refresh()
                self.set_projects_dirty(not pristine)
            elif kind == 'plan':
                year = document.year.strip()
                if not valid_year(year):
                    errors.append(f'{Path(path).name}: 올바른 4자리 연도가 필요합니다.')
                    continue
                if year in self.plan_docs and (self.plan_docs[year].people or self.plan_sources.get(year)):
                    errors.append(f'{Path(path).name}: {year}년 계획이 이미 열려 있습니다.')
                    continue
                self.plan_docs[year] = document
                self.plan_dirty[year] = False
                self.plan_sources[year] = str(path)
                if year not in self.people_docs:
                    self.people_docs[year] = PeopleDoc(year, [])
                    self.people_dirty[year] = False
                    self.people_sources[year] = []
                self.select_year(year)
                imported_people.append(str(path))
            else:
                year = document.year.strip()
                if not year:
                    errors.append(f'{Path(path).name}: 연도가 비어 있습니다.')
                    continue
                document.year = year
                existing = self.people_docs.get(year)
                # 내용이 있는 연도에 덧붙일 때만 합치기로 보고, 비어 있는 문서에 넣으면 채우기로 본다.
                merge = (existing is not None
                         and (existing.people or self.people_dirty.get(year) or self.people_sources.get(year)))
                if merge:
                    dupes = sorted({p.name for p in document.people} & {p.name for p in existing.people})
                    if dupes:
                        errors.append(f'{Path(path).name}: 이미 열린 {year}년 사람 이름과 겹칩니다: '
                                      + ', '.join(dupes[:5]) + (' 외' if len(dupes) > 5 else ''))
                        continue
                    existing.people.extend(document.people)
                    self.people_sources[year].append(str(path))
                    self.set_people_dirty(year, True)
                    if year == self.current_year:
                        self.people_editor.refresh()
                else:
                    self.people_docs[year] = document
                    self.people_dirty[year] = False
                    self.people_sources[year] = [str(path)]
                    self.select_year(year)
                imported_people.append(str(path))
            self.loaded_files.append(str(path))
            self.settings.setValue('loaded_files', self.loaded_files)
        if imported_people and not self.project_editor.projects and not self.projects_dirty:
            # 사업 입력이 비어 있을 때만 사람 파일 옆의 사업 파일을 자동으로 가져온다.
            found = find_projects_file(imported_people[0])
            if found and found not in [str(p) for p in paths]:
                sub_errors, sub_dropped = self.import_files([found])
                errors.extend(sub_errors)
                dropped.extend(sub_dropped)
        if not errors:
            self.statusBar().showMessage('불러오기: ' + ', '.join(Path(p).name for p in paths))
        return errors, dropped

    def select_year(self, year):
        """고른 연도 문서를 편집기에 올린다. 사업 연도 표도 그 연도 기준으로 옮긴다."""
        self.current_year = year
        self.people_editor.blockSignals(True)
        self.people_editor.set_doc(self.people_docs[year])
        self.people_editor.blockSignals(False)
        if year not in self.plan_docs:
            self.plan_docs[year] = PlanDoc(year, [])
            self.plan_dirty[year] = False
            self.plan_sources[year] = ''
        self.planning_page.set_doc(self.plan_docs[year], self.plan_sources.get(year) or f'(새 {year} 계획)')
        self.project_editor.reset_years()
        self.refresh_marks()
        self.settings.setValue('current_year', year)

    def year_combo_changed(self, index):
        year = self.year_combo.itemData(index)
        if year is not None and year != self.current_year:
            self.select_year(year)

    def flush_year_edit(self):
        """연도 입력칸의 타이핑을 확정한다. 계산·내보내기를 진행해도 되면 True."""
        typed = self.people_editor.doc.year.strip()
        if typed == self.current_year:
            return True
        if not valid_year(typed):
            self.warn('연도 입력', '4자리 연도를 입력하세요 (예: 2026).')
            return False
        if typed in self.people_docs:
            self.warn('연도 입력', f'{typed}년 입력이 이미 열려 있습니다.')
            return False
        self.people_docs[typed] = self.people_docs.pop(self.current_year)
        self.people_dirty[typed] = self.people_dirty.pop(self.current_year)
        self.people_sources[typed] = self.people_sources.pop(self.current_year)
        if self.current_year in self.plan_docs:
            self.plan_docs[typed] = self.plan_docs.pop(self.current_year)
            self.plan_docs[typed].year = typed
            self.plan_dirty[typed] = self.plan_dirty.pop(self.current_year)
            self.plan_sources[typed] = self.plan_sources.pop(self.current_year)
        self.people_editor.doc.year = typed
        self.current_year = typed
        self.refresh_year_combo()
        return True

    def add_year(self):
        year = (self.ask_year() or '').strip()
        if not year:
            return
        if not valid_year(year):
            self.warn('연도 추가', f'4자리 연도를 입력하세요 (예: 2026). 입력: {year}')
            return
        if year in self.people_docs:
            self.select_year(year)
            self.statusBar().showMessage(f'이미 열린 연도입니다: {year}')
            return
        self.people_docs[year] = PeopleDoc(year, [])
        self.people_dirty[year] = False
        self.people_sources[year] = []
        self.plan_docs[year] = PlanDoc(year, [])
        self.plan_dirty[year] = False
        self.plan_sources[year] = ''
        self.select_year(year)
        self.statusBar().showMessage(f'연도 추가: {year}')

    def close_year(self, year=None):
        year = year or self.current_year
        if len(self.people_docs) <= 1:
            self.statusBar().showMessage('연도 문서는 최소 하나 있어야 합니다.')
            return
        if not self.keep_year(year):
            return
        years = sorted(self.people_docs)
        rest = [y for y in years if y != year]
        closed_sources = self.people_sources.pop(year, [])
        plan_source = self.plan_sources.pop(year, '')
        del self.people_docs[year]
        del self.people_dirty[year]
        self.plan_docs.pop(year, None)
        self.plan_dirty.pop(year, None)
        self.loaded_files = [p for p in self.loaded_files if p not in closed_sources]
        if plan_source:
            self.loaded_files = [p for p in self.loaded_files if p != plan_source]
        self.settings.setValue('loaded_files', self.loaded_files)
        self.select_year(rest[min(years.index(year), len(rest) - 1)])
        self.statusBar().showMessage(f'연도 닫기: {year}')

    def new_all(self):
        """미반영 변경을 처리한 뒤 사업·사람 전체를 비우고 올해 빈 연도로 시작한다."""
        if not self.keep_all():
            return
        self.project_editor.set_projects([])
        today = PeopleDoc().year
        self.people_docs = {today: PeopleDoc()}
        self.people_dirty = {today: False}
        self.people_sources = {today: []}
        self.plan_docs = {today: PlanDoc(today, [])}
        self.plan_dirty = {today: False}
        self.plan_sources = {today: ''}
        self.projects_sources = []
        self.loaded_files = []
        self.settings.setValue('loaded_files', [])
        self.set_projects_dirty(False)
        self.current_year = None
        self.select_year(today)
        self.show_page(self.INPUT_PAGE)
        self.statusBar().showMessage('새로 만들기: 빈 입력으로 시작합니다.')

    def browse_export(self):
        folder = self.ask_export_folder()
        if not folder:
            return False
        return self.export_all(folder)

    def browse_save_plan(self):
        folder = self.ask_export_folder()
        return bool(folder) and self.save_plan(folder)

    def _write_backup_file(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        key = str(path.resolve())
        if path.exists() and key not in self.backed_up:
            shutil.copy2(path, path.with_name(path.name + '.bak'))
            self.backed_up.add(key)
        path.write_text(text, encoding='utf-8')

    def save_plan(self, folder):
        """오류가 남은 수동 초안도 별도 계획 파일에 보존한다."""
        if not self.flush_year_edit():
            return False
        target = Path(folder)
        plan_path = target / 'plans' / f'plan_{self.current_year}.yaml'
        try:
            self._write_backup_file(target / 'projects.yaml', dump_projects(self.project_editor.projects))
            self._write_backup_file(plan_path, dump_plan(self.plan_docs[self.current_year]))
        except OSError as exc:
            self.warn('계획 저장 실패', f'{plan_path}\n{exc.strerror or exc}')
            return False
        self.plan_sources[self.current_year] = str(plan_path)
        self.planning_page.plan_path = str(plan_path)
        self.plan_dirty[self.current_year] = False
        self.set_projects_dirty(False)
        if str(plan_path) not in self.loaded_files:
            self.loaded_files.append(str(plan_path))
            self.settings.setValue('loaded_files', self.loaded_files)
        self.settings.setValue('export_folder', str(folder))
        self.refresh_marks()
        self.statusBar().showMessage(f'계획 저장: {plan_path}')
        return True

    def browse_confirm_plan(self):
        folder = self.ask_export_folder()
        return bool(folder) and self.confirm_plan(folder)

    def confirm_plan(self, folder):
        """현재 초안을 월별 엔진으로 검증한 뒤 확정 파일을 따로 만든다."""
        if not self.flush_year_edit():
            return False
        plan, review = self.planning_page.refresh_review()
        if plan is None or review is None or review.has_errors:
            self.warn('확정 불가', '계획 화면의 ERROR를 먼저 해결하세요.')
            return False
        doc = self.planning_page.confirmed_doc()
        from .loading import load_inputs
        inputs, issues = load_inputs('(현재 사업 입력)', '(현재 확정 입력)',
                                      {'projects': dump_projects(self.project_editor.projects),
                                       'people': dump_people(doc)})
        result = calculate(inputs, issues) if inputs is not None else None
        if result is None or result.has_errors:
            self.warn('확정 불가', '확정 사람 파일의 월별 검증을 통과하지 못했습니다.')
            return False
        if not self.save_plan(folder):
            return False
        path = Path(folder) / 'people' / f'people_{self.current_year}.yaml'
        try:
            self._write_backup_file(path, dump_people(doc))
        except OSError as exc:
            self.warn('확정 파일 저장 실패', f'{path}\n{exc.strerror or exc}')
            return False
        self.people_docs[self.current_year] = doc
        self.people_sources[self.current_year] = [str(path)]
        self.people_dirty[self.current_year] = False
        self.people_editor.set_doc(doc)
        if str(path) not in self.loaded_files:
            self.loaded_files.append(str(path))
            self.settings.setValue('loaded_files', self.loaded_files)
        self.refresh_marks()
        self.statusBar().showMessage(f'확정 파일 생성: {path}')
        return True

    def export_all(self, folder):
        """폴더에 projects.yaml과 연도별 사람 파일을 쓴다. 전부 성공하면 True."""
        if not self.flush_year_edit():
            return False
        target = Path(folder)
        jobs = [(target / 'projects.yaml', dump_projects(self.project_editor.projects))]
        for year in sorted(self.people_docs):
            jobs.append((target / 'people' / f'people_{year}.yaml', dump_people(self.people_docs[year])))
        try:
            (target / 'people').mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.warn('내보내기 실패', f'{target / "people"}\n{exc.strerror or exc}')
            return False
        errors = []
        for path, text in jobs:
            try:
                key = str(path.resolve())
                if path.exists() and key not in self.backed_up:
                    shutil.copy2(path, path.with_name(path.name + '.bak'))
                    self.backed_up.add(key)
                path.write_text(text, encoding='utf-8')
            except OSError as exc:
                errors.append(f'{path.name}: {exc.strerror or exc}')
        if errors:
            self.warn('내보내기 실패', '다음 파일은 쓰지 못했습니다:\n' + '\n'.join(errors))
            return False
        self.set_projects_dirty(False)
        for year in self.people_dirty:
            self.set_people_dirty(year, False)
        self.settings.setValue('export_folder', str(folder))
        self.statusBar().showMessage(f'내보내기: {folder} (사업 1개 · 사람 {len(jobs) - 1}개 연도)')
        return True

    def restore_or_default(self):
        """지난번에 불러온 파일들을 다시 연다. 열린 연도가 없으면 올해 빈 연도로 시작한다."""
        stored = self.settings.value('loaded_files', None)
        if stored is None:
            stored = []
            for kind in KINDS:
                old = self.settings.value(f'{kind}_file', '', type=str)
                if old and Path(old).is_file():
                    stored.append(old)
            self.settings.remove('projects_file')
            self.settings.remove('people_file')
        if stored:
            # 없어진 파일은 조용히 건너뛴다.
            self.run_import([p for p in stored if Path(p).is_file()])
        if not self.people_docs:
            today = PeopleDoc().year
            self.people_docs[today] = PeopleDoc()
            self.people_dirty[today] = False
            self.people_sources[today] = []
        wanted = self.settings.value('current_year', '', type=str)
        self.select_year(wanted if wanted in self.people_docs else sorted(self.people_docs)[0])

    def source_labels(self):
        """계산에 쓴 파일 표시 이름. 합친 파일은 쉼표로 잇는다."""
        projects = ', '.join(sorted(Path(p).name for p in self.projects_sources)) or '(새 사업 입력)'
        sources = self.people_sources.get(self.current_year, [])
        people = ', '.join(sorted(Path(p).name for p in sources)) or f'(새 {self.current_year} 입력)'
        return projects, people

    def closeEvent(self, event):
        if self.keep_all():
            event.accept()
        else:
            event.ignore()

    # --- 계산 결과 ---

    def clear_results(self):
        for view in (self.people_summary_view, self.project_summary_view, self.person_view, self.project_people_view):
            view.show_table([], [])
        self.project_detail_label.clear()
        self.person_list.clear()
        self.project_list.clear()

    def generate(self):
        """입력 화면의 현재 내용(내보내기 전이어도)을 파일과 같은 규칙으로 검증·계산해 결과 화면에 보여 준다."""
        if not self.flush_year_edit():
            self.show_page(self.INPUT_PAGE)
            return
        labels = self.source_labels()
        self.result, self.issues, self.generated_at = None, [], None
        self.excel_button.setEnabled(False)
        self.clear_results()
        self.issue_view.show_table([], [])
        self.pages.setCurrentIndex(self.RESULT_PAGE)
        self.nav_buttons[self.RESULT_PAGE].setChecked(True)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            inputs, issues = load_inputs(
                *labels, texts={'projects': dump_projects(self.project_editor.projects),
                                'people': dump_people(self.people_editor.doc)})
            result = calculate(inputs, issues) if inputs is not None else None
        except Exception as exc:  # 예상하지 못한 실패도 창을 닫지 않고 알린다.
            self.show_result_header()
            self.statusBar().showMessage('계산 실패')
            self.warn('계산 실패', f'{type(exc).__name__}: {exc}')
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.issues = result.issues if result is not None else issues
        self.generated_at, self.generated_from = datetime.now(), labels
        self.issue_view.show_table(*issue_table(self.issues), colors=severity_colors())
        if result is None or result.has_errors:
            self.show_result_header(' · 오류로 결과를 만들지 않았습니다. 입력 화면에서 고친 뒤 결과 화면으로 돌아오세요.')
            self.result_tabs.setCurrentIndex(self.ISSUES_TAB)
            return

        self.result = result
        self.show_result_header()
        self.people_summary_view.show_table(*people_summary_table(result))
        self.project_summary_view.show_table(*project_summary_table(result))
        self.project_list.addItems(list(result.inputs.projects))
        self.project_list.setCurrentRow(0)
        self.person_list.addItems(list(result.inputs.people))
        self.person_list.setCurrentRow(0)
        self.excel_button.setEnabled(True)
        self.result_tabs.setCurrentIndex(self.SUMMARY_TAB)

    def show_project_people(self, alias):
        if self.result is not None and alias in self.result.inputs.projects:
            item, limit, missing, totals = project_summary(self.result, alias)
            total = sum(totals)
            self.project_detail_label.setText(
                f'{alias} ({item.name}) · 배정 {amount(limit)} / 할당 {amount(total)} / 잔여 {amount(limit - total)}'
                + (' (배정 미등록)' if missing else ''))
            self.project_people_view.show_table(*project_people_table(self.result, alias), bold_label='합계')
        else:
            self.project_detail_label.clear()
            self.project_people_view.show_table([], [])

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
        self.statusBar().showMessage(f'엑셀 저장: {path}')
        return True


WAYLAND_NOISE = (
    # Wayland는 팝업이 아닌 창의 마우스 grab을 지원하지 않는다. 열린 메뉴 제목을 다시 누르는 등
    # Qt 내부 동작에도 경고를 찍지만 동작은 정상이므로, 정확히 이 줄만 버리고 나머지는 그대로 둔다.
    'This plugin supports grabbing the mouse only for popup windows',
)
_previous_message_handler = None
_message_handler_installed = False


def _qt_message_handler(mode, context, message):
    if message in WAYLAND_NOISE:
        return
    if _previous_message_handler is not None:
        _previous_message_handler(mode, context, message)
    else:
        # 따로 건 핸들러가 없을 때가 Qt 기본 출력이다. PySide6는 기본 핸들러를
        # 노출하지 않으므로 stderr에 직접 쓴다. --windowed 실행파일에는 2번
        # 디스크립터가 없을 수 있으므로 이 경우에는 메시지만 버린다.
        try:
            os.write(2, f'{message}\n'.encode('utf-8', 'replace'))
        except OSError:
            pass


def install_qt_noise_filter():
    """알려진 Wayland 무해 경고를 버린다. 두 번 불러도 한 번만 건다."""
    global _previous_message_handler, _message_handler_installed
    if not _message_handler_installed:
        _previous_message_handler = qInstallMessageHandler(_qt_message_handler)
        _message_handler_installed = True


def main():
    install_qt_noise_filter()
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
