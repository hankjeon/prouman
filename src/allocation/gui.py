"""PySide6 데스크톱 화면. 사업·사람 입력을 표로 편집하고, 계산 결과를 그리드로 보여 준다."""
import os
import json
import sys
import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import yaml

from PySide6.QtCore import (QAbstractTableModel, QEvent, QModelIndex, QSettings, QSize, Qt, Signal, QTimer,
                            qInstallMessageHandler)
from PySide6.QtGui import QAction, QFont, QGuiApplication, QKeySequence, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton,
                               QLineEdit, QPlainTextEdit, QTextEdit,
                               QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTableView, QTabWidget, QToolTip, QVBoxLayout, QWidget)

from . import theme
from .autosave import TransactionalSession
from .global_history import GlobalHistory
from .document import (DocumentError, PeopleDoc, PlanDoc, ProjectList, detect_kind, dump_people, dump_plan, dump_projects,
                       plan_from_people, read_people, read_plan, read_projects, valid_year)
from .editor import PeopleEditor, PinListDelegate, ProjectEditor, SelectionDelegate
from .engine import calculate
from .loading import decode_text, load_inputs
from .models import amount
from .planning_gui import PlanningPage
from .storage import write_bundle
from .text_encoding import ENCODINGS, decode_yaml, encode_yaml
from .encoding_ui import choose_files, choose_folder
from .yaml_editor import YamlEditorPanel, extract_error_line
from .rendering import issue_counts, project_summary
from .tables import (issue_table, people_summary_table, person_detail_table, project_people_table,
                     project_summary_table)
from .table_order import TableOrderMixin, selection_text

YAML_FILTER = 'YAML 파일 (*.yaml *.yml);;모든 파일 (*)'
KINDS = {'projects': '사업', 'people': '사람'}


class AutosaveEncodingError(ValueError):
    def __init__(self, path, encoding):
        self.path, self.encoding = path, encoding
        super().__init__(f'{path}: {ENCODINGS[encoding]}로 표현할 수 없는 문자가 있습니다.')


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


class PinTableDelegate(SelectionDelegate):
    """결과 개인상세 0번 열 사업명 왼쪽에 압정 고정 아이콘을 그린다."""

    def paint(self, painter, option, index):
        view = self.parent()
        provider = getattr(view, '_pin_provider', None)
        state = provider(index.row())[0] if provider is not None and index.column() == 0 else 'none'
        if state == 'none':
            super().paint(painter, option, index)
            return
        opt_state = option.state
        plain = QStyleOptionViewItem(option)
        plain.state = opt_state & ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        self.selected = bool(opt_state & QStyle.StateFlag.State_Selected)
        if self.selected:
            painter.fillRect(option.rect, theme.selection_color())
            plain.palette.setColor(QPalette.Text, theme.selected_text_color())
        text_option = QStyleOptionViewItem(plain)
        text_option.rect = option.rect.adjusted(theme.pin_text_offset(option.rect), 0, 0, 0)
        QStyledItemDelegate.paint(self, painter, text_option, index)
        self.selected = False
        theme.draw_pin_icon(painter, theme.pin_icon_rect(option.rect), state,
                            selected=bool(opt_state & QStyle.StateFlag.State_Selected), state_colors=True)
        if opt_state & QStyle.StateFlag.State_HasFocus:
            painter.save()
            painter.setPen(theme.current_cell_color())
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        view = self.parent()
        provider = getattr(view, '_pin_provider', None)
        if provider is not None and index.column() == 0 and provider(index.row())[0] != 'none':
            height = max(size.height(), option.fontMetrics.height() + 8)
            return QSize(size.width() + theme.pin_icon_size(height) + 14, height)
        return size


class PinListWidget(QListWidget):
    """사람 이름 앞에 고정/부분고정/미고정 압정 버튼을 표시하고 토글하는 목록."""

    pin_toggled = Signal(str)

    def __init__(self):
        super().__init__()
        self._pin_provider = None
        self._pin_press_row = None
        self.setMouseTracking(True)
        self.setItemDelegate(PinListDelegate(self))

    def set_pin_provider(self, provider):
        self._pin_provider = provider
        self.viewport().update()

    def pin_info(self, name):
        return self._pin_provider(name) if self._pin_provider is not None else ('none', '')

    def pin_state(self, target):
        name = target.data(Qt.DisplayRole) or '' if hasattr(target, 'data') else target
        return self.pin_info(name)[0]

    def pin_hit(self, item, position):
        if item is None or self._pin_provider is None:
            return False
        state, _ = self.pin_info(item.text())
        return state != 'none' and theme.pin_hit_rect(self.visualItemRect(item)).contains(position)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self.doItemsLayout()

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip and self._pin_provider is not None:
            item = self.itemAt(event.pos())
            if item is not None:
                _, tip = self.pin_info(item.text())
                if tip:
                    QToolTip.showText(event.globalPos(), tip, self)
                    return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        self._pin_press_row = None
        position = event.position().toPoint()
        item = self.itemAt(position)
        if (event.button() == Qt.LeftButton and event.modifiers() == Qt.NoModifier
                and self.pin_hit(item, position)):
            self._pin_press_row = self.row(item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        position = event.position().toPoint()
        if self._pin_press_row is not None and event.buttons() & Qt.LeftButton:
            event.accept()
            return
        if not event.buttons():
            item = self.itemAt(position)
            if self.pin_hit(item, position):
                self.viewport().setCursor(Qt.PointingHandCursor)
            else:
                self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        pin_row = self._pin_press_row
        self._pin_press_row = None
        if pin_row is not None and event.button() == Qt.LeftButton:
            position = event.position().toPoint()
            item = self.itemAt(position)
            event.accept()
            if item is not None and self.row(item) == pin_row and self.pin_hit(item, position):
                self.pin_toggled.emit(item.text())
            return
        super().mouseReleaseEvent(event)


class CopyTableView(TableOrderMixin, QTableView):
    """선택한 셀 범위를 탭으로 구분한 텍스트로 복사한다. 선택하지 않은 칸은 비워 둔다."""

    def __init__(self):
        super().__init__()
        self._pin_provider = None
        self._on_pin_click = None
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.fit_rows()
        self.setItemDelegate(SelectionDelegate(self))
        self.setModel(ResultTableModel())
        self.init_table_order()

    def set_pin_handler(self, provider, on_click):
        self._pin_provider = provider
        self._on_pin_click = on_click
        self.setItemDelegate(PinTableDelegate(self) if provider is not None else SelectionDelegate(self))
        self.setMouseTracking(True)

    def pin_hit(self, index, position):
        if self._pin_provider is None or index.column() != 0:
            return False
        state, _ = self._pin_provider(index.row())
        return state != 'none' and theme.pin_hit_rect(self.visualRect(index)).contains(position)

    def pin_clicked(self, row):
        if self._on_pin_click is not None:
            self._on_pin_click(row)

    def fit_rows(self):
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)

    def resizeColumnsToContents(self):
        super().resizeColumnsToContents()
        if self._pin_provider is not None and self.model() is not None and self.model().columnCount() > 0:
            # 0번 열이 매우 짧은 약칭이어도 압정 영역과 중앙 드래그 영역이 겹치지 않게 최소 너비를 둔다.
            min_width = (theme.pin_icon_size(self.verticalHeader().defaultSectionSize()) + 8) * 2 + 16
            if self.columnWidth(0) < min_width:
                self.setColumnWidth(0, min_width)

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip and self._pin_provider is not None:
            index = self.indexAt(event.pos())
            if index.isValid() and self.pin_hit(index, event.pos()):
                _, tip = self._pin_provider(index.row())
                if tip:
                    QToolTip.showText(event.globalPos(), tip, self)
                    return True
        return super().viewportEvent(event)

    def changeEvent(self, event):
        # 글자 크기가 바뀌면 행 높이와 열 너비를 새 크기에 맞춘다.
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self.fit_rows()
            self.resizeColumnsToContents()

    def show_table(self, headers, rows, order_context=None, footer_rows=0, **options):
        self._ordering_update = True
        self.setModel(ResultTableModel(headers, rows, **options))
        row_keys = {row: self.model().rows[row][0]
                    for row in range(max(0, self.model().rowCount() - footer_rows))} if order_context else {}
        self.configure_order(order_context, rows=row_keys)
        self.resizeColumnsToContents()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def copy_selection(self):
        text = selection_text(self, self.model().text)
        if self.selectedIndexes():
            QGuiApplication.clipboard().setText(text)
        return text


def find_projects_file(people_path):
    # 사람 파일 폴더와 그 상위 폴더 순서로 찾는다. 권장 구조는 작업폴더/people/people_연도.yaml이다.
    folder = Path(people_path).resolve().parent
    for candidate in (folder / 'projects.yaml', folder.parent / 'projects.yaml'):
        if candidate.is_file():
            return str(candidate)
    return None


class WorkspaceWindow(QMainWindow):
    """서쪽 탭으로 입력 화면(표 편집)과 결과 화면(계산 결과·엑셀 저장)을 바꾼다.

    결과 화면으로 가면 입력 화면의 현재 내용을 바로 계산해 보여 준다."""

    INPUT_PAGE, RESULT_PAGE, YAML_PAGE = 0, 1, 2
    PROJECTS_TAB, PEOPLE_TAB, OVERVIEW_TAB = 0, 1, 2  # input_tabs
    ISSUES_TAB, SUMMARY_TAB = 0, 1  # result_tabs
    history_committed = Signal(object, str, object, object)
    history_invalidated = Signal()

    def __init__(self, settings=None, planning=False):
        super().__init__()
        self.planning = planning
        self.settings = settings if settings is not None else QSettings('prouman', 'prouman')
        self.result = None
        self._shown_person = None
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
        self.document_encodings = {}  # 문서 종류:연도 -> 병합한 원본 인코딩 집합
        self.file_encodings = {}  # 절대 경로 -> (인코딩, 판별 안내)
        self.file_fingerprints = {}  # 읽거나 성공적으로 쓴 원본의 내용 지문
        self._source_metadata = {}  # 원본 경로별 파일 공통 metadata
        self._autosave = None
        self._autosave_pending = False
        self._autosave_restoring = False
        self._autosave_blocked = False
        self._autosave_selection = None
        self._autosave_touched_projects = False
        self._autosave_touched_years = set()
        self._binding_deferred_projects = False
        self._binding_deferred_years = set()
        self._history_action = None
        self._history_before = None
        self._copy_backed_up = set()
        self._import_depth = 0
        self._unsupported_sources = set()
        self._last_page = self.INPUT_PAGE
        try:
            choices = json.loads(self.settings.value('read_encodings', '{}'))
            self.read_encodings = {key: value for key, value in choices.items()
                                   if isinstance(key, str) and value in ENCODINGS}
        except (ValueError, TypeError, AttributeError):
            self.read_encodings = {}
        self._save_encoding = 'preserve'
        self.setWindowTitle('인력 배분')
        self.resize(1280, 800)

        # 입력 화면: 열린 문서 목록, 사업·사람 입력 탭 (파일 명령은 메뉴가 맡는다)
        self.projects_label = QLabel()
        self.encoding_label = QLabel()
        self.save_status_label = QLabel('저장 위치 필요')
        self.statusBar().addPermanentWidget(self.save_status_label)
        self.statusBar().addPermanentWidget(self.encoding_label)
        files_row = QHBoxLayout()
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
        self.planning_page.before_change.connect(self._capture_autosave_selection)
        self.planning_page.save_requested.connect(self.browse_work_copy)
        self.planning_page.confirm_requested.connect(self.browse_confirm_plan)
        self.project_editor.managed_year = lambda: self.people_editor.doc.year
        self.people_editor.year_changed.connect(self.project_editor.refresh_grid)
        self.project_editor.allow_remove = self.allow_remove_project
        self.project_editor.changed.connect(self.projects_changed)
        self.project_editor.before_change.connect(self._capture_autosave_selection)
        self.project_editor.alias_renamed.connect(self.alias_renamed)
        self.project_editor.alias_removed.connect(self.remove_alias_everywhere)
        self.project_editor.open_yaml_requested.connect(lambda name: self.open_yaml_for('projects', name))
        self.people_editor.changed.connect(self.people_changed)
        self.people_editor.before_change.connect(self._capture_autosave_selection)
        self.people_editor.open_yaml_requested.connect(
            lambda name: self.open_yaml_for('people:' + str(self.current_year), name))
        self.planning_page.open_yaml_requested.connect(
            lambda name: self.open_yaml_for('plan:' + str(self.current_year), name))
        self.people_editor.overview.projects_order_requested.connect(self.project_editor.reorder_projects)
        self.input_tabs = QTabWidget()
        self.input_tabs.addTab(self.project_editor, '사업 입력')
        if planning:
            self.input_tabs.addTab(self.planning_page.input_widget, '사람 입력')
        else:
            self.input_tabs.addTab(self.people_editor, '사람 입력')
            self.input_tabs.addTab(self.people_editor.overview, '종합')
        self.people_editor.overview.person_requested.connect(self.open_person)
        input_page = QWidget()
        layout = QVBoxLayout(input_page)
        layout.addLayout(files_row)
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
        self.person_list = PinListWidget()
        self.person_list.set_pin_provider(self.person_pin_info)
        self.person_list.pin_toggled.connect(self.toggle_person_pin)
        self.person_list.currentTextChanged.connect(self.show_person)
        self.person_view = CopyTableView()
        self.person_view.set_pin_handler(self._person_view_pin_info, self._person_view_pin_clicked)
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

        # 각 업무는 입력·결과·YAML 편집 탭을 사용한다. 작업 모드 전환은 바깥 창이 맡는다.
        # 탭 전환은 좌측 세로(서쪽) 탭으로 둔다.
        self.yaml_panel = YamlEditorPanel(self)
        self.yaml_panel.dirty_changed.connect(lambda _dirty: self.refresh_marks())
        self.yaml_panel.zoom_requested.connect(self.change_font_size)
        self.pages = QTabWidget()
        self.pages.setTabPosition(QTabWidget.West)
        self.pages.addTab(input_page, '입력')
        self.pages.addTab(self.planning_page if planning else result_page, '결과')
        self.pages.addTab(self.yaml_panel, 'YAML 편집')
        if planning:
            result_page.setParent(self)
            result_page.hide()
            self.people_editor.setParent(self)
            self.people_editor.hide()
        else:
            self.planning_page.setParent(self)
            self.planning_page.hide()
            self.planning_page.input_widget.setParent(self)
            self.planning_page.input_widget.hide()
        self.pages.currentChanged.connect(self.show_page)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(year_row)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu('파일')
        for text, shortcut, slot in (('새로 만들기', QKeySequence.New, self.browse_new),
                                    ('불러오기', QKeySequence.Open, self.browse_import),
                                    ('저장 위치 지정', None, self.browse_bind_sources),
                                    ('내보내기', None, self.browse_export),
                                    ('종료', QKeySequence.Quit, self.close)):
            action = QAction(text, self)
            if shortcut is not None:
                action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, slot=slot: slot())
            file_menu.addAction(action)
        view_menu = self.menuBar().addMenu('보기')
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
        self.apply_theme_styles()
        QApplication.instance().paletteChanged.connect(self.apply_theme_styles)

    # --- 공통 대화상자 (테스트에서 바꿔 끼운다) ---

    def warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def ask_export(self, desc):
        """내보내지 않은 변경이 있을 때 'export' / 'discard' / 'cancel'을 돌려준다."""
        answer = QMessageBox.question(
            self, '저장하지 않은 변경', f'{desc}에 저장하지 않은 변경이 있습니다. 저장할까요?',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return {QMessageBox.Save: 'export', QMessageBox.Discard: 'discard'}.get(answer, 'cancel')

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def ask_export_folder(self):
        folder, self._save_encoding = choose_folder(self, self.start_folder('export') or '.')
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
        if hasattr(self, 'yaml_panel'):
            self.yaml_panel.set_font_size(self.font_size)
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

    def confirm_leave_yaml(self):
        """YAML 편집기에 미저장 변경이 있으면 저장·버리기·취소를 묻는다."""
        if not hasattr(self, 'yaml_panel') or not self.yaml_panel.has_dirty():
            return True
        return self.yaml_panel.confirm_leave_all()

    def open_yaml_for(self, key, target_name=''):
        """표 편집기에서 선택한 사업/사람 위치로 YAML 편집 탭을 연다."""
        self.show_page(self.YAML_PAGE)
        if self.pages.currentIndex() == self.YAML_PAGE:
            self.yaml_panel.jump_to_item(key, target_name)

    def show_page(self, page):
        """입력·결과·YAML 편집 화면을 전환한다."""
        if getattr(self, '_last_page', self.INPUT_PAGE) == self.YAML_PAGE and page != self.YAML_PAGE:
            if not self.confirm_leave_yaml():
                self.pages.blockSignals(True)
                self.pages.setCurrentIndex(self.YAML_PAGE)
                self.pages.blockSignals(False)
                return
        if page in (self.RESULT_PAGE, self.YAML_PAGE):
            focus = QApplication.focusWidget()
            if focus is not None and self.isAncestorOf(focus):
                focus.clearFocus()
            if not self.flush_autosave():
                if page == self.YAML_PAGE:
                    self.pages.blockSignals(True)
                    self.pages.setCurrentIndex(self._last_page)
                    self.pages.blockSignals(False)
                return
        if page == self.RESULT_PAGE and not self.planning:
            self.generate()
            self._last_page = self.pages.currentIndex()
            return
        if self.planning and page == self.RESULT_PAGE:
            self.planning_page.refresh_review()
        if page == self.YAML_PAGE:
            self.yaml_panel.sync_from_workspace(preserve_dirty=True)
        self.pages.blockSignals(True)
        self.pages.setCurrentIndex(page)
        self.pages.blockSignals(False)
        self._last_page = page

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
        if getattr(self, '_theme_applying', False):
            return
        self._theme_applying = True
        try:
            theme.apply_window_theme(self)
            self._refresh_theme_widgets()
        finally:
            self._theme_applying = False

    def _refresh_theme_widgets(self):
        self.project_editor.apply_theme()
        self.people_editor.apply_theme()
        self.planning_page.refresh_review()
        self.yaml_panel.apply_theme()
        fresh = severity_colors()
        for view in (self.issue_view, self.people_summary_view, self.project_summary_view,
                     self.person_view, self.project_people_view):
            model = view.model()
            if isinstance(model, ResultTableModel) and model.colors:
                model.colors = fresh
                model.layoutChanged.emit()
            view.viewport().update()
        self.person_list.viewport().update()

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

    def set_save_status(self, state, detail=''):
        self.save_status_label.setText(state)
        paths = self.projects_sources + [p for sources in self.people_sources.values() for p in sources]
        paths += [p for p in self.plan_sources.values() if p]
        self.save_status_label.setToolTip('\n'.join(paths) + (('\n' + detail) if detail else ''))

    def update_save_status(self, *, reset=False):
        if (not reset and self.save_status_label.text() in
                ('저장 실패', '외부 변경 충돌', '복구 필요')):
            return
        dirty = self.plan_dirty if self.planning else self.people_dirty
        sources = self.plan_sources if self.planning else self.people_sources
        missing = self.projects_dirty and len(self.projects_sources) != 1
        missing |= any(value and (not sources.get(y) or (not self.planning and len(sources[y]) != 1))
                       for y, value in dirty.items())
        if self.current_year in self.people_docs:
            current = sources.get(self.current_year)
            missing |= not current or (not self.planning and len(current) != 1)
        if self._autosave_blocked:
            self.set_save_status('복구 필요')
        elif missing or not self.loaded_files:
            self.set_save_status('저장 위치 필요')
        else:
            self.set_save_status('저장됨')

    def save_failure_status(self, exc):
        message = str(exc)
        state = ('복구 필요' if '원본 복구 실패' in message else
                 '외부 변경 충돌' if '외부 변경' in message or '외부에서 변경' in message else '저장 실패')
        self.set_save_status(state, message)

    def _business_snapshot(self):
        return (self.project_editor.projects, self.people_docs, self.plan_docs)

    @property
    def autosave_session(self):
        return self._autosave

    def history_ready(self):
        if self._autosave is None or self._autosave_blocked:
            return False
        dirty = self.plan_dirty if self.planning else self.people_dirty
        return (not (self._autosave_paths & self._unsupported_sources)
                and not (self._autosave_project is not None and self.projects_dirty)
                and not any(dirty.get(year) for year in self._autosave_people))

    def block_history_writes(self):
        self._autosave_blocked = True

    def restore_history_state(self, context=None):
        """성공한 이력 이동을 화면과 저장 지문에 반영한다."""
        self._autosave_selection = None
        self._restore_autosave(self._autosave.state, True, set(self._autosave_people))
        for path, fingerprint in self._autosave.fingerprints.items():
            self.file_fingerprints[str(path)] = fingerprint
        self.backed_up.update(self._autosave.backed_up)
        self.update_save_status(reset=True)
        if context is None:
            return
        year, page, tab, alias, name, result_tab = context
        self._autosave_restoring = True
        try:
            if year in self.people_docs:
                self.select_year(year)
            if 0 <= tab < self.input_tabs.count():
                self.input_tabs.setCurrentIndex(tab)
            row = next((i for i, p in enumerate(self.project_editor.projects) if p.alias == alias), -1)
            if row >= 0:
                self.project_editor.table.setCurrentCell(row, 0)
            doc = self.plan_docs[self.current_year] if self.planning else self.people_docs[self.current_year]
            row = next((i for i, person in enumerate(doc.people) if person.name == name), -1)
            people_list = self.planning_page.people_list if self.planning else self.people_editor.people_list
            if row >= 0:
                people_list.setCurrentRow(row)
            self.show_page(page)
            if not self.planning and page == self.RESULT_PAGE:
                self.result_tabs.setCurrentIndex(result_tab)
                matches = self.person_list.findItems(name or '', Qt.MatchExactly)
                if matches:
                    self.person_list.setCurrentItem(matches[0])
        finally:
            self._autosave_restoring = False
        if hasattr(self, 'yaml_panel'):
            self.yaml_panel.sync_from_workspace(preserve_dirty=False)

    def apply_and_save_yaml(self, key, text):
        """YAML 편집기 텍스트를 파싱·검증한 뒤 문서에 반영하고 원본에 저장한다."""
        if any('\t' in line for line in text.splitlines()):
            line_no = next(i for i, line in enumerate(text.splitlines(), 1) if '\t' in line)
            self.warn('YAML 저장 실패', f'{line_no}번째 줄: YAML에서는 탭 문자(\\t) 대신 공백을 사용해야 합니다.')
            self.yaml_panel.editor.jump_to_line(line_no)
            return False
        try:
            if key == 'projects':
                new_doc, dropped = read_projects(text)
                if dropped:
                    raise ValueError('지원하지 않는 YAML 항목이 있어 저장할 수 없습니다: ' + ', '.join(dropped))
                canonical = dump_projects(new_doc)
                current_canonical = dump_projects(self.project_editor.projects)
            elif key.startswith('people:'):
                year = key.split(':', 1)[1]
                new_doc, dropped = read_people(text)
                if dropped:
                    raise ValueError('지원하지 않는 YAML 항목이 있어 저장할 수 없습니다: ' + ', '.join(dropped))
                if new_doc.year.strip() != year:
                    raise ValueError(f'이 탭의 연도({year})와 YAML의 year({new_doc.year})가 다릅니다. '
                                     '연도 변경은 연도 추가를 이용하세요.')
                canonical = dump_people(new_doc)
                current_canonical = dump_people(self.people_docs[year]) if year in self.people_docs else ''
            elif key.startswith('plan:'):
                year = key.split(':', 1)[1]
                new_doc = read_plan(text)
                if new_doc.year.strip() != year:
                    raise ValueError(f'이 탭의 연도({year})와 YAML의 year({new_doc.year})가 다릅니다. '
                                     '연도 변경은 연도 추가를 이용하세요.')
                canonical = dump_plan(new_doc)
                current_canonical = dump_plan(self.plan_docs[year]) if year in self.plan_docs else ''
            else:
                return False
        except (DocumentError, yaml.YAMLError, ValueError) as exc:
            msg = str(exc)
            line_no = extract_error_line(msg)
            if line_no is None and isinstance(exc, yaml.YAMLError) and getattr(exc, 'problem_mark', None):
                line_no = exc.problem_mark.line + 1
            self.warn('YAML 저장 실패', msg)
            if line_no:
                self.yaml_panel.editor.jump_to_line(line_no)
            return False

        if canonical == current_canonical:
            self.yaml_panel.mark_saved(key, canonical)
            self.statusBar().showMessage('변경된 내용이 없습니다.')
            return True

        old_snapshot = deepcopy(self._business_snapshot())
        old_proj_dirty = self.projects_dirty
        old_people_dirty = self.people_dirty.copy()
        old_plan_dirty = self.plan_dirty.copy()
        self._autosave_selection = (
            self.current_year,
            self.project_editor.current().alias if self.project_editor.current() else None,
            (self.planning_page.current() if self.planning else self.people_editor.current()).name
            if (self.planning_page.current() if self.planning else self.people_editor.current()) else None,
            self.project_editor.table.currentColumn(),
            self.planning_page.matrix.currentRow(),
            self.planning_page.matrix.currentColumn(),
        )
        self._history_before = self.history_context()
        self._history_action = 'YAML 편집'
        try:
            if key == 'projects':
                self.project_editor.set_projects(new_doc)
                self.people_editor.refresh()
                self.planning_page.refresh()
                self.projects_dirty = True
                self._autosave_touched_projects = True
            elif key.startswith('people:'):
                year = key.split(':', 1)[1]
                self.people_docs[year] = new_doc
                if year == self.current_year:
                    self.people_editor.blockSignals(True)
                    self.people_editor.set_doc(new_doc)
                    self.people_editor.blockSignals(False)
                self.people_dirty[year] = True
                self._autosave_touched_years.add(year)
            else:
                year = key.split(':', 1)[1]
                self.plan_docs[year] = new_doc
                if year == self.current_year:
                    self.planning_page.set_doc(new_doc, self.plan_sources.get(year) or f'(새 {year} 계획)')
                self.plan_dirty[year] = True
                self._autosave_touched_years.add(year)

            self._autosave_pending = True
            ok = self.flush_autosave()
        finally:
            self._history_action = None

        if not ok:
            self._autosave_restoring = True
            try:
                old_projects, old_people, old_plans = old_snapshot
                self.people_docs, self.plan_docs = old_people, old_plans
                self.project_editor.set_projects(old_projects)
                if self.current_year in self.people_docs:
                    self.people_editor.blockSignals(True)
                    self.people_editor.set_doc(self.people_docs[self.current_year])
                    self.people_editor.blockSignals(False)
                if self.current_year in self.plan_docs:
                    self.planning_page.set_doc(self.plan_docs[self.current_year],
                                               self.plan_sources.get(self.current_year) or f'(새 {self.current_year} 계획)')
                self.projects_dirty = old_proj_dirty
                self.people_dirty = old_people_dirty
                self.plan_dirty = old_plan_dirty
                self.refresh_marks()
            finally:
                self._autosave_restoring = False
            return False

        self.yaml_panel.sync_from_workspace(preferred_key=key, preserve_dirty=True)
        self.yaml_panel.mark_saved(key, canonical)
        self.statusBar().showMessage('YAML 저장 및 반영 완료')
        return True

    def history_context(self):
        project = self.project_editor.current()
        person = self.planning_page.current() if self.planning else self.people_editor.current()
        name = person.name if person else None
        if not self.planning and self.pages.currentIndex() == self.RESULT_PAGE and self._shown_person:
            name = self._shown_person
        return (self.current_year, self.pages.currentIndex(), self.input_tabs.currentIndex(),
                project.alias if project else None, name,
                self.result_tabs.currentIndex())

    def history_label(self, before, after):
        mode = '계획' if self.planning else '실적'
        if self._history_action:
            return f'{mode}·{self._history_action}'
        old_projects, old_people, old_plans = before
        new_projects, new_people, new_plans = after
        if dump_projects(old_projects) != dump_projects(new_projects):
            a, b = [p.alias for p in old_projects], [p.alias for p in new_projects]
            if sorted(a) == sorted(b) and a != b:
                kind = '순서 변경'
            elif len(a) == len(b) and a != b:
                kind = '약칭 변경'
            elif len(a) == len(b) and any(x.years != y.years for x, y in zip(old_projects, new_projects)):
                kind = '사업 배정 수정'
            else:
                kind = '사업 수정'
            return f'{mode}·사업 입력 — {kind}'
        year = self.current_year
        if self.planning:
            old = old_plans.get(year)
            new = new_plans.get(year)
            if self.pages.currentIndex() == self.RESULT_PAGE:
                kind = '재계산' if self.planning_page.last_generation is not None else '할당 수정'
                return f'{mode}·결과 — {kind}'
            if old and new and len(old.people) == len(new.people):
                if any(a.draft != b.draft for a, b in zip(old.people, new.people)):
                    return f'{mode}·사람 입력 — 참고 점수 수정'
                if any(a.priorities != b.priorities for a, b in zip(old.people, new.people)):
                    return f'{mode}·사람 입력 — 순위 수정'
            return f'{mode}·사람 입력 — 사람 수정'
        old, new = old_people.get(year), new_people.get(year)
        if old and new and [p.name for p in old.people] != [p.name for p in new.people]:
            if sorted(p.name for p in old.people) == sorted(p.name for p in new.people):
                where = '종합' if self.input_tabs.currentIndex() == self.OVERVIEW_TAB else '사람 입력'
                return f'{mode}·{where} — 사람 순서 변경'
            return f'{mode}·사람 입력 — ' + ('사람 이름 수정' if len(old.people) == len(new.people) else '사람 수정')
        if old and new and len(old.people) == len(new.people):
            kinds = set()
            for a, b in zip(old.people, new.people):
                if a.allocations != b.allocations:
                    kinds.add('참여율 수정')
                if a.absences != b.absences:
                    kinds.add('공백 기간 수정')
                if a.fixed != b.fixed:
                    kinds.add('월별 고정')
                if a.metadata != b.metadata:
                    kinds.add('추가정보 수정')
            if kinds:
                return f'{mode}·사람 입력 — ' + (next(iter(kinds)) if len(kinds) == 1 else '여러 항목 수정')
        return f'{mode}·사람 입력 — 참여율 수정'

    def _bind_autosave(self):
        """불러오기 경계에서만 단일 원본을 묶는다. 가져오기 자체는 저장하지 않는다."""
        self.history_invalidated.emit()
        self._autosave = None
        self._autosave_selection = None
        self._autosave_touched_projects = False
        self._autosave_touched_years.clear()
        if self.planning:
            people_paths = {year: Path(path).resolve() for year, path in self.plan_sources.items() if path}
        else:
            people_paths = {year: Path(paths[0]).resolve() for year, paths in self.people_sources.items() if len(paths) == 1}
        project_path = Path(self.projects_sources[0]).resolve() if len(self.projects_sources) == 1 else None
        if not people_paths and project_path is None:
            return
        paths = ([project_path] if project_path else []) + list(people_paths.values())
        if len(set(paths)) != len(paths):
            self.warn('자동 저장 차단', '같은 파일이 여러 문서의 원본으로 열렸습니다.')
            return
        try:
            for path in paths:
                expected = self.file_fingerprints.get(str(path))
                if expected is None or hashlib.sha256(path.read_bytes()).digest() != expected:
                    raise ValueError(f'불러온 뒤 외부에서 변경된 파일: {path}')
            encodings = {path: self.file_encodings[str(path)][0] for path in paths}

            def serialize(snapshot):
                projects, people, plans = snapshot
                texts = {project_path: dump_projects(projects)} if project_path else {}
                docs, dump = (plans, dump_plan) if self.planning else (people, dump_people)
                texts.update({path: dump(docs[year]) for year, path in people_paths.items()})
                result = {}
                for path, text in texts.items():
                    try:
                        result[path] = encode_yaml(text, encodings[path])
                    except UnicodeEncodeError as exc:
                        raise AutosaveEncodingError(path, encodings[path]) from exc
                return result

            session = TransactionalSession(self._business_snapshot(), serialize, backed_up=self.backed_up)
            if any(session.fingerprints[path] != self.file_fingerprints[str(path)] for path in paths):
                raise ValueError('불러오는 동안 원본이 외부에서 변경되었습니다.')
            self._autosave = session
            self._autosave_people = people_paths
            self._autosave_project = project_path
            self._autosave_paths = set(paths)
            self._autosave_encodings = encodings
            self._autosave_blocked = False
            self.update_save_status(reset=True)
        except (OSError, UnicodeError, ValueError, KeyError) as exc:
            self.save_failure_status(exc)
            self.warn('자동 저장 차단', str(exc))

    def _schedule_autosave(self):
        if self._autosave_restoring or self._import_depth or self._autosave_blocked:
            return
        if not self._autosave_pending:
            self.set_save_status('저장 중')
            self._capture_autosave_selection()
            self._autosave_pending = True
            QTimer.singleShot(0, self.flush_autosave)

    def _capture_autosave_selection(self):
        if (self._autosave is None or self._autosave_pending or self._autosave_selection is not None
                or self._import_depth or self._autosave_restoring):
            return
        project = self.project_editor.current()
        person = self.planning_page.current() if self.planning else self.people_editor.current()
        self._autosave_selection = (self.current_year,
                                    project.alias if project is not None else None,
                                    person.name if person is not None else None,
                                    self.project_editor.table.currentColumn(),
                                    self.planning_page.matrix.currentRow(),
                                    self.planning_page.matrix.currentColumn())
        self._history_before = self.history_context()

    def flush_autosave(self):
        if not self._autosave_pending:
            return not self._autosave_blocked
        self._autosave_pending = False
        if self._autosave_blocked:
            return False
        if ((self._autosave_touched_projects and self._binding_deferred_projects)
                or self._autosave_touched_years & self._binding_deferred_years):
            self._autosave_touched_projects |= self._binding_deferred_projects
            self._autosave_touched_years.update(self._binding_deferred_years)
        unbound_project = self._autosave_touched_projects and (not self._autosave or self._autosave_project is None)
        unbound_years = {year for year in self._autosave_touched_years
                         if not self._autosave or year not in self._autosave_people}
        if unbound_project or unbound_years:
            return self._save_new_bindings(unbound_project, unbound_years)
        if not self._autosave:
            return False
        old = self._autosave.state
        current = self._business_snapshot()
        projects_changed, people_changed = True, set(self._autosave_people)
        try:
            projects_changed = (self._autosave_project is not None
                                and dump_projects(old[0]) != dump_projects(current[0]))
            index, dump = (2, dump_plan) if self.planning else (1, dump_people)
            people_changed = {y for y in self._autosave_people
                              if y in current[index] and dump(old[index][y]) != dump(current[index][y])}
            unsafe = self._autosave_paths & self._unsupported_sources
            if unsafe:
                raise ValueError('지원하지 않는 항목을 metadata로 옮긴 뒤 다시 불러오세요: ' + ', '.join(map(str, unsafe)))
            # 저장 위치가 없는 별도 문서는 이 작업과 무관하면 메모리에 유지한다.
            candidate = list(deepcopy(current))
            if self._autosave_project is None:
                candidate[0] = deepcopy(old[0])
            if (not self.planning and self.current_year in self._autosave_people
                    and current[1][self.current_year].year != self.current_year):
                year = self.current_year
                if [p.alias for p in old[0]] != [p.alias for p in current[0]]:
                    raise ValueError('연도 변경 중 약칭 변경을 저장하려면 먼저 새 연도의 저장 위치를 지정하세요.')
                candidate[index][year] = deepcopy(old[index][year])
                people_changed.discard(year)
            for year in (set(candidate[index]) | set(old[index])) - set(self._autosave_people):
                if year in old[index]:
                    candidate[index][year] = deepcopy(old[index][year])
                else:
                    candidate[index].pop(year, None)
            other, other_dump = (1, dump_people) if self.planning else (2, dump_plan)
            candidate[other] = deepcopy(old[other])
            changed_encodings = {}
            label = self.history_label(old, current)
            did_commit = False
            try:
                while True:
                    try:
                        did_commit = self._autosave.commit(label, tuple(candidate))
                        break
                    except AutosaveEncodingError as exc:
                        if not self.confirm('UTF-8로 저장할까요?',
                                            f'{exc}\n이 파일을 UTF-8로 바꿔 저장할까요?'):
                            raise
                        changed_encodings[exc.path] = self._autosave_encodings[exc.path]
                        self._autosave_encodings[exc.path] = 'utf-8'
            except Exception:
                self._autosave_encodings.update(changed_encodings)
                raise
        except (OSError, UnicodeError, ValueError, TypeError, DocumentError) as exc:
            self._restore_autosave(old, projects_changed or self._autosave_touched_projects,
                                   people_changed | self._autosave_touched_years)
            self._autosave_touched_projects = False
            self._autosave_touched_years.clear()
            self._history_before = None
            if '원본 복구 실패' in str(exc):
                self._autosave_blocked = True
            self.save_failure_status(exc)
            self.warn('자동 저장 실패', str(exc))
            return False
        for path, fingerprint in self._autosave.fingerprints.items():
            self.file_fingerprints[str(path)] = fingerprint
            self.file_encodings[str(path)] = (self._autosave_encodings[path], '저장 인코딩')
            self.read_encodings[str(path)] = self._autosave_encodings[path]
        if self._autosave_project:
            self.document_encodings['projects'] = {self._autosave_encodings[self._autosave_project]}
        prefix = 'plan:' if self.planning else 'people:'
        for year, path in self._autosave_people.items():
            self.document_encodings[prefix + year] = {self._autosave_encodings[path]}
        self.persist_read_encodings()
        self.refresh_encoding_label()
        self.backed_up.update(self._autosave.backed_up)
        if did_commit:
            self.history_committed.emit(self._autosave, label,
                                        self._history_before or self.history_context(), self.history_context())
        self._history_before = None
        self._autosave_selection = None
        if projects_changed or self._autosave_touched_projects:
            self.projects_dirty = False
        dirty = self.plan_dirty if self.planning else self.people_dirty
        for year in (people_changed | self._autosave_touched_years) & set(self._autosave_people):
            dirty[year] = False
        self._autosave_touched_projects = False
        self._autosave_touched_years.clear()
        self.refresh_marks()
        self.update_save_status(reset=True)
        self.statusBar().showMessage('원본 자동 저장됨')
        return True

    def _restore_autosave(self, snapshot, projects_changed, people_changed):
        project_row = self.project_editor.table.currentRow()
        person_list = self.planning_page.people_list if self.planning else self.people_editor.people_list
        person_row = person_list.currentRow()
        selection = self._autosave_selection
        self._autosave_selection = None
        self._autosave_restoring = True
        try:
            projects, old_people, old_plans = snapshot
            if self._autosave_project is None:
                projects = self.project_editor.projects
            if self.planning:
                old_people = self.people_docs
                old_plans.update({y: d for y, d in self.plan_docs.items() if y not in self._autosave_people})
            else:
                old_plans = self.plan_docs
                old_people.update({y: d for y, d in self.people_docs.items() if y not in self._autosave_people})
            self.people_docs, self.plan_docs = old_people, old_plans
            self.project_editor.set_projects(projects)
            if selection and selection[0] in self.people_docs:
                self.current_year = selection[0]
            if self.current_year in self.people_docs:
                self.select_year(self.current_year)
            if selection:
                project_row = next((i for i, p in enumerate(projects) if p.alias == selection[1]), project_row)
                doc = self.plan_docs[self.current_year] if self.planning else self.people_editor.doc
                person_row = next((i for i, p in enumerate(doc.people)
                                   if p.name == selection[2]), person_row)
            if self.project_editor.projects and project_row >= 0:
                column = max(0, selection[3]) if selection else 0
                self.project_editor.table.setCurrentCell(min(project_row, len(self.project_editor.projects) - 1), column)
            doc = self.plan_docs[self.current_year] if self.planning else self.people_editor.doc
            if doc.people and person_row >= 0:
                person_list.setCurrentRow(min(person_row, len(doc.people) - 1))
            if self.planning and selection and selection[4] >= 0 and selection[5] >= 0:
                self.planning_page.matrix.setCurrentCell(selection[4], selection[5])
            if projects_changed:
                self.projects_dirty = False
            dirty = self.plan_dirty if self.planning else self.people_dirty
            for year in people_changed & set(self._autosave_people):
                dirty[year] = False
            self.refresh_marks()
        finally:
            self._autosave_restoring = False

    def people_changed(self):
        self.set_people_dirty(self.current_year, True)
        self._autosave_touched_years.add(self.current_year)
        if self.current_year in self.people_docs and self.people_docs[self.current_year].year != self.current_year:
            # 연도 재바인딩은 별도 작업이다. 타이핑 중 원본 연도를 덮어쓰지 않는다.
            self._autosave_pending = False
            self._autosave_touched_years.discard(self.current_year)
            self.statusBar().showMessage('연도 변경은 저장 위치 확인이 필요합니다.')
            self.set_save_status('저장 위치 필요')
            return
        self._schedule_autosave()

    def plan_changed(self):
        if self.current_year in self.plan_dirty:
            self.plan_dirty[self.current_year] = True
        self._autosave_touched_years.add(self.current_year)
        self.refresh_marks()
        self._schedule_autosave()

    def refresh_marks(self):
        """탭·메뉴·연도 목록·사업 줄에 미내보내기 표시(*)를 다시 그린다."""
        self.input_tabs.setTabText(self.PROJECTS_TAB, '사업 입력' + (' *' if self.projects_dirty else ''))
        any_people = any(self.people_dirty.values())
        any_plan = any(self.plan_dirty.values())
        self.input_tabs.setTabText(self.PEOPLE_TAB, '사람 입력' + (' *' if (any_plan if self.planning else any_people) else ''))
        self.pages.setTabText(self.INPUT_PAGE, '입력' + (' *' if self.projects_dirty or (any_plan if self.planning else any_people) else ''))
        yaml_dirty = hasattr(self, 'yaml_panel') and self.yaml_panel.has_dirty()
        self.pages.setTabText(self.YAML_PAGE, 'YAML 편집' + (' *' if yaml_dirty else ''))
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
        self.refresh_encoding_label()

    def refresh_encoding_label(self):
        def label(key):
            codes = self.document_encodings.get(key, set())
            return ' / '.join(ENCODINGS[code] for code in sorted(codes)) if codes else 'UTF-8 (신규)'
        key = ('plan:' if self.planning else 'people:') + str(self.current_year)
        self.encoding_label.setText(f'사업: {label("projects")} · 사람: {label(key)}')
        self.encoding_label.setToolTip('\n'.join(
            f'{path}: {ENCODINGS[code]}' + (f' · {note}' if note else '')
            for path, (code, note) in self.file_encodings.items())
            + '\n자동 판별은 추정입니다. CP949/EUC-KR 공통 바이트는 CP949, ASCII는 UTF-8로 처리합니다.')

    def remember_encoding(self, path, decoded, key):
        path = str(Path(path).resolve())
        self.file_encodings[path] = (decoded.encoding, decoded.note)
        self.document_encodings.setdefault(key, set()).add(decoded.encoding)

    def merged_metadata(self, paths, incoming_path, incoming_metadata):
        """파일별 metadata를 별도 항목으로 보존하여 중복 키도 잃지 않는다."""
        return {'prouman_source_metadata': [
            {'source': str(Path(path).resolve()),
             'metadata': deepcopy(incoming_metadata if str(Path(path).resolve()) == incoming_path
                                  else self._source_metadata.get(str(Path(path).resolve())))}
            for path in paths]}

    def choose_source_path(self, kind, year, sources):
        """새 원본 또는 통합 원본의 경로를 사용자가 직접 선택한다."""
        title = '사업 저장 위치' if kind == 'projects' else f'{year}년 {"계획" if self.planning else "사람"} 저장 위치'
        if len(sources) > 1 and not self.confirm(
                '통합 파일 만들기',
                '새 통합 파일을 만들고 기존 원본을 유지합니다. 파일 공통 metadata는 원본별 항목으로 보존됩니다. 계속할까요?'):
            return ''
        path, _ = QFileDialog.getSaveFileName(self, title, self.start_folder('export') or '.', YAML_FILTER)
        return path

    def browse_bind_sources(self):
        """경로 선택 취소 후에도 작업 자료의 저장 위치를 다시 지정할 수 있다."""
        if not self.planning and self.people_editor.doc.year.strip() != self.current_year:
            return self.flush_year_edit()
        project = self.projects_dirty and (not self._autosave or self._autosave_project is None)
        dirty = self.plan_dirty if self.planning else self.people_dirty
        years = {y for y, value in dirty.items() if value and (not self._autosave or y not in self._autosave_people)}
        if not project and not years:
            self.statusBar().showMessage('저장 위치가 필요한 작업 문서가 없습니다.')
            return False
        return self._save_new_bindings(project, years)

    def _save_new_bindings(self, unbound_project, unbound_years):
        """경로 선정부터 전체 묶음 교체 성공까지 기존 바인딩과 이력을 유지한다."""
        self._autosave_pending = False
        selected = []
        success = False
        touched_projects = self._autosave_touched_projects
        touched_years = set(self._autosave_touched_years)
        try:
            requests = ([('projects', None, list(self.projects_sources))] if unbound_project else [])
            for year in sorted(unbound_years):
                sources = ([self.plan_sources[year]] if self.planning and self.plan_sources.get(year)
                           else ([] if self.planning else list(self.people_sources.get(year, []))))
                requests.append(('plan' if self.planning else 'people', year, sources))
            protected = {Path(p).resolve() for p in self.loaded_files}
            protected.update(Path(p).resolve() for p in getattr(self, 'protected_paths', lambda: [])())
            for kind, year, sources in requests:
                chosen = self.choose_source_path(kind, year, sources)
                if not chosen:
                    self.statusBar().showMessage('저장 위치 필요 · 편집 내용은 메모리에 보존됩니다.')
                    self.set_save_status('저장 위치 필요')
                    return False
                path = Path(chosen).resolve()
                if path in protected or any(path == item[2] for item in selected):
                    raise ValueError(f'열린 원본 또는 다른 문서와 같은 저장 경로입니다: {path}')
                existing = hashlib.sha256(path.read_bytes()).digest() if path.exists() else None
                if existing is not None and not self.confirm('기존 파일 덮어쓰기', f'{path}\n기존 파일을 교체할까요?'):
                    self.set_save_status('저장 위치 필요')
                    return False
                selected.append((kind, year, path, sources, existing))
            if self._autosave:
                self._autosave.check_fingerprints()
                unsafe = self._autosave_paths & self._unsupported_sources
                if unsafe:
                    raise ValueError('지원하지 않는 원본 항목이 있어 저장할 수 없습니다: '
                                     + ', '.join(map(str, unsafe)))
            guards = dict(self._autosave.fingerprints) if self._autosave else {}
            for _, _, _, sources, _ in selected:
                for source in sources:
                    path = Path(source).resolve()
                    expected = self.file_fingerprints.get(str(path))
                    if expected is None or not path.exists() or hashlib.sha256(path.read_bytes()).digest() != expected:
                        raise ValueError(f'원본 외부 변경 충돌: {path}')
                    guards[path] = expected
                    if path in self._unsupported_sources:
                        raise ValueError(f'지원하지 않는 원본 항목이 있습니다: {path}')
            jobs = []
            expected = {}
            encodings = {}
            old = self._autosave.state if self._autosave else None
            current = self._business_snapshot()
            index, dump = (2, dump_plan) if self.planning else (1, dump_people)
            if old and self._autosave_project and dump_projects(old[0]) != dump_projects(current[0]):
                path = self._autosave_project
                jobs.append((path, dump_projects(current[0]), 'projects'))
                expected[path] = self._autosave.fingerprints[path]
            if old:
                for year, path in self._autosave_people.items():
                    if year in old[index] and year in current[index] and dump(old[index][year]) != dump(current[index][year]):
                        jobs.append((path, dump(current[index][year]), ('plan:' if self.planning else 'people:') + year))
                        expected[path] = self._autosave.fingerprints[path]
            for kind, year, path, _, fingerprint in selected:
                text = dump_projects(current[0]) if kind == 'projects' else dump(current[index][year])
                jobs.append((path, text, kind if year is None else kind + ':' + year))
                expected[path] = fingerprint
            prepared = []
            for path, text, key in jobs:
                code = (self._autosave_encodings[path] if self._autosave and path in self._autosave_encodings
                        else self.choose_output_encoding(path, key, True))
                if code is None:
                    self.set_save_status('저장 위치 필요')
                    return False
                try:
                    data = encode_yaml(text, code)
                except UnicodeEncodeError:
                    if not self.confirm('UTF-8로 저장할까요?', f'{path}\n원본 인코딩으로 표현할 수 없습니다. UTF-8로 저장할까요?'):
                        self.set_save_status('저장 위치 필요')
                        return False
                    code, data = 'utf-8', encode_yaml(text, 'utf-8')
                prepared.append((path, data))
                encodings[path] = code
            self.set_save_status('저장 중')
            write_bundle(prepared, self.backed_up, expected=expected, guards=guards)
            success = True
        except (OSError, UnicodeError, ValueError, TypeError, DocumentError) as exc:
            if '원본 복구 실패' in str(exc):
                self._autosave_blocked = True
            self.save_failure_status(exc)
            self.warn('저장 위치 지정 실패', str(exc))
            return False
        finally:
            if success:
                self._binding_deferred_projects = False
                self._binding_deferred_years.clear()
            elif (touched_projects and touched_years) or len(touched_years) > 1:
                self._binding_deferred_projects |= touched_projects
                self._binding_deferred_years.update(touched_years)
            self._autosave_touched_projects = False
            self._autosave_touched_years.clear()
            self._autosave_selection = None
            self._history_before = None
        for path, data in prepared:
            self.file_fingerprints[str(path)] = hashlib.sha256(data).digest()
            self.file_encodings[str(path)] = (encodings[path], '저장 인코딩')
            self.read_encodings[str(path)] = encodings[path]
        for kind, year, path, sources, _ in selected:
            if kind == 'projects':
                self.projects_sources = [str(path)]
                self.projects_dirty = False
                self._source_metadata[str(path)] = deepcopy(getattr(self.project_editor.projects, 'metadata', None))
            elif kind == 'plan':
                self.plan_sources[year] = str(path)
                self.plan_dirty[year] = False
                self._source_metadata[str(path)] = deepcopy(self.plan_docs[year].metadata)
            else:
                self.people_sources[year] = [str(path)]
                self.people_dirty[year] = False
                self._source_metadata[str(path)] = deepcopy(self.people_docs[year].metadata)
            self.document_encodings[kind if year is None else kind + ':' + year] = {encodings[path]}
            self.loaded_files = [p for p in self.loaded_files if p not in sources]
            self.loaded_files.append(str(path))
        for path, _, key in jobs:
            self.document_encodings[key] = {encodings[path]}
            if key == 'projects':
                self.projects_dirty = False
            elif key.startswith('plan:'):
                self.plan_dirty[key[5:]] = False
            elif key.startswith('people:'):
                self.people_dirty[key[7:]] = False
        self.settings.setValue('loaded_files', self.loaded_files)
        self.persist_read_encodings()
        self._bind_autosave()
        self.refresh_marks()
        self.update_save_status(reset=True)
        self.statusBar().showMessage('저장 위치 지정 및 원본 저장 완료')
        return True

    def persist_read_encodings(self):
        self.settings.setValue('read_encodings', json.dumps(self.read_encodings, ensure_ascii=False))

    def choose_output_encoding(self, path, key, interactive):
        """기존 대상 파일 우선, 새 경로는 원본 문서의 인코딩을 따른다."""
        if path.exists():
            return decode_yaml(path.read_bytes(), self.read_encodings.get(str(path.resolve()), 'auto')).encoding
        codes = self.document_encodings.get(key, set())
        if len(codes) <= 1:
            return next(iter(codes), 'utf-8')
        if not interactive:
            raise ValueError(f'{path}: 원본 인코딩이 혼합되어 있습니다. 저장 인코딩을 직접 선택하세요.')
        label, ok = QInputDialog.getItem(self, '저장 인코딩 선택',
                                        f'{path.name}: 원본 인코딩이 혼합되어 있습니다.',
                                        list(ENCODINGS.values()), 0, False)
        return next(code for code, text in ENCODINGS.items() if text == label) if ok else None

    def write_yaml_jobs(self, jobs, encoding='preserve', interactive=False, adopt_document=True, expected=None):
        """변환·사용자 확인이 모두 끝나야 파일 묶음을 교체한다. 실패 시 메타데이터도 유지한다."""
        prepared, resolved = [], []
        try:
            for path, text, key in jobs:
                path = Path(path)
                code = (self.choose_output_encoding(path, key, interactive)
                        if encoding == 'preserve' else encoding)
                if code is None:
                    return False
                try:
                    data = encode_yaml(text, code)
                except UnicodeEncodeError:
                    if not interactive:
                        raise ValueError(f'{path}: {ENCODINGS[code]}로 표현할 수 없는 문자가 있습니다. '
                                         'UTF-8로 지정하여 다시 저장하세요.')
                    if not self.confirm('UTF-8로 저장할까요?',
                                        f'{path}\n{ENCODINGS[code]}로 표현할 수 없는 문자가 있습니다. '
                                        '이 파일을 UTF-8로 바꿔 저장할까요? (아니요: 전체 저장 취소)'):
                        return False
                    code, data = 'utf-8', encode_yaml(text, 'utf-8')
                prepared.append((path, data))
                resolved.append((path, key, code))
            write_bundle(prepared, self.backed_up, expected=expected)
        except (OSError, UnicodeError, ValueError) as exc:
            if '원본 복구 실패' in str(exc):
                self._autosave_blocked = True
            self.save_failure_status(exc)
            self.warn('YAML 저장 실패', str(exc))
            return False
        for (path, key, code), (_, data) in zip(resolved, prepared):
            canonical = str(path.resolve())
            self.file_fingerprints[canonical] = hashlib.sha256(data).digest()
            self.file_encodings[canonical] = (code, '저장 인코딩')
            self.read_encodings[canonical] = code
            # 병합 결과를 저장한 다음에는 이 인코딩이 새 경로 저장의 기준이 된다.
            if adopt_document:
                self.document_encodings[key] = {code}
        self.persist_read_encodings()
        self.refresh_encoding_label()
        return True

    def projects_changed(self):
        self.set_projects_dirty(True)
        self._autosave_touched_projects = True
        self.people_editor.refresh()
        self.planning_page.refresh_review()
        self._schedule_autosave()

    def alias_renamed(self, old, new):
        for year, doc in self.people_docs.items():
            if self.people_editor.count_alias(old, doc):
                self.people_editor.rename_alias(old, new, doc)
                self.set_people_dirty(year, True)
                if not self.planning:
                    self._autosave_touched_years.add(year)
        for year, doc in self.plan_docs.items():
            changed = False
            for person in doc.people:
                if old in person.draft or old in person.priorities or old in (person.allocations or {}):
                    person.draft = {new if key == old else key: value for key, value in person.draft.items()}
                    person.priorities = {new if key == old else key: value
                                         for key, value in person.priorities.items()}
                    if person.allocations is not None:
                        person.allocations = {new if key == old else key: value
                                              for key, value in person.allocations.items()}
                    changed = True
            if changed:
                self.plan_dirty[year] = True
                if self.planning:
                    self._autosave_touched_years.add(year)
        self.planning_page.refresh()
        self.refresh_marks()

    def remove_alias_everywhere(self, alias):
        for year, doc in self.people_docs.items():
            if any(alias in person.allocations or alias in person.fixed for person in doc.people):
                self.people_editor.remove_alias(alias, doc)
                self.set_people_dirty(year, True)
                if not self.planning:
                    self._autosave_touched_years.add(year)
        for year, doc in self.plan_docs.items():
            changed = False
            for person in doc.people:
                if alias in person.draft or alias in person.priorities or alias in (person.allocations or {}):
                    person.priorities.pop(alias, None)
                    person.draft.pop(alias, None)
                    if person.allocations is not None:
                        person.allocations.pop(alias, None)
                    changed = True
            if changed:
                self.plan_dirty[year] = True
                if self.planning:
                    self._autosave_touched_years.add(year)
        self.planning_page.refresh()
        self.refresh_marks()

    def allow_remove_project(self, project):
        count = sum(self.people_editor.count_alias(project.alias, doc) for doc in self.people_docs.values())
        plan_count = sum(project.alias in person.draft for doc in self.plan_docs.values() for person in doc.people)
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
        if not self.confirm_leave_yaml():
            return False
        focus = QApplication.focusWidget()
        if focus is not None and self.isAncestorOf(focus):
            focus.clearFocus()
        if not self.flush_autosave():
            return False
        if not (self.projects_dirty or any(self.people_dirty.values()) or any(self.plan_dirty.values())):
            return True
        answer = self.ask_export(what)
        if answer == 'discard':
            return True
        if answer != 'export':
            return False
        return self.browse_bind_sources()

    def keep_year(self, year):
        """해당 연도의 변경을 처리한다. 진행해도 되면 True."""
        prefix = 'plan:' if self.planning else 'people:'
        if hasattr(self, 'yaml_panel') and not self.yaml_panel.confirm_leave_tab(prefix + str(year)):
            return False
        if not (self.people_dirty.get(year) or self.plan_dirty.get(year)):
            return True
        answer = self.ask_export(f'{year}년 사람 입력')
        if answer == 'discard':
            return True
        if answer != 'export':
            return False
        return self.browse_bind_sources()

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
        if not self.confirm_leave_yaml():
            return
        paths, encoding = choose_files(self, self.start_folder('import') or '.')
        if paths:
            self.settings.setValue('import_folder', str(Path(paths[0]).parent))
            self.run_import(paths, encoding=encoding)

    def browse_prepare_plan(self):
        """명시적인 명령으로만 실적 사람 파일을 새 계획 참고값으로 변환한다."""
        paths, encoding = choose_files(self, self.start_folder('import') or '.', single=True)
        if not paths:
            return False
        path = paths[0]
        try:
            decoded = decode_yaml(Path(path).read_bytes(), encoding)
            doc, dropped = read_people(decoded.text)
            if dropped:
                raise DocumentError('지원하지 않는 항목이 있어 변환하지 않았습니다: ' + ', '.join(dropped))
            year, ok = QInputDialog.getText(self, '계획 연도', '새 계획 연도 (휴직 날짜는 자동 이동하지 않습니다):',
                                            text=str(int(doc.year) + 1) if valid_year(doc.year) else '')
            if not ok:
                return False
            if not self.prepare_plan(doc, year):
                return False
            self.document_encodings['plan:' + self.current_year] = {decoded.encoding}
            self.refresh_encoding_label()
            return True
        except (OSError, UnicodeError, DocumentError) as exc:
            self.warn('계획 준비 실패', str(exc))
            return False

    def prepare_plan(self, people, year):
        if not self.planning:
            return False
        try:
            doc = plan_from_people(people, str(year).strip())
        except DocumentError as exc:
            self.warn('계획 준비 실패', str(exc))
            return False
        if doc.year in self.plan_docs and self.plan_docs[doc.year].people:
            if not self.confirm('계획 교체', f'{doc.year}년의 현재 계획을 새 참고 자료로 바꿀까요?'):
                return False
        if not self.flush_autosave():
            return False
        self.people_docs.setdefault(doc.year, PeopleDoc(doc.year, []))
        self.people_dirty.setdefault(doc.year, False)
        self.people_sources.setdefault(doc.year, [])
        self.plan_docs[doc.year] = doc
        self.plan_sources[doc.year] = ''
        self.plan_dirty[doc.year] = True
        self.document_encodings.pop('plan:' + doc.year, None)
        self.select_year(doc.year)
        self.show_page(self.INPUT_PAGE)
        self.input_tabs.setCurrentIndex(self.PEOPLE_TAB)
        message = '실적 할당값을 참고 점수로 복사했습니다. 사업 조건과 휴직 날짜를 확인하세요.'
        if any(person.fixed for person in people.people):
            message += ' 이전 연도의 월 고정값은 복사하지 않았습니다.'
        self.statusBar().showMessage(message)
        self.update_save_status()
        return True

    def run_import(self, paths, encoding=None):
        """불러온 뒤 실패·지원 외 항목을 모아 한 번씩만 알린다."""
        errors, dropped = self.import_files(paths, encoding=encoding)
        if errors:
            self.warn('불러오기', '다음 파일은 열지 못했습니다:\n' + '\n'.join(errors))
        if dropped:
            lines = [f'{name}: {entry}' for name, entries in dropped for entry in entries]
            self.warn('지원하지 않는 항목', '표 편집기에서 다루지 않는 다음 항목은 내보낼 때 빠집니다.\n'
                      + '\n'.join(lines[:20]) + ('\n...' if len(lines) > 20 else ''))

    def import_files(self, paths, encoding=None):
        if not self._import_depth and not self.confirm_leave_yaml():
            return ['YAML 미저장 변경으로 불러오기를 취소했습니다.'], []
        if not self._import_depth and not self.flush_autosave():
            return ['기존 변경을 저장하지 못해 불러오기를 취소했습니다.'], []
        if not self._import_depth and (self._binding_deferred_projects or self._binding_deferred_years):
            return ['연관된 미저장 변경의 저장 위치를 먼저 지정하세요.'], []
        before = len(self.loaded_files)
        self._import_depth += 1
        try:
            return self._import_files(paths, encoding)
        finally:
            self._import_depth -= 1
            if not self._import_depth and len(self.loaded_files) != before:
                self.history_invalidated.emit()
                self._bind_autosave()
            if not self._import_depth and hasattr(self, 'yaml_panel'):
                self.yaml_panel.sync_from_workspace(preserve_dirty=True)

    def _import_files(self, paths, encoding=None):
        """파일을 추가로 불러온다. 성공한 것만 반영하고 (실패 설명, 지원 외 항목)을 돌려준다."""
        errors, dropped, imported_people, migrated = [], [], [], []
        for path in paths:
            try:
                canonical = str(Path(path).resolve())
                selected = encoding if encoding is not None else self.read_encodings.get(canonical, 'auto')
                raw_bytes = Path(path).read_bytes()
                decoded = decode_yaml(raw_bytes, selected)
                text = decoded.text
                kind = detect_kind(text)
                if kind is None:
                    raise DocumentError('사업 파일도 사람 파일도 아닙니다.')
                if kind == 'plan' and not self.planning:
                    raise DocumentError('계획 파일은 상단의 계획작업 모드에서 불러오세요.')
                if kind == 'people' and self.planning:
                    raise DocumentError('실적 파일은 「실적 파일에서 계획 준비」로 새 연도를 지정해 가져오세요.')
                if kind == 'projects':
                    document, file_dropped = read_projects(text)
                elif kind == 'plan':
                    document, file_dropped = read_plan(text), []
                else:
                    document, file_dropped = read_people(text)
            except (OSError, UnicodeError, DocumentError, ValueError) as exc:
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
                if pristine:
                    self.project_editor.set_projects(document)
                else:
                    if not isinstance(self.project_editor.projects, ProjectList):
                        self.project_editor.projects = ProjectList(self.project_editor.projects)
                    self.project_editor.projects.extend(document)
                    self.project_editor.projects.metadata = self.merged_metadata(
                        self.projects_sources + [str(path)], canonical, document.metadata)
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
                self.plan_dirty[year] = document.migrated
                if document.migrated:
                    migrated.append(Path(path).name)
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
                    existing.metadata = self.merged_metadata(self.people_sources[year] + [str(path)],
                                                             canonical, document.metadata)
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
            self._source_metadata[canonical] = deepcopy(document.metadata if kind != 'projects'
                                                        else getattr(document, 'metadata', None))
            if file_dropped:
                self._unsupported_sources.add(Path(path).resolve())
            else:
                self._unsupported_sources.discard(Path(path).resolve())
            self.file_fingerprints[canonical] = hashlib.sha256(raw_bytes).digest()
            key = 'projects' if kind == 'projects' else kind + ':' + year
            self.remember_encoding(path, decoded, key)
            if selected == 'auto':
                self.read_encodings.pop(canonical, None)
            else:
                self.read_encodings[canonical] = selected
            self.persist_read_encodings()
            self.settings.setValue('loaded_files', self.loaded_files)
        if imported_people and not self.project_editor.projects and not self.projects_dirty:
            # 사업 입력이 비어 있을 때만 사람 파일 옆의 사업 파일을 자동으로 가져온다.
            found = find_projects_file(imported_people[0])
            if found and found not in [str(p) for p in paths]:
                sub_errors, sub_dropped = self.import_files([found])
                errors.extend(sub_errors)
                dropped.extend(sub_dropped)
        if not errors:
            message = '불러오기: ' + ', '.join(Path(p).name for p in paths)
            if migrated:
                message += ' · 이전 계획 형식 변환 (참고점수 0, 결과·순위 보존): ' + ', '.join(migrated)
            self.statusBar().showMessage(message)
        self.refresh_encoding_label()
        self.update_save_status()
        return errors, dropped

    def select_year(self, year):
        """고른 연도 문서를 편집기에 올린다. 사업 연도 표도 그 연도 기준으로 옮긴다."""
        if (year != self.current_year and not self._import_depth and not self._autosave_restoring
                and not self.flush_autosave()):
            return
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
        self.update_save_status()
        if not self._import_depth and hasattr(self, 'yaml_panel'):
            self.yaml_panel.sync_from_workspace(preserve_dirty=True)

    def year_combo_changed(self, index):
        year = self.year_combo.itemData(index)
        if year is not None and year != self.current_year:
            if not self.confirm_leave_yaml() or not self.flush_autosave():
                self.refresh_year_combo()
                return
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
        old_year = self.current_year
        if self.plan_sources.get(old_year) or self.plan_docs.get(old_year, PlanDoc()).people:
            self.warn('연도 입력', '기존 계획 문서가 있어 연도를 변경할 수 없습니다. 새 연도를 추가하세요.')
            return False
        old_people, old_dirty, old_sources = (self.people_docs.copy(), self.people_dirty.copy(),
                                               deepcopy(self.people_sources))
        old_plans, old_plan_dirty, old_plan_sources = (self.plan_docs.copy(), self.plan_dirty.copy(),
                                                       self.plan_sources.copy())
        old_encoding = deepcopy(self.document_encodings)
        self.people_docs[typed] = self.people_docs.pop(old_year)
        self.people_dirty[typed] = self.people_dirty.pop(old_year)
        self.people_sources[typed] = self.people_sources.pop(old_year)
        self.plan_docs.pop(old_year, None)
        self.plan_dirty.pop(old_year, None)
        self.plan_sources.pop(old_year, None)
        self.plan_docs[typed] = PlanDoc(typed, [])
        self.plan_dirty[typed] = False
        self.plan_sources[typed] = ''
        if 'people:' + old_year in self.document_encodings:
            self.document_encodings['people:' + typed] = set(self.document_encodings['people:' + old_year])
        self.current_year = typed
        self._autosave_touched_years.add(typed)
        if not self._save_new_bindings(False, {typed}):
            self.people_docs, self.people_dirty, self.people_sources = old_people, old_dirty, old_sources
            self.plan_docs, self.plan_dirty, self.plan_sources = old_plans, old_plan_dirty, old_plan_sources
            self.document_encodings = old_encoding
            self.current_year = old_year
            self.refresh_marks()
            return False
        self.settings.setValue('current_year', typed)
        self.select_year(typed)
        return True

    def add_year(self):
        if not self.confirm_leave_yaml():
            return
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
        if not self.flush_autosave():
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
        if not self.confirm_leave_yaml() or not self.flush_autosave():
            return
        if not self.keep_year(year):
            return
        self._autosave = None
        years = sorted(self.people_docs)
        rest = [y for y in years if y != year]
        closed_sources = self.people_sources.pop(year, [])
        plan_source = self.plan_sources.pop(year, '')
        del self.people_docs[year]
        del self.people_dirty[year]
        self.plan_docs.pop(year, None)
        self.plan_dirty.pop(year, None)
        self.document_encodings.pop('people:' + year, None)
        self.document_encodings.pop('plan:' + year, None)
        self.loaded_files = [p for p in self.loaded_files if p not in closed_sources]
        if plan_source:
            self.loaded_files = [p for p in self.loaded_files if p != plan_source]
        self.settings.setValue('loaded_files', self.loaded_files)
        self.select_year(rest[min(years.index(year), len(rest) - 1)])
        self._bind_autosave()
        self.statusBar().showMessage(f'연도 닫기: {year}')

    def new_all(self):
        """미반영 변경을 처리한 뒤 사업·사람 전체를 비우고 올해 빈 연도로 시작한다."""
        if not self.keep_all():
            return
        self.history_invalidated.emit()
        self._autosave = None
        self._binding_deferred_projects = False
        self._binding_deferred_years.clear()
        self._unsupported_sources.clear()
        self.project_editor.set_projects([])
        today = PeopleDoc().year
        self.people_docs = {today: PeopleDoc()}
        self.people_dirty = {today: False}
        self.people_sources = {today: []}
        self.plan_docs = {today: PlanDoc(today, [])}
        self.plan_dirty = {today: False}
        self.plan_sources = {today: ''}
        self.projects_sources = []
        self.document_encodings.clear()
        self.file_encodings.clear()
        self.file_fingerprints.clear()
        self._source_metadata.clear()
        self.loaded_files = []
        self.settings.setValue('loaded_files', [])
        self.set_projects_dirty(False)
        self.current_year = None
        self.select_year(today)
        if hasattr(self, 'yaml_panel'):
            self.yaml_panel.sync_from_workspace(preserve_dirty=False)
        self.show_page(self.INPUT_PAGE)
        self.statusBar().showMessage('새로 만들기: 빈 입력으로 시작합니다.')
        self.update_save_status(reset=True)

    def browse_new(self):
        """두 파일의 위치를 먼저 선택하고 생성이 완료된 뒤 현재 문서를 교체한다."""
        year = PeopleDoc().year
        kind = 'plan' if self.planning else 'people'
        chosen = []
        protected = {Path(p).resolve() for p in self.loaded_files}
        protected.update(Path(p).resolve() for p in getattr(self, 'protected_paths', lambda: [])())
        try:
            for name, target_year in (('projects', None), (kind, year)):
                raw = self.choose_source_path(name, target_year, [])
                if not raw:
                    return False
                path = Path(raw).resolve()
                if path in protected or any(path == item[0] for item in chosen):
                    raise ValueError(f'열린 원본 또는 다른 문서와 같은 경로입니다: {path}')
                fingerprint = hashlib.sha256(path.read_bytes()).digest() if path.exists() else None
                if fingerprint is not None and not self.confirm('기존 파일 덮어쓰기', f'{path}\n기존 파일을 교체할까요?'):
                    return False
                chosen.append((path, fingerprint))
            if not self.keep_all():
                return False
            project_doc = ProjectList()
            person_doc = PeopleDoc(year, [])
            plan_doc = PlanDoc(year, [])
            texts = [dump_projects(project_doc), dump_plan(plan_doc) if self.planning else dump_people(person_doc)]
            prepared = [(path, encode_yaml(text, 'utf-8')) for (path, _), text in zip(chosen, texts)]
            self.set_save_status('저장 중')
            write_bundle(prepared, self.backed_up, expected={path: fingerprint for path, fingerprint in chosen})
        except (OSError, UnicodeError, ValueError, TypeError, DocumentError) as exc:
            self.save_failure_status(exc)
            self.warn('새 문서 생성 실패', str(exc))
            return False
        self.history_invalidated.emit()
        self._autosave = None
        self._binding_deferred_projects = False
        self._binding_deferred_years.clear()
        self._unsupported_sources.clear()
        self.project_editor.set_projects(project_doc)
        self.people_docs = {year: person_doc}
        self.people_dirty = {year: False}
        self.people_sources = {year: [] if self.planning else [str(chosen[1][0])]}
        self.plan_docs = {year: plan_doc}
        self.plan_dirty = {year: False}
        self.plan_sources = {year: str(chosen[1][0]) if self.planning else ''}
        self.projects_sources = [str(chosen[0][0])]
        self.projects_dirty = False
        self.loaded_files = [str(path) for path, _ in chosen]
        self.file_fingerprints = {str(path): hashlib.sha256(data).digest() for path, data in prepared}
        self.file_encodings = {str(path): ('utf-8', '새 문서') for path, _ in chosen}
        self.document_encodings = {'projects': {'utf-8'}, kind + ':' + year: {'utf-8'}}
        self._source_metadata = {str(path): None for path, _ in chosen}
        self.current_year = None
        self.select_year(year)
        if hasattr(self, 'yaml_panel'):
            self.yaml_panel.sync_from_workspace(preserve_dirty=False)
        self.settings.setValue('loaded_files', self.loaded_files)
        self._bind_autosave()
        self.show_page(self.INPUT_PAGE)
        self.refresh_marks()
        self.update_save_status(reset=True)
        self.statusBar().showMessage('새 문서 생성 완료')
        return True

    def prepare_copy(self):
        """마지막 편집을 확정한다. 원본 위치 취소는 사본 보존을 막지 않는다."""
        focus = QApplication.focusWidget()
        if focus is not None and self.isAncestorOf(focus):
            focus.clearFocus()
        if self._autosave_pending:
            self.flush_autosave()

    def ask_copy_kind(self):
        choices = ['작업자료 묶음', '사업 파일 사본', '현재 연도 파일 사본']
        if self.planning:
            choices.append('실적용 확정 묶음')
        choice, ok = QInputDialog.getItem(self, '내보내기', '내보낼 자료:', choices, 0, False)
        return choice if ok else ''

    def ask_copy_file(self, title):
        path, _ = QFileDialog.getSaveFileName(self, title, self.start_folder('export') or '.', YAML_FILTER)
        if not path:
            return '', 'preserve'
        options = ['원본 인코딩 유지'] + list(ENCODINGS.values())
        choice, ok = QInputDialog.getItem(self, '사본 인코딩', '저장 인코딩:', options, 0, False)
        if not ok:
            return '', 'preserve'
        return path, ('preserve' if choice == options[0] else next(code for code, label in ENCODINGS.items() if label == choice))

    def browse_export(self):
        self.prepare_copy()
        choice = self.ask_copy_kind()
        if choice == '작업자료 묶음':
            return self.browse_work_copy()
        if choice == '사업 파일 사본':
            path, code = self.ask_copy_file('사업 파일 사본')
            return bool(path) and self.export_one(path, 'projects', code, True)
        if choice == '현재 연도 파일 사본':
            path, code = self.ask_copy_file('현재 연도 파일 사본')
            return bool(path) and self.export_one(path, 'current', code, True)
        if choice == '실적용 확정 묶음':
            return self.browse_confirm_plan()
        return False

    def browse_work_copy(self):
        self.prepare_copy()
        self._save_encoding = 'preserve'
        folder = self.ask_export_folder()
        return bool(folder) and self.export_all(folder, encoding=self._save_encoding, interactive=True)

    def browse_save_plan(self):
        return self.browse_work_copy()

    def save_plan(self, folder, encoding='preserve', interactive=False):
        """호환 호출: 계획 작업자료 사본이며 원본을 변경하지 않는다."""
        return self.export_all(folder, encoding, interactive)

    def _copy_sources(self, key):
        if key == 'projects':
            return self.projects_sources
        kind, year = key.split(':', 1)
        if kind == 'plan':
            return [self.plan_sources[year]] if self.plan_sources.get(year) else []
        return self.people_sources.get(year, [])

    def _write_copy_jobs(self, jobs, encoding='preserve', interactive=False):
        """사본의 경로·변환·지문을 검증한 뒤 원본 메타데이터 변경 없이 교체한다."""
        protected = {Path(p).resolve() for p in self.loaded_files}
        protected.update(Path(p).resolve() for p in self.projects_sources)
        protected.update(Path(p).resolve() for sources in self.people_sources.values() for p in sources)
        protected.update(Path(p).resolve() for p in self.plan_sources.values() if p)
        protected.update(Path(p).resolve() for p in getattr(self, 'protected_paths', lambda: [])())
        prepared, expected = [], {}
        previous_status = None
        try:
            for raw_path, text, key in jobs:
                path = Path(raw_path).resolve()
                if path in protected:
                    raise ValueError(f'열린 원본에는 사본을 쓸 수 없습니다: {path}')
                if path in expected:
                    raise ValueError(f'중복된 사본 경로: {path}')
                unsafe = {Path(p).resolve() for p in self._copy_sources(key)} & self._unsupported_sources
                if unsafe:
                    raise ValueError('지원하지 않는 원본 항목이 있어 사본을 만들 수 없습니다: ' + ', '.join(map(str, unsafe)))
                fingerprint = hashlib.sha256(path.read_bytes()).digest() if path.exists() else None
                if fingerprint is not None and not self.confirm('기존 사본 덮어쓰기', f'{path}\n기존 파일을 교체할까요?'):
                    return False
                code = self.choose_output_encoding(path, key, interactive) if encoding == 'preserve' else encoding
                if code is None:
                    return False
                try:
                    data = encode_yaml(text, code)
                except UnicodeEncodeError:
                    if not interactive or not self.confirm('UTF-8로 사본을 저장할까요?', f'{path}\nUTF-8로 사본을 저장할까요?'):
                        return False
                    data = encode_yaml(text, 'utf-8')
                expected[path] = fingerprint
                prepared.append((path, data))
            previous_status = (self.save_status_label.text(), self.save_status_label.toolTip())
            self.set_save_status('저장 중')
            write_bundle(prepared, self._copy_backed_up, expected=expected)
        except (OSError, UnicodeError, ValueError, TypeError, DocumentError) as exc:
            self.warn('사본 내보내기 실패', str(exc))
            return False
        finally:
            if previous_status is not None:
                self.save_status_label.setText(previous_status[0])
                self.save_status_label.setToolTip(previous_status[1])
        self.statusBar().showMessage('작업자료 사본 내보내기 완료')
        return True

    def export_one(self, path, kind, encoding='preserve', interactive=False):
        self.prepare_copy()
        if kind == 'projects':
            return self._write_copy_jobs([(path, dump_projects(self.project_editor.projects), 'projects')], encoding, interactive)
        year = self.current_year
        doc = self.plan_docs[year] if self.planning else self.people_docs[year]
        dump = dump_plan if self.planning else dump_people
        return self._write_copy_jobs([(path, dump(doc), ('plan:' if self.planning else 'people:') + year)], encoding, interactive)

    def browse_confirm_plan(self):
        self._save_encoding = 'preserve'
        folder = self.ask_export_folder()
        return bool(folder) and self.confirm_plan(folder, encoding=self._save_encoding, interactive=True)

    def confirm_plan(self, folder, encoding='preserve', interactive=False):
        """현재 초안을 월별 엔진으로 검증한 뒤 확정 파일을 따로 만든다."""
        self.prepare_copy()
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
        path = Path(folder) / 'people' / f'people_{self.current_year}.yaml'
        project_path = Path(folder) / 'projects.yaml'
        protected = {Path(p).resolve() for p in self.projects_sources + list(self.plan_sources.values()) if p}
        protected.update(Path(p).resolve() for sources in self.people_sources.values() for p in sources)
        protected.update(Path(p).resolve() for p in getattr(self, 'protected_paths', lambda: [])())
        if project_path.resolve() in protected or path.resolve() in protected:
            self.warn('별도 폴더 필요', '계획 작업 자료·불러온 원본과 다른 폴더로 내보내세요.')
            return False
        if not self._write_copy_jobs([
                (project_path, dump_projects(self.project_editor.projects), 'projects'),
                (path, dump_people(doc), 'plan:' + self.current_year)],
                encoding, interactive):
            return False
        self.statusBar().showMessage(f'확정 파일 생성: {path}')
        return True

    def export_all(self, folder, encoding='preserve', interactive=False):
        """현재 작업자료 묶음을 사본으로 쓴다. 원본과 dirty는 변경하지 않는다."""
        self.prepare_copy()
        target = Path(folder)
        jobs = [(target / 'projects.yaml', dump_projects(self.project_editor.projects), 'projects')]
        docs, dump, prefix = ((self.plan_docs, dump_plan, 'plan') if self.planning
                              else (self.people_docs, dump_people, 'people'))
        for year in sorted(docs):
            doc = docs[year]
            output_year = doc.year if valid_year(doc.year) else year
            folder_name = 'plans' if self.planning else 'people'
            jobs.append((target / folder_name / f'{prefix}_{output_year}.yaml', dump(doc), prefix + ':' + year))
        return self._write_copy_jobs(jobs, encoding, interactive)

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

    def source_labels(self, year=None):
        """계산에 쓴 파일 표시 이름. 합친 파일은 쉼표로 잇는다."""
        target_year = self.current_year if year is None else year
        projects = ', '.join(sorted(Path(p).name for p in self.projects_sources)) or '(새 사업 입력)'
        sources = self.people_sources.get(target_year, [])
        people = ', '.join(sorted(Path(p).name for p in sources)) or f'(새 {target_year} 입력)'
        return projects, people

    def closeEvent(self, event):
        if self._autosave_blocked:
            if self.confirm('복구 필요', '파일 복구에 실패했습니다. 안내된 복구 파일을 보존한 뒤 종료할까요?'):
                event.accept()
            else:
                event.ignore()
            return
        if self.keep_all():
            event.accept()
        else:
            event.ignore()

    # --- 계산 결과 ---

    def clear_results(self):
        self._shown_person = None
        for view in (self.people_summary_view, self.project_summary_view, self.person_view, self.project_people_view):
            view.show_table([], [])
        self.project_detail_label.clear()
        self.person_list.clear()
        self.project_list.clear()

    def _result_person_entry(self, name):
        """현재 계산 결과 연도의 사람 문서와 해당 PersonEntry를 돌려준다."""
        if self.result is None or not name:
            return None, None, None
        year = str(self.result.inputs.year)
        doc = self.people_docs.get(year)
        if doc is None:
            return None, None, None
        entry = next((item for item in doc.people if item.name == name), None)
        return (year, doc, entry) if entry is not None else (None, None, None)

    def project_pin_info(self, name, alias):
        """개인상세 사업 행의 압정 상태와 안내 문구: ('fixed' | 'unfixed' | 'none', tooltip)."""
        _, _, entry = self._result_person_entry(name)
        if entry is None or alias not in self.result.allocations.get(name, {}):
            return 'none', ''
        if alias in entry.fixed:
            return 'fixed', f'{alias}: 월별 고정됨 (클릭하여 고정 해제)'
        return 'unfixed', f'{alias}: 미고정 (클릭하여 현재 월별 값 고정)'

    def person_pin_info(self, name):
        """개인상세 사람 목록의 압정 상태와 안내 문구: ('fixed' | 'partial' | 'unfixed' | 'none', tooltip)."""
        _, _, entry = self._result_person_entry(name)
        projects = list(self.result.allocations.get(name, ())) if self.result is not None else []
        if entry is None or not projects:
            return 'none', ''
        pinned = sum(1 for alias in projects if alias in entry.fixed)
        total = len(projects)
        if pinned == 0:
            return 'unfixed', f'{name}: 미고정 (0/{total}개 사업 · 클릭하여 전체 고정)'
        if pinned == total:
            return 'fixed', f'{name}: 전체 고정됨 ({total}/{total}개 사업 · 클릭하여 전체 해제)'
        return 'partial', f'{name}: 일부 고정됨 ({pinned}/{total}개 사업 · 클릭하여 전체 고정)'

    def _person_view_project_alias(self, row):
        model = self.person_view.model()
        if (self.result is None or not self._shown_person or model is None
                or not 0 <= row < model.rowCount() - 2):
            return None
        alias = model.rows[row][0]
        return alias if alias in self.result.allocations.get(self._shown_person, {}) else None

    def _person_view_pin_info(self, row):
        alias = self._person_view_project_alias(row)
        return self.project_pin_info(self._shown_person, alias) if alias is not None else ('none', '')

    def _person_view_pin_clicked(self, row):
        alias = self._person_view_project_alias(row)
        if alias is not None:
            self.toggle_project_pin(self._shown_person, alias)

    def toggle_project_pin(self, name, alias):
        """개별 사람·사업의 월별 고정 상태를 전환하고 단일 원본 파일에 실시간 저장한다."""
        if not self.flush_autosave():
            return False
        year, doc, entry = self._result_person_entry(name)
        units = self.result.allocations.get(name, {}).get(alias) if self.result is not None else None
        if entry is None or units is None:
            return False
        old_fixed = deepcopy(entry.fixed)
        unpinning = alias in entry.fixed
        if unpinning:
            entry.fixed.pop(alias, None)
            action = f'{name} · {alias} 월별 고정 해제'
        else:
            entry.fixed[alias] = [amount(u) for u in units]
            action = f'{name} · {alias} 월별 고정'
        return self._finish_pin_change(year, doc, entry, old_fixed, action, recalculate=unpinning)

    def toggle_person_pin(self, name):
        """사람의 전체 사업 월별 고정 상태를 전환한다. 일부 고정 상태는 전체 고정으로 전환한다."""
        if not self.flush_autosave():
            return False
        year, doc, entry = self._result_person_entry(name)
        allocs = self.result.allocations.get(name, {}) if self.result is not None else {}
        if entry is None or not allocs:
            return False
        old_fixed = deepcopy(entry.fixed)
        state, _ = self.person_pin_info(name)
        if state == 'fixed':
            for alias in allocs:
                entry.fixed.pop(alias, None)
            unpinning = True
            action = f'{name} 전체 사업 월별 고정 해제'
        else:
            for alias, units in allocs.items():
                if alias not in entry.fixed:
                    entry.fixed[alias] = [amount(u) for u in units]
            unpinning = False
            action = f'{name} 전체 사업 월별 고정'
        return self._finish_pin_change(year, doc, entry, old_fixed, action, recalculate=unpinning)

    def _finish_pin_change(self, year, doc, entry, old_fixed, action, recalculate=False):
        sources = self.people_sources.get(year, [])
        if self._autosave and year in self._autosave_people:
            self._autosave_pending = True
            self._history_before = self.history_context()
            self._history_action = '개인상세 — 월별 고정'
            try:
                if not self.flush_autosave():
                    return False
            finally:
                self._history_action = None
            save_note = f' ({Path(sources[0]).name} 저장됨)'
        elif len(sources) == 1:
            # 계산이 의존하는 모든 원본의 기준 지문을 확보하지 못하면
            # 사람 파일만 별도로 저장해서 오래된 계산을 확정하지 않는다.
            entry.fixed = old_fixed
            self.warn('원본 저장 차단', '원본의 저장 기준을 확인할 수 없습니다. 파일을 다시 불러오세요.')
            return False
        else:
            self.set_people_dirty(year, True)
            self._autosave_touched_years.add(year)
            if self._save_new_bindings(False, {year}):
                save_note = f' ({Path(self.people_sources[year][0]).name} 저장됨)'
            else:
                save_note = ' (저장 위치 필요 · 변경 내용은 메모리에 보존됩니다.)'

        # 입력 탭의 월별 고정 표와 사람 목록 상태를 즉시 맞춘다.
        if year == self.current_year and self.people_editor.doc is doc:
            try:
                index = doc.people.index(entry)
            except ValueError:
                index = -1
            if index >= 0:
                self.people_editor.mark_person(index)
            if self.people_editor.current() is entry:
                self.people_editor.show_person()

        if recalculate:
            self._refresh_result_in_place(year)
        else:
            self.person_list.viewport().update()
            self.person_view.viewport().update()
        self.statusBar().showMessage(action + save_note)
        return True

    def _refresh_result_in_place(self, year):
        """현재 결과 탭·선택된 사람/사업·표시 순서를 유지한 채 해당 연도 결과를 다시 계산한다."""
        doc = self.people_docs.get(year)
        if doc is None:
            return
        current_tab = self.result_tabs.currentIndex()
        shown_person = self._shown_person
        selected_person = self.person_list.currentItem().text() if self.person_list.currentItem() else None
        selected_project = self.project_list.currentItem().text() if self.project_list.currentItem() else None
        labels = self.source_labels(year)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            inputs, issues = load_inputs(
                *labels, texts={'projects': dump_projects(self.project_editor.projects),
                                'people': dump_people(doc)})
            result = calculate(inputs, issues) if inputs is not None else None
        except Exception as exc:
            self.warn('계산 실패', f'{type(exc).__name__}: {exc}')
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.issues = result.issues if result is not None else issues
        self.generated_at, self.generated_from = datetime.now(), labels
        self.issue_view.show_table(*issue_table(self.issues), colors=severity_colors())
        if result is None or result.has_errors:
            self.result = None
            self.excel_button.setEnabled(False)
            self.clear_results()
            self.show_result_header(' · 오류로 결과를 만들지 않았습니다. 입력 화면에서 고친 뒤 결과 화면으로 돌아오세요.')
            self.result_tabs.setCurrentIndex(self.ISSUES_TAB)
            return

        self.result = result
        self.show_result_header()
        self.people_summary_view.show_table(*people_summary_table(result))
        self.project_summary_view.show_table(*project_summary_table(result))
        project_names = list(result.inputs.projects)
        if [self.project_list.item(i).text() for i in range(self.project_list.count())] != project_names:
            self.project_list.blockSignals(True)
            self.project_list.clear()
            self.project_list.addItems(project_names)
            if selected_project in project_names:
                self.project_list.setCurrentRow(project_names.index(selected_project))
            elif project_names:
                self.project_list.setCurrentRow(0)
            self.project_list.blockSignals(False)
        active_project = (selected_project if selected_project in result.inputs.projects
                          else (project_names[0] if project_names else ''))
        self.show_project_people(active_project)

        person_names = list(result.inputs.people)
        if [self.person_list.item(i).text() for i in range(self.person_list.count())] != person_names:
            self.person_list.blockSignals(True)
            self.person_list.clear()
            self.person_list.addItems(person_names)
            if selected_person in person_names:
                self.person_list.setCurrentRow(person_names.index(selected_person))
            elif person_names:
                self.person_list.setCurrentRow(0)
            self.person_list.blockSignals(False)
        active_person = (shown_person if shown_person in result.inputs.people
                         else (selected_person if selected_person in result.inputs.people
                               else (person_names[0] if person_names else '')))
        self.show_person(active_person)
        self.person_list.viewport().update()
        self.excel_button.setEnabled(True)
        self.result_tabs.setCurrentIndex(current_tab)

    def generate(self):
        """입력 화면의 현재 내용(내보내기 전이어도)을 파일과 같은 규칙으로 검증·계산해 결과 화면에 보여 준다."""
        if not self.flush_autosave():
            return
        if not self.flush_year_edit():
            self.show_page(self.INPUT_PAGE)
            return
        labels = self.source_labels()
        self.result, self.issues, self.generated_at = None, [], None
        self.excel_button.setEnabled(False)
        self.clear_results()
        self.issue_view.show_table([], [])
        self.pages.blockSignals(True)
        self.pages.setCurrentIndex(self.RESULT_PAGE)
        self.pages.blockSignals(False)
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
            self.project_people_view.show_table(*project_people_table(self.result, alias), bold_label='합계',
                                                order_context=('project', alias), footer_rows=1)
        else:
            self.project_detail_label.clear()
            self.project_people_view.show_table([], [])

    def show_person(self, name):
        if self.result is not None and name in self.result.inputs.people:
            self._shown_person = name
            self.person_view.show_table(*person_detail_table(self.result, name), bold_label='합계',
                                        order_context=('person', name), footer_rows=2)
        else:
            self._shown_person = None
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
        if self.result is None or self.result.has_errors:
            self.warn('엑셀 저장 불가', '오류 없는 계산 결과가 필요합니다.')
            return False
        # openpyxl은 엑셀 저장에만 필요하므로 이때 불러온다.
        from .excel import write_workbook
        try:
            write_workbook(self.result, path)
        except OSError as exc:
            self.warn('엑셀 저장 실패', f'{path}\n{exc.strerror or exc}\n파일이 열려 있으면 닫고 다시 저장하세요.')
            return False
        self.statusBar().showMessage(f'엑셀 저장: {path}')
        return True


class _ModeSettings:
    """같은 설정 저장소 안에서도 작업별 파일·연도를 분리한다."""
    def __init__(self, settings, prefix):
        self.settings, self.prefix = settings, prefix + '/'

    def value(self, key, default=None, **options):
        fallback = self.settings.value('font_size', default, **options) if key == 'font_size' else default
        return self.settings.value(self.prefix + key, fallback, **options)

    def setValue(self, key, value):
        self.settings.setValue(self.prefix + key, value)

    def remove(self, key):
        self.settings.remove(self.prefix + key)


class MainWindow(QMainWindow):
    """서로 독립된 사업·사람 문서를 갖는 실적작업·계획작업의 공통 창."""
    ACTUAL, PLANNING = 0, 1

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('prouman', 'prouman')
        self._migrate_settings()
        self.workspaces = [WorkspaceWindow(_ModeSettings(self.settings, 'actual')),
                           WorkspaceWindow(_ModeSettings(self.settings, 'planning'), planning=True)]
        self.history = GlobalHistory()
        for workspace in self.workspaces:
            workspace.history_committed.connect(
                lambda session, label, before, after, workspace=workspace:
                self.history.record(workspace, session, label, (before, after), self.workspaces))
            workspace.history_invalidated.connect(self.clear_history)
        self.actual, self.plan = self.workspaces
        self.mode_stack = QStackedWidget()
        for workspace in self.workspaces:
            workspace.menuBar().hide()
            for action in workspace.findChildren(QAction):
                action.setShortcuts([])
            workspace.setWindowFlags(Qt.Widget)
            workspace.protected_paths = self.open_source_paths
            self.mode_stack.addWidget(workspace)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.mode_stack)
        self.setCentralWidget(central)
        mode_corner = QWidget(self.menuBar())
        corner_layout = QHBoxLayout(mode_corner)
        corner_layout.setContentsMargins(0, 0, 8, 0)
        corner_layout.addWidget(QLabel('작업모드'))
        self.mode_combo = QComboBox(mode_corner)
        self.mode_combo.addItems(('실적작업', '계획작업'))
        self.mode_combo.currentIndexChanged.connect(lambda index: self.set_mode(index))
        corner_layout.addWidget(self.mode_combo)
        self.menuBar().setCornerWidget(mode_corner, Qt.TopRightCorner)
        self.mode_corner = mode_corner  # 코너 위젯의 C++ 수명을 창과 함께 유지한다.
        file_menu = self.menuBar().addMenu('파일')
        for label, key, callback in (
                ('새로 만들기', QKeySequence.New, lambda: self.active_workspace.browse_new()),
                ('불러오기', QKeySequence.Open, lambda: self.active_workspace.browse_import()),
                ('저장 위치 지정', None, lambda: self.active_workspace.browse_bind_sources()),
                ('실적 파일에서 계획 준비', None, self.prepare_current),
                ('내보내기', 'Ctrl+Shift+S', lambda: self.active_workspace.browse_export()),
                ('종료', QKeySequence.Quit, self.close)):
            action = QAction(label, self)
            if key is not None:
                action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda checked=False, callback=callback: callback())
            file_menu.addAction(action)
            if label == '실적 파일에서 계획 준비':
                self.prepare_action = action
        edit_menu = self.menuBar().addMenu('편집')
        self.undo_action = QAction('실행 취소', self)
        self.undo_action.setShortcut(QKeySequence('Ctrl+Z'))
        self.undo_action.triggered.connect(lambda: self.move_history(-1, keyboard=True))
        edit_menu.addAction(self.undo_action)
        self.redo_action = QAction('다시 실행', self)
        self.redo_action.setShortcut(QKeySequence('Ctrl+Y'))
        self.redo_action.triggered.connect(lambda: self.move_history(1, keyboard=True))
        edit_menu.addAction(self.redo_action)
        self.undo_menu = edit_menu.addMenu('실행 취소 이력')
        self.redo_menu = edit_menu.addMenu('다시 실행 이력')
        self.undo_menu.aboutToShow.connect(lambda: self.populate_history_menu(False))
        self.redo_menu.aboutToShow.connect(lambda: self.populate_history_menu(True))
        mode_menu = self.menuBar().addMenu('작업')
        for index, label in enumerate(('실적작업', '계획작업')):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(f'Ctrl+Alt+{index + 1}'))
            action.triggered.connect(lambda checked=False, index=index: self.set_mode(index))
            mode_menu.addAction(action)
        view_menu = self.menuBar().addMenu('보기')
        for label, keys, step in (('글자 크게', ['Ctrl++', 'Ctrl+='], 1),
                                  ('글자 작게', ['Ctrl+-'], -1), ('기본 크기', ['Ctrl+0'], 0)):
            action = QAction(label, self)
            action.setShortcuts([QKeySequence(key) for key in keys])
            action.triggered.connect(lambda checked=False, step=step: self.change_font_size(step))
            view_menu.addAction(action)
        self.resize(1280, 850)
        self.set_mode(self.settings.value('work_mode', self.ACTUAL, type=int))
        theme.apply_window_theme(self)
        QApplication.instance().paletteChanged.connect(self._apply_main_theme)

    def _apply_main_theme(self):
        if getattr(self, '_theme_applying', False):
            return
        self._theme_applying = True
        try:
            theme.apply_window_theme(self)
            for workspace in self.workspaces:
                workspace.apply_theme_styles()
        finally:
            self._theme_applying = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ApplicationPaletteChange and hasattr(self, 'workspaces'):
            self._apply_main_theme()

    @property
    def active_workspace(self):
        return self.workspaces[self.mode_stack.currentIndex()]

    def clear_history(self):
        if self.history.entries:
            self.history.clear()
            self.statusBar().showMessage('원본 문서가 바뀌어 이전 실행 이력을 정리했습니다.')

    def populate_history_menu(self, redo=False):
        menu = self.redo_menu if redo else self.undo_menu
        menu.clear()
        labels = self.history.labels(redo)
        revision = self.history.revision
        for index, label in enumerate(labels[:20], 1):
            action = menu.addAction(f'{index}. {label[:60]}')
            action.triggered.connect(lambda checked=False, n=index, rev=revision:
                                     self.move_history(n if redo else -n, revision=rev))
        if len(labels) > 20:
            action = menu.addAction('더 보기…')
            action.triggered.connect(lambda checked=False, rev=revision:
                                     self.more_history(redo, len(labels), rev))
        if not labels:
            menu.addAction('(이력 없음)').setEnabled(False)

    def more_history(self, redo, total, revision):
        labels = self.history.labels(redo)
        if revision != self.history.revision or len(labels) != total:
            self.statusBar().showMessage('이력이 변경되어 메뉴를 다시 열어야 합니다.')
            return
        choices = [f'{index}. {label[:60]}' for index, label in enumerate(labels, 1)]
        choice, ok = QInputDialog.getItem(self, '이력 이동', '이동할 마지막 작업:', choices, 20, False)
        if ok:
            count = int(choice.split('.', 1)[0])
            self.move_history(count if redo else -count, revision=revision)

    def move_history(self, delta, *, keyboard=False, revision=None):
        focus = QApplication.focusWidget()
        if keyboard and isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return False
        if focus is not None and self.isAncestorOf(focus):
            focus.clearFocus()
        for workspace in self.workspaces:
            if not workspace.confirm_leave_yaml() or not workspace.flush_autosave():
                return False
        if revision is not None and revision != self.history.revision:
            self.statusBar().showMessage('이력이 변경되어 메뉴를 다시 열어야 합니다.')
            return False
        target = self.history.cursor + delta
        if not delta or not 0 <= target <= len(self.history.entries):
            return False
        group = (self.history.entries[target:self.history.cursor] if delta < 0
                 else self.history.entries[self.history.cursor:target])
        affected = {entry.workspace for entry in group}
        for workspace in affected:
            workspace.set_save_status('저장 중')
        try:
            entry = self.history.move(delta)
        except (OSError, UnicodeError, ValueError, TypeError, DocumentError) as exc:
            if '원본 복구 실패' in str(exc):
                for workspace in affected:
                    workspace.block_history_writes()
            for workspace in affected:
                workspace.save_failure_status(exc)
            self.active_workspace.warn('이력 저장 실패', str(exc))
            return False
        for workspace in affected:
            workspace.restore_history_state()
        context = entry.context[0 if delta < 0 else 1]
        self.set_mode(self.workspaces.index(entry.workspace))
        entry.workspace.restore_history_state(context)
        self.statusBar().showMessage(f'{abs(delta)}개 작업 이력 이동 완료')
        return True

    def set_mode(self, mode):
        mode = mode if mode in (self.ACTUAL, self.PLANNING) else self.ACTUAL
        current = self.mode_stack.currentIndex()
        if current != mode and current >= 0:
            workspace = self.workspaces[current]
            focus = QApplication.focusWidget()
            if focus is not None and workspace.isAncestorOf(focus):
                focus.clearFocus()
            if not workspace.confirm_leave_yaml() or not workspace.flush_autosave():
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(current)
                self.mode_combo.blockSignals(False)
                return False
        self.mode_stack.setCurrentIndex(mode)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(mode)
        self.mode_combo.blockSignals(False)
        self.prepare_action.setEnabled(mode == self.PLANNING)
        self.setWindowTitle('인력 배분 — ' + ('계획작업' if mode else '실적작업'))
        self.settings.setValue('work_mode', mode)
        return True

    def prepare_current(self):
        workspace = self.active_workspace
        if not workspace.planning:
            return
        workspace.browse_prepare_plan()

    def change_font_size(self, step):
        size = self.active_workspace.font_size + step if step else DEFAULT_FONT_SIZE
        for workspace in self.workspaces:
            workspace.set_font_size(size)

    def open_source_paths(self):
        return [path for workspace in self.workspaces
                for path in workspace.projects_sources + list(workspace.plan_sources.values())
                + [p for sources in workspace.people_sources.values() for p in sources] if path]

    def _migrate_settings(self):
        if self.settings.value('actual/loaded_files', None) is not None:
            return
        files = self.settings.value('loaded_files', None)
        if files is None:
            files = [path for kind in KINDS
                     if (path := self.settings.value(f'{kind}_file', '', type=str)) and Path(path).is_file()]
        kinds = {}
        for path in files or []:
            try:
                kinds[path] = detect_kind(decode_text(Path(path).read_bytes()))
            except (OSError, UnicodeError, DocumentError):
                continue
        has_plan = 'plan' in kinds.values()
        self.settings.setValue('actual/loaded_files', [p for p, kind in kinds.items() if kind in ('projects', 'people')])
        self.settings.setValue('planning/loaded_files', [p for p, kind in kinds.items()
                                                        if has_plan and kind in ('projects', 'plan')])
        wanted = self.settings.value('current_year', '', type=str)
        for prefix in ('actual', 'planning'):
            self.settings.setValue(prefix + '/current_year', wanted)

    def closeEvent(self, event):
        original = self.mode_stack.currentIndex()
        for index, workspace in enumerate(self.workspaces):
            if not self.set_mode(index):
                event.ignore()
                return
            if workspace._autosave_blocked:
                if not workspace.confirm('복구 필요', '파일 복구에 실패했습니다. 안내된 복구 파일을 보존한 뒤 종료할까요?'):
                    event.ignore()
                    return
                continue
            if not workspace.keep_all('계획작업' if workspace.planning else '실적작업'):
                event.ignore()
                return
        self.set_mode(original)
        event.accept()


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
