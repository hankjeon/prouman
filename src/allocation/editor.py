"""YAML을 몰라도 사업·사람 입력을 표로 편집하는 위젯."""
from bisect import bisect_left
from datetime import date

from PySide6.QtCore import QDate, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QKeySequence, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollBar, QSizePolicy, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
                               QStyleFactory, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .document import (QUANTITY_HINT, AbsenceEntry, PeopleDoc, PersonEntry, ProjectEntry, YearEntry,
                       absence_problems, person_totals, project_problems, quantity_units, to_date, valid_year,
                       year_problems)
from .models import amount
from .table_order import TableOrderMixin, selection_text

TRANSPARENT = QColor(0, 0, 0, 0)
BASE_ROLE, NOTE_ROLE = Qt.UserRole + 1, Qt.UserRole + 2  # 칸의 기본 배경색과 안내(오류가 없을 때 보임)


class SelectionDelegate(QStyledItemDelegate):
    """선택 셀을 직접 칠한다.

    Windows 11 기본 스타일은 선택 셀을 거의 보이지 않는 회색으로, 현재 셀은 흰 바탕으로 그리고
    스타일시트로도 포커스 상태에 따라 일관되게 바뀌지 않는다. 포커스와 관계없이 선택 범위는 같은 색,
    현재 셀은 테두리로 표시한다. 선택 배경·글자는 시스템 팔레트(강조색)에서 가져와 다크 테마에서도
    보인다. 표의 row_band가 켜져 있으면 현재 행을 옅은 띠로 칠한다. 칸 자체의 배경색은 띠 위에
    그려지므로 띠보다 우선한다.
    """

    selected = False

    def initStyleOption(self, option, index):
        # 칸의 배경색(오류·제외 월 등)보다 선택 색을 우선해야 색이 있는 칸도 선택이 보인다.
        super().initStyleOption(option, index)
        if self.selected:
            option.backgroundBrush = QBrush(theme.selection_color())

    def paint(self, painter, option, index):
        state = option.state
        plain = QStyleOptionViewItem(option)
        plain.state = state & ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        view = self.parent()
        self.selected = bool(state & QStyle.StateFlag.State_Selected)
        if self.selected:
            painter.fillRect(option.rect, theme.selection_color())
            # 선택 상태 표식을 지웠으므로 글자도 강조 글자색으로 직접 맞춘다.
            plain.palette.setColor(QPalette.Text, theme.selected_text_color())
        elif getattr(view, 'row_band', False) and index.row() == view.currentRow():
            painter.fillRect(option.rect, theme.current_row_color())
        super().paint(painter, plain, index)
        self.selected = False
        if state & QStyle.StateFlag.State_HasFocus:
            painter.save()
            painter.setPen(theme.current_cell_color())
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()


class DateDelegate(SelectionDelegate):
    """달력에서 날짜를 고르거나 직접 입력한다. 저장 형식은 YYYY-MM-DD다."""

    def createEditor(self, parent, option, index):
        editor = QDateEdit(parent)
        editor.setCalendarPopup(True)
        editor.setDisplayFormat('yyyy-MM-dd')
        return editor

    def setEditorData(self, editor, index):
        day = to_date(index.data() or '')
        editor.setDate(QDate(day.year, day.month, day.day) if day else QDate.currentDate())

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date().toString('yyyy-MM-dd'))


class EditTable(TableOrderMixin, QTableWidget):
    """편집 가능한 표. Ctrl+C로 복사, Ctrl+V로 엑셀 범위 붙여넣기, Delete로 선택 칸 지우기를 지원한다."""

    def __init__(self, headers=(), stretch_last=False, row_band=False):
        super().__init__(0, len(headers))
        self.row_band = row_band
        if row_band:
            self.currentCellChanged.connect(lambda *args: self.viewport().update())
        self.setHorizontalHeaderLabels(list(headers))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                             | QAbstractItemView.AnyKeyPressed)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        # 자유 입력 칸(사유·제외 월)만 남는 너비를 채운다. 숫자·날짜 칸이 늘어나면 읽기 어렵다.
        self.horizontalHeader().setStretchLastSection(stretch_last)
        self.date_columns = ()
        self.setItemDelegate(SelectionDelegate(self))
        self.fit_rows()
        self.init_table_order()

    def set_date_columns(self, *columns):
        self.date_columns = columns
        for column in columns:
            self.setItemDelegateForColumn(column, DateDelegate(self))

    def fit_rows(self):
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 10)

    def resizeColumnsToContents(self):
        # 빈 표에서도 날짜 칸은 날짜가 다 보이는 너비를 둔다.
        super().resizeColumnsToContents()
        width = self.fontMetrics().horizontalAdvance('0000-00-00') + 24
        for column in self.date_columns:
            self.setColumnWidth(column, max(self.columnWidth(column), width))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self.fit_rows()
            self.resizeColumnsToContents()

    def set_cell(self, row, column, text, editable=True, problem='', computed=False, base=None, note=''):
        """base는 오류가 없을 때의 배경색, note는 오류가 없을 때도 보이는 툴팁이다."""
        item = QTableWidgetItem(text)
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        item.setFlags(flags | Qt.ItemIsEditable if editable else flags)
        if computed:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            base = base or theme.computed_color()
        item.setData(BASE_ROLE, base)
        item.setData(NOTE_ROLE, note)
        self.setItem(row, column, item)
        self.mark(row, column, problem)
        return item

    def mark(self, row, column, problem):
        item = self.item(row, column)
        if item is None:
            return
        base = item.data(BASE_ROLE)
        item.setBackground(theme.problem_color() if problem else base if base is not None else TRANSPARENT)
        item.setToolTip('\n'.join(text for text in (problem, item.data(NOTE_ROLE) or '') if text))

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
        elif event.matches(QKeySequence.Paste):
            self.paste(QGuiApplication.clipboard().text())
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state() != QAbstractItemView.EditingState:
            for item in self.selectedItems():
                if item.flags() & Qt.ItemIsEditable:
                    item.setText('')
        else:
            super().keyPressEvent(event)

    def copy_selection(self):
        text = selection_text(self, lambda row, column: self.item(row, column).text()
                              if self.item(row, column) is not None else '')
        if self.selectedIndexes():
            QGuiApplication.clipboard().setText(text)
        return text

    def paste(self, text):
        """현재 셀부터 탭·줄 구분 값을 채운다. 표 밖이나 계산 칸은 건너뛴다."""
        start = self.currentIndex()
        if not start.isValid() or not text:
            return
        for r, line in enumerate(text.rstrip('\r\n').split('\n')):
            for c, value in enumerate(line.rstrip('\r').split('\t')):
                item = self.item(start.row() + r, start.column() + c)
                if item is not None and item.flags() & Qt.ItemIsEditable:
                    item.setText(value.strip())


class OrderedPeopleList(QListWidget):
    """이름 항목 하나를 잡아 목록 안에서 순서를 옮긴다."""

    order_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._drag_item = None
        self._drag_start = None
        self._moved = False

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._drag_item = self.itemAt(event.position().toPoint()) if event.button() == Qt.LeftButton else None
        self._drag_start = event.position().toPoint()
        self._moved = False

    def mouseMoveEvent(self, event):
        if self._drag_item is not None and event.buttons() & Qt.LeftButton:
            position = event.position().toPoint()
            if not self._moved and (position - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            target = self.itemAt(position)
            if target is not None and target is not self._drag_item:
                target_row = self.row(target)
                item = self.takeItem(self.row(self._drag_item))
                self.insertItem(target_row, item)
                self.setCurrentItem(item)
                self._moved = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        moved = self._moved
        self._drag_item = None
        self._drag_start = None
        self._moved = False
        if moved:
            event.accept()
            self.order_changed.emit()
        else:
            super().mouseReleaseEvent(event)


def button_row(*buttons):
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    row.addStretch()
    return row


def titled(title, *parts):
    """제목(문자열 또는 바뀌는 제목용 QLabel) 아래에 위젯·레이아웃을 쌓는다."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(title if isinstance(title, QLabel) else QLabel(title))
    for part in parts:
        if isinstance(part, QWidget):
            layout.addWidget(part)
        else:
            layout.addLayout(part)
    return box


def legend(color, text):
    """범례 칩. 배경은 의미 색, 글자는 칩 밝기에 맞춰 정한다(다크에서도 보여야 한다)."""
    label = QLabel(f'  {text}  ')
    label.setStyleSheet(theme.legend_stylesheet(color))
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return label


def theme_color(key):
    """범례 이름 → 현재 테마의 의미 색."""
    return {'outside': theme.outside_color(), 'excluded': theme.excluded_color(),
            'problem': theme.problem_color()}[key]


class ProjectEditor(QWidget):
    """좌측 사업 목록(사업명·약칭·기간·총 배정)과 우측 사업 × 연도 배정인력 표.

    두 표는 행 순서·행 높이·세로 스크롤·현재 행이 맞물린다. 우측은 연속한 6개 연도와, 현재 칸의
    연도를 따르는 맨 오른쪽 `YYYY 제외월` 열로 이루어진다. 아래 가로 스크롤바로 연도 창을 옮긴다.
    """

    changed = Signal()
    alias_renamed = Signal(str, str)
    alias_removed = Signal(str)
    NAME, ALIAS, START, END, TOTAL = range(5)
    YEARS = 6
    CENTER = YEARS // 2  # 짝수 열에서는 가운데 두 열 중 오른쪽에 관리 연도를 둔다.
    FIRST_YEAR, LAST_YEAR = 2020, 2050
    EXCLUDED_WIDE = 1.4  # 제외월 열 너비(연도 열의 1.4배). 연도 열은 등간격이다.

    def __init__(self):
        super().__init__()
        self.projects = []
        self.managed_year = lambda: str(date.today().year)  # 관리 연도. 메인 창이 사람 입력과 연결한다.
        self.allow_remove = lambda project: True
        self.year_start = self.base_year()
        self.excluded_year = self.managed_year_number()
        self.table = EditTable(['사업명', '약칭', '시작일', '종료일', '총 배정'], row_band=True)
        self.table.row_order_committed = self.reorder_project_rows
        self.table.set_date_columns(self.START, self.END)
        self.table.horizontalHeader().setSectionResizeMode(self.NAME, QHeaderView.Stretch)
        self.grid = EditTable([''] * (self.YEARS + 1), row_band=True)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.horizontalHeader().setSectionResizeMode(self.YEARS, QHeaderView.Fixed)
        self.grid.viewport().installEventFilter(self)
        self.grid.setToolTip('칸에는 배정인력, 맨 오른쪽 칸에는 선택한 연도의 제외 월을 쉼표로 구분해 입력합니다 '
                             '(예: 3,4,5).')
        # 두 표의 보이는 높이를 같게 두어 줄이 맞게 한다. 세로 스크롤바는 우측에만 둔다.
        for view in (self.table, self.grid):
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.verticalScrollBar().valueChanged.connect(self.grid.verticalScrollBar().setValue)
        self.grid.verticalScrollBar().valueChanged.connect(self.table.verticalScrollBar().setValue)
        self.table.currentCellChanged.connect(lambda row, *args: self.sync_rows(self.grid, row))
        self.grid.currentCellChanged.connect(self.grid_current_changed)
        self.table.itemChanged.connect(self.project_edited)
        self.grid.itemChanged.connect(self.grid_edited)

        self.range_label = QLabel()
        self.notice = QLabel()
        self.notice.setStyleSheet(theme.error_stylesheet())
        title = QHBoxLayout()
        title.addWidget(QLabel('사업 목록'))
        title.addStretch()
        title.addWidget(self.notice)
        title.addWidget(QLabel('연도별 배정인력'))
        title.addWidget(self.range_label)
        self.year_scroll = QScrollBar(Qt.Horizontal)
        # Windows 스타일의 화살표는 마우스를 올려야 나타난다. 이 스크롤바에만
        # Qt 기본 Fusion 스타일을 적용해 양끝 화살표를 항상 그린다.
        self.year_scroll_style = QStyleFactory.create('Fusion')
        self.year_scroll.setStyle(self.year_scroll_style)
        self.year_scroll.setSingleStep(1)
        self.year_scroll.setPageStep(self.YEARS)
        self.year_scroll.valueChanged.connect(self.set_year_start)
        self.year_scroll.installEventFilter(self)
        # 좌우 표 높이를 같게 두어야 세로 스크롤 값 1:1 맞물림이 어긋나지 않는다.
        self.scroll_filler = QWidget()
        self.scroll_filler.setFixedHeight(self.year_scroll.sizeHint().height())

        self.add_button, self.remove_button = QPushButton('사업 추가'), QPushButton('사업 삭제')
        self.add_button.clicked.connect(self.add_project)
        self.remove_button.clicked.connect(self.remove_project)
        bottom = button_row(self.add_button, self.remove_button)
        self.legends = []  # (칩, 의미색 이름, 문구). 시스템 테마가 바뀌면 다시 칠한다.
        for key, text in (('outside', '사업 기간 밖 연도'), ('excluded', '제외 월 있음'),
                          ('problem', '입력 확인 필요')):
            chip = legend(theme_color(key), text)
            self.legends.append((chip, key, text))
            bottom.addWidget(chip)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.table, 1)
        left_layout.addWidget(self.scroll_filler)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.grid, 1)
        right_layout.addWidget(self.year_scroll)
        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.setSizes([620, 620])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(title)
        layout.addWidget(self.split, 1)
        layout.addLayout(bottom)
        self.refresh()

    # --- 연도 범위 ---

    def managed_year_number(self):
        year = self.managed_year()
        return int(year) if valid_year(year) else date.today().year

    def clamp_year_start(self, start):
        return max(self.FIRST_YEAR, min(start, self.LAST_YEAR - self.YEARS + 1))

    def base_year(self):
        """배정 연도를 최대한 담고, 그 안에서 관리 연도를 중앙에 가장 가깝게 둔다."""
        managed = self.managed_year_number()
        years = sorted({int(entry.year) for project in self.projects for entry in project.years
                        if valid_year(entry.year) and self.FIRST_YEAR <= int(entry.year) <= self.LAST_YEAR})
        if not years:
            return self.clamp_year_start(managed - self.CENTER)
        return max(range(max(self.FIRST_YEAR, years[0] - self.YEARS + 1),
                         min(self.LAST_YEAR - self.YEARS + 1, years[-1]) + 1),
                   key=lambda start: (bisect_left(years, start + self.YEARS) - bisect_left(years, start),
                                      -abs(2 * (managed - start) - (self.YEARS - 1)),
                                      -start))

    def selected_year_start(self):
        start = self.base_year()
        managed = self.managed_year_number()
        if self.FIRST_YEAR <= managed <= self.LAST_YEAR and not start <= managed < start + self.YEARS:
            return self.clamp_year_start(managed - self.CENTER)
        return start

    def years_shown(self):
        return [self.year_start + offset for offset in range(self.YEARS)]

    def reset_years(self):
        """입력 연도를 반영한 처음 범위로 돌리고 제외월 열은 관리 연도로 둔다."""
        self.year_start = self.selected_year_start()
        self.excluded_year = self.managed_year_number()
        if self.excluded_year not in self.years_shown():
            self.excluded_year = self.year_start + self.CENTER
        self.refresh_grid()

    def show_selected_year(self):
        """콤보에서 현재 연도를 다시 골라도 해당 연도와 제외월 열을 보여 준다."""
        managed = self.managed_year_number()
        if not self.FIRST_YEAR <= managed <= self.LAST_YEAR:
            return
        start = self.year_start if managed in self.years_shown() else self.clamp_year_start(managed - self.CENTER)
        if start != self.year_start or self.excluded_year != managed:
            self.year_start = start
            self.excluded_year = managed
            self.refresh_grid()

    def set_year_start(self, start):
        start = self.clamp_year_start(start)
        step = 1 if start >= self.year_start else -1
        self.year_start = start
        shown = self.years_shown()
        if self.excluded_year not in shown:
            self.excluded_year = shown[0] if step > 0 else shown[-1]
        self.refresh_grid()

    def shift_years(self, step):
        self.set_year_start(self.year_start + step)

    def update_year_scroll(self):
        """6년 창의 처음 연도를 2020~2045로 제한한다."""
        bar = self.year_scroll
        bar.blockSignals(True)
        bar.setRange(self.FIRST_YEAR, self.LAST_YEAR - self.YEARS + 1)
        bar.setValue(self.year_start)
        bar.blockSignals(False)

    # --- 데이터 ---

    def set_projects(self, projects):
        self.projects = projects
        self.year_start = self.selected_year_start()
        self.excluded_year = self.managed_year_number()
        if self.excluded_year not in self.years_shown():
            self.excluded_year = self.year_start + self.CENTER
        self.refresh()
        if projects:
            self.table.setCurrentCell(0, 0)

    def reorder_projects(self, aliases):
        if len(aliases) != len(self.projects):
            return
        current = [project.alias for project in self.projects]
        if aliases == current:
            return
        lookup = {project.alias: project for project in self.projects}
        if len(lookup) != len(aliases) or set(lookup) != set(aliases):
            return
        self.reorder_project_rows([self.projects.index(lookup[alias]) for alias in aliases])

    def reorder_project_rows(self, rows):
        if rows == list(range(len(self.projects))) or sorted(rows) != list(range(len(self.projects))):
            return
        selected = self.current()
        self.projects[:] = [self.projects[row] for row in rows]
        self.refresh()
        if selected is not None:
            self.table.setCurrentCell(self.projects.index(selected), self.NAME)
        self.changed.emit()

    def current(self):
        row = self.table.currentRow()
        return self.projects[row] if 0 <= row < len(self.projects) else None

    @staticmethod
    def entry(project, year):
        return next((e for e in project.years if e.year.strip() == str(year)), None)

    # --- 좌측 사업 목록 ---

    def refresh(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.projects))
        problems = project_problems(self.projects)
        for row, project in enumerate(self.projects):
            for column, text in enumerate((project.name, project.alias, project.start, project.end)):
                self.table.set_cell(row, column, text, problem=problems.get((row, column), ''))
            self.table.set_cell(row, self.TOTAL, '', editable=False, computed=True)
            self.update_total(row)
        self.table.blockSignals(False)
        self.table.configure_order(None, rows={row: row for row in range(len(self.projects))})
        self.table.resizeColumnsToContents()
        self.remove_button.setEnabled(bool(self.projects))
        self.refresh_grid()

    def update_total(self, row):
        """모든 연도(보이지 않는 연도 포함)의 배정인력 합계. 형식이 틀린 값이 있으면 '?'."""
        project = self.projects[row]
        bad = [e.year or '(연도 없음)' for e in project.years if quantity_units(e.manpower) is None]
        total = sum(quantity_units(e.manpower) or 0 for e in project.years)
        item = self.table.item(row, self.TOTAL)
        self.table.blockSignals(True)
        item.setText('?' if bad else amount(total))
        item.setData(NOTE_ROLE, f'배정인력 형식을 확인하세요: {", ".join(bad)}' if bad else
                     f'모든 연도의 배정인력 합계 ({len(project.years)}개 연도)')
        self.table.mark(row, self.TOTAL, '')
        self.table.blockSignals(False)

    def revalidate(self):
        problems = project_problems(self.projects)
        for row in range(len(self.projects)):
            for column in range(self.TOTAL):
                self.table.mark(row, column, problems.get((row, column), ''))

    def project_edited(self, item):
        if item.column() >= self.TOTAL:
            return
        project = self.projects[item.row()]
        text = item.text().strip() if item.column() else item.text()
        field = ('name', 'alias', 'start', 'end')[item.column()]
        old = getattr(project, field)
        if old == text:
            return
        setattr(project, field, text)
        self.revalidate()
        # 다른 사업과 겹치는 약칭으로(또는 겹치는 약칭에서) 바꿀 때는 그 사업의 참여율을 옮기지 않는다.
        others = {p.alias for p in self.projects if p is not project}
        if field == 'alias' and old and old not in others and text not in others:
            self.alias_renamed.emit(old, text)
        if field in ('start', 'end'):
            self.refresh_grid()  # 사업 기간 밖 연도 표시가 바뀐다.
        self.changed.emit()

    def add_project(self):
        self.projects.append(ProjectEntry())
        self.refresh()
        self.table.setCurrentCell(len(self.projects) - 1, self.NAME)
        self.table.editItem(self.table.item(len(self.projects) - 1, self.NAME))
        self.changed.emit()

    def remove_project(self):
        project = self.current()
        if project is None or not self.allow_remove(project):
            return
        row = self.table.currentRow()
        self.projects.pop(row)
        self.refresh()
        if self.projects:
            self.table.setCurrentCell(min(row, len(self.projects) - 1), self.NAME)
        if project.alias:
            self.alias_removed.emit(project.alias)
        self.changed.emit()

    # --- 우측 연도 표 ---

    def refresh_grid(self):
        shown = self.years_shown()
        managed = int(self.managed_year()) if valid_year(self.managed_year()) else None
        grid = self.grid
        grid.blockSignals(True)
        grid.setRowCount(len(self.projects))
        grid.setHorizontalHeaderLabels([str(y) for y in shown] + [f'{self.excluded_year} 제외월'])
        for column, year in enumerate(shown + [self.excluded_year]):
            header = grid.horizontalHeaderItem(column)
            font = QFont(grid.font())
            font.setBold(year == managed)
            header.setFont(font)
            header.setToolTip('관리 연도' if year == managed and column < self.YEARS else '')
        hidden = [set(), set()]
        unshown = 0
        for row, project in enumerate(self.projects):
            problems = year_problems(project.years)
            index = {id(e): i for i, e in enumerate(project.years)}
            start, end = to_date(project.start), to_date(project.end)
            for entry in project.years:
                if (index[id(entry)], 0) in problems:
                    unshown += 1
                elif int(entry.year) < shown[0]:
                    hidden[0].add(int(entry.year))
                elif int(entry.year) > shown[-1]:
                    hidden[1].add(int(entry.year))
            for column, year in enumerate(shown):
                entry = self.entry(project, year)
                notes, base = [], None
                if start and end and not start.year <= year <= end.year:
                    notes.append('사업 기간 밖 연도입니다 (계산하면 경고).')
                    base = theme.outside_color()
                if entry and entry.excluded.strip():
                    notes.append(f'제외 월: {entry.excluded}')
                    base = theme.excluded_color()
                problem = problems.get((index[id(entry)], 1), '') if entry else ''
                grid.set_cell(row, column, entry.manpower if entry else '', problem=problem, base=base,
                              note='\n'.join(notes))
            entry = self.entry(project, self.excluded_year)
            if entry is None:
                grid.set_cell(row, self.YEARS, '', editable=False,
                              note=f'먼저 {self.excluded_year} 배정인력을 입력하세요.')
            else:
                grid.set_cell(row, self.YEARS, entry.excluded, problem=problems.get((index[id(entry)], 2), ''))
        grid.blockSignals(False)
        tips = ['2020~2050년 보기 · 양끝 화살표: 1년씩 이동, 빈 곳: 6년씩 이동']
        if hidden[0]:
            tips.append('앞쪽에 배정이 있는 연도: ' + ', '.join(map(str, sorted(hidden[0]))))
        if hidden[1]:
            tips.append('뒤쪽에 배정이 있는 연도: ' + ', '.join(map(str, sorted(hidden[1]))))
        self.year_scroll.setToolTip('\n'.join(tips))
        self.range_label.setText(f'{shown[0]} ~ {shown[-1]}')
        self.notice.setText(f'표시할 수 없는 연도 항목 {unshown}건 (계산하면 오류로 표시)' if unshown else '')
        self.update_year_scroll()
        self.fit_excluded_column()
        self.sync_headers()

    def fit_excluded_column(self):
        """제외월 열 너비를 연도 열의 1.4배로 둔다. 연도 열은 stretch로 등간격을 유지한다."""
        width = self.grid.viewport().width()
        if width > 0:
            share = self.EXCLUDED_WIDE / (self.YEARS + self.EXCLUDED_WIDE)
            self.grid.setColumnWidth(self.YEARS, round(width * share))

    def eventFilter(self, watched, event):
        # 표 본체가 바뀌는 시점에는 viewport 너비가 아직 예전 값이라 viewport를 직접 본다.
        if watched is self.grid.viewport() and event.type() == QEvent.Resize:
            self.fit_excluded_column()
        elif watched is self.year_scroll and event.type() == QEvent.Resize:
            self.scroll_filler.setFixedHeight(self.year_scroll.height())
        return super().eventFilter(watched, event)
        self.align_current()

    def align_current(self):
        """우측 현재 칸이 연도 열이면 제외월 기준 연도의 열에 둔다. 둘이 어긋나 보이지 않게 한다."""
        row, column = self.grid.currentRow(), self.grid.currentColumn()
        target = self.years_shown().index(self.excluded_year)
        if row >= 0 and 0 <= column < self.YEARS and column != target:
            self.grid.blockSignals(True)
            self.grid.setCurrentCell(row, target)
            self.grid.blockSignals(False)

    def grid_edited(self, item):
        row, column = item.row(), item.column()
        project, text = self.projects[row], item.text().strip()
        if column < self.YEARS:
            year = str(self.years_shown()[column])
            entry = self.entry(project, year)
            if text and entry:
                entry.manpower = text
            elif text:
                project.years.append(YearEntry(year, text))
                project.years.sort(key=lambda e: (not valid_year(e.year), e.year))
            elif entry and entry.excluded.strip():
                entry.manpower = ''  # 제외 월이 남아 있으면 항목을 지우지 않는다(빈 배정은 오류로 표시).
            elif entry:
                project.years.remove(entry)
            else:
                return
        else:
            entry = self.entry(project, self.excluded_year)
            if entry is None:
                return
            entry.excluded = text
        self.refresh_grid()
        self.update_total(row)
        self.changed.emit()

    def grid_current_changed(self, row, column, *args):
        if 0 <= column < self.YEARS and self.years_shown()[column] != self.excluded_year:
            self.excluded_year = self.years_shown()[column]
            self.refresh_grid()
        self.sync_rows(self.table, row)

    # --- 좌우 맞춤 ---

    def sync_rows(self, target, row):
        if row < 0 or target.currentRow() == row:
            return
        column = target.currentColumn()
        if column < 0:
            # 처음 맞출 때 우측은 제외월 기준 연도 열, 좌측은 사업명 열에 둔다.
            column = self.years_shown().index(self.excluded_year) if target is self.grid else self.NAME
        target.blockSignals(True)
        target.setCurrentCell(row, column)
        target.blockSignals(False)
        target.viewport().update()

    def sync_headers(self):
        height = max(view.horizontalHeader().sizeHint().height() for view in (self.table, self.grid))
        for view in (self.table, self.grid):
            view.horizontalHeader().setFixedHeight(height)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            QTimer.singleShot(0, self.sync_headers)

    def apply_theme(self):
        """시스템 테마가 바뀌면 스타일·범례·칸 색을 다시 칠한다."""
        self.notice.setStyleSheet(theme.error_stylesheet())
        for chip, key, text in self.legends:
            chip.setStyleSheet(theme.legend_stylesheet(theme_color(key)))
            chip.setText(f'  {text}  ')
        self.refresh()


def rename_key(mapping, old, new):
    """순서를 유지하며 키 이름을 바꾼 새 dict."""
    return {new if key == old else key: value for key, value in mapping.items()}


class ProjectDelegate(SelectionDelegate):
    """참여 사업 칸에서 고를 수 있는 사업을 드롭다운으로 보여 준다. 값은 사업약칭으로 저장한다."""

    def __init__(self, view, choices):
        super().__init__(view)
        self.choices = choices  # 행 번호 → [(약칭, 표시 문구)]

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for alias, label in self.choices(index.row()):
            editor.addItem(label, alias)
        QTimer.singleShot(0, editor, editor.showPopup)  # 칸을 열면 바로 목록을 펼친다(편집기가 닫히면 취소).
        return editor

    def setEditorData(self, editor, index):
        position = editor.findData(index.data() or '')
        editor.setCurrentIndex(max(position, 0))

    def setModelData(self, editor, model, index):
        if editor.currentIndex() >= 0:
            model.setData(index, editor.currentData())


class PeopleEditor(QWidget):
    """관리 연도, 왼쪽 사람 목록, 오른쪽 고른 사람의 참여 사업·참여율과 공백 기간."""

    changed = Signal()
    year_changed = Signal()
    PROJECT, RATE, PROJECT_NAME = 0, 1, 2  # 사업명은 계산 칸이라 맨 끝에 두어 엑셀의 (사업, 참여율) 붙여넣기를 막지 않는다.

    def __init__(self, projects):
        super().__init__()
        self.projects = projects  # 사업 편집기의 현재 목록을 돌려주는 함수
        self.doc = PeopleDoc()
        self.year_edit = QLineEdit()
        self.year_edit.setMaximumWidth(120)
        self.year_edit.textEdited.connect(self.year_edited)

        self.people_list = OrderedPeopleList()
        self.people_list.order_changed.connect(self.people_rows_moved)
        self.people_list.currentRowChanged.connect(lambda row: self.show_person())
        self.people_list.itemChanged.connect(self.name_edited)
        self.add_button, self.remove_button = QPushButton('사람 추가'), QPushButton('사람 삭제')
        self.add_button.clicked.connect(self.add_person)
        self.remove_button.clicked.connect(self.remove_person)
        self.message = QLabel()
        self.message.setStyleSheet(theme.error_stylesheet())
        self.message.setWordWrap(True)
        left = titled('사람 (이름을 두 번 눌러 바꿉니다)', self.people_list,
                      button_row(self.add_button, self.remove_button), self.message)

        self.title = QLabel()
        self.allocations = EditTable(['사업', '참여율', '사업명'], stretch_last=True)
        self.allocations.setItemDelegateForColumn(self.PROJECT, ProjectDelegate(self.allocations, self.choices))
        self.allocations.itemChanged.connect(self.allocation_edited)
        self.totals = QLabel()
        self.add_row_button, self.remove_row_button = QPushButton('행 추가'), QPushButton('행 삭제')
        self.add_row_button.clicked.connect(self.add_row)
        self.remove_row_button.clicked.connect(self.remove_row)
        self.add_fixed_button = QPushButton('선택 사업 월 고정')
        self.add_fixed_button.clicked.connect(self.add_fixed)
        self.fixed = EditTable(['사업'] + [f'{m}월' for m in range(1, 13)] + ['고정 합계'])
        self.fixed.itemChanged.connect(self.fixed_edited)
        self.remove_fixed_button = QPushButton('선택 고정 해제')
        self.remove_fixed_button.clicked.connect(self.remove_fixed)
        self.fixed_panel = titled('월별 고정 — 0도 고정하며, 12개월 합계는 연간 참여율과 같아야 합니다.',
                                  self.fixed, button_row(self.remove_fixed_button))
        self.absences = EditTable(['시작일', '종료일', '사유'], stretch_last=True)
        self.absences.set_date_columns(0, 1)
        self.absences.itemChanged.connect(self.absence_edited)
        self.add_absence_button, self.remove_absence_button = QPushButton('공백 추가'), QPushButton('공백 삭제')
        self.add_absence_button.clicked.connect(self.add_absence)
        self.remove_absence_button.clicked.connect(self.remove_absence)
        right = QSplitter(Qt.Vertical)
        right.addWidget(titled(self.title, self.allocations, self.totals,
                               button_row(self.add_row_button, self.remove_row_button, self.add_fixed_button)))
        right.addWidget(self.fixed_panel)
        right.addWidget(titled('공백 기간', self.absences,
                               button_row(self.add_absence_button, self.remove_absence_button)))
        right.setSizes([350, 200, 250])

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel('관리 연도'))
        year_row.addWidget(self.year_edit)
        year_row.addWidget(QLabel('사람마다 사업 합계 + 공백 = 1.00000이 되도록 참여율을 입력합니다. '
                                  '사업은 관리 연도에 배정이 있는 사업에서 고릅니다.'))
        year_row.addStretch()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([260, 940])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(year_row)
        layout.addWidget(split, 1)  # 남는 높이를 표 영역이 가져가야 연도 줄이 늘어나지 않는다.
        self.overview = PeopleOverview(self)
        self.refresh()

    # --- 문서 ---

    def set_doc(self, doc):
        self.doc = doc
        self.year_edit.setText(doc.year)
        self.refresh()
        if doc.people:
            self.people_list.setCurrentRow(0)

    def people_rows_moved(self, *args):
        names = [self.people_list.item(row).text() for row in range(self.people_list.count())]
        if names == [person.name for person in self.doc.people]:
            return
        lookup = {person.name: person for person in self.doc.people}
        if len(lookup) != len(names) or set(lookup) != set(names):
            return
        self.doc.people[:] = [lookup[name] for name in names]
        self.overview.refresh()
        self.changed.emit()

    def reorder_people(self, names):
        if names == [person.name for person in self.doc.people]:
            return
        lookup = {person.name: person for person in self.doc.people}
        if len(lookup) != len(names) or set(lookup) != set(names):
            return
        selected = self.current()
        self.doc.people[:] = [lookup[name] for name in names]
        self.refresh()
        if selected is not None:
            self.people_list.setCurrentRow(self.doc.people.index(selected))
        self.changed.emit()

    def current(self):
        row = self.people_list.currentRow()
        return self.doc.people[row] if 0 <= row < len(self.doc.people) else None

    def select_person(self, row):
        self.people_list.setCurrentRow(row)

    def refresh(self):
        """사업 목록·관리 연도가 바뀌거나 문서를 바꿀 때 전체를 다시 그린다."""
        row = self.people_list.currentRow()
        self.people_list.blockSignals(True)
        self.people_list.clear()
        for person in self.doc.people:
            item = QListWidgetItem(person.name)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.people_list.addItem(item)
        self.people_list.blockSignals(False)
        for index in range(len(self.doc.people)):
            self.mark_person(index)
        if self.doc.people:
            self.people_list.setCurrentRow(min(max(row, 0), len(self.doc.people) - 1))
        self.remove_button.setEnabled(bool(self.doc.people))
        self.mark_year()
        self.show_person()
        self.overview.refresh()

    def notify(self):
        self.overview.refresh()
        self.changed.emit()

    def apply_theme(self):
        """시스템 테마가 바뀌면 스타일·목록·칸 색을 다시 칠한다."""
        self.message.setStyleSheet(theme.error_stylesheet())
        self.refresh()

    # --- 사람 목록 ---

    def name_problem(self, person):
        if not person.name.strip():
            return '이름을 입력하세요.'
        if [p.name for p in self.doc.people].count(person.name) > 1:
            return '같은 이름이 이미 있습니다.'
        return ''

    def total_problem(self, person):
        targets, absence = person_totals(person, self.doc.year)
        if targets is None or absence is None:
            return '참여율이나 공백 기간 형식을 확인하세요.'
        if targets + absence != 100000:
            return f'사업 합계 + 공백이 1.00000이어야 합니다 (현재 {amount(targets + absence)}).'
        return ''

    def mark_person(self, index):
        """합계가 맞지 않거나 이름에 문제가 있는 사람은 목록에서 붉게 표시한다."""
        person, item = self.doc.people[index], self.people_list.item(index)
        problem = (self.name_problem(person) or self.total_problem(person) or self.row_problem(person)
                   or next((self.fixed_problem(person, alias) for alias in person.fixed
                            if self.fixed_problem(person, alias)), ''))
        self.people_list.blockSignals(True)
        # 정상은 기본 글자색(시스템 테마)으로 둔다. 검은색을 고정하면 다크에서 안 보인다.
        if problem:
            item.setForeground(theme.error_text())
        else:
            item.setForeground(QBrush())
        item.setToolTip(problem)
        self.people_list.blockSignals(False)

    def unique_name(self):
        names = {p.name for p in self.doc.people}
        return next(name for n in range(1, len(names) + 2)
                    if (name := '새 사람' if n == 1 else f'새 사람 {n}') not in names)

    def add_person(self):
        self.doc.people.append(PersonEntry(self.unique_name()))
        self.refresh()
        self.people_list.setCurrentRow(len(self.doc.people) - 1)
        self.people_list.editItem(self.people_list.currentItem())
        self.notify()

    def remove_person(self):
        row = self.people_list.currentRow()
        if not 0 <= row < len(self.doc.people):
            return
        self.doc.people.pop(row)
        self.refresh()
        self.notify()

    def name_edited(self, item):
        """이름은 비어 있거나 다른 사람과 같을 수 없다. 그런 이름은 받지 않고 원래 이름으로 돌린다."""
        index = self.people_list.row(item)
        person, name = self.doc.people[index], item.text().strip()
        if name == person.name:
            return
        others = {p.name for p in self.doc.people if p is not person}
        problem = '이름을 입력하세요.' if not name else f'"{name}"은(는) 이미 있는 이름입니다.' if name in others else ''
        self.message.setText(problem)
        if problem:
            self.people_list.blockSignals(True)
            item.setText(person.name)
            self.people_list.blockSignals(False)
            return
        person.name = name
        self.people_list.blockSignals(True)
        item.setText(name)
        self.people_list.blockSignals(False)
        for row in range(len(self.doc.people)):
            self.mark_person(row)
        self.show_person()
        self.notify()

    # --- 관리 연도 ---

    def mark_year(self):
        problem = '' if valid_year(self.doc.year) else '4자리 연도를 입력하세요 (예: 2026).'
        self.year_edit.setStyleSheet(f'background-color: {theme.problem_color().name()};' if problem else '')
        self.year_edit.setToolTip(problem)

    def year_edited(self, text):
        self.doc.year = text.strip()
        self.mark_year()
        for row in range(len(self.doc.people)):
            self.mark_person(row)
        self.show_person()
        self.year_changed.emit()
        self.notify()

    # --- 참여 사업 ---

    def available(self):
        """관리 연도에 배정이 있고 사업 기간이 그 해와 겹치는 사업: 약칭 → (사업, 배정 상한 또는 None)."""
        year = self.doc.year.strip()
        result = {}
        for project in self.projects():
            alias = project.alias.strip()
            entry = next((e for e in project.years if e.year.strip() == year), None)
            start, end = to_date(project.start), to_date(project.end)
            if alias and entry and valid_year(year) and (not (start and end) or start.year <= int(year) <= end.year):
                result.setdefault(alias, (project, quantity_units(entry.manpower)))
        return result

    def choices(self, row):
        """행에서 고를 수 있는 사업. 이 사람이 다른 행에서 이미 고른 사업은 뺀다. 잔여 배정을 함께 보인다."""
        person = self.current()
        if person is None:
            return []
        current = list(person.allocations)[row] if row < len(person.allocations) else ''
        taken = set(person.allocations) - {current}
        result = []
        for alias, (project, limit) in self.available().items():
            if alias in taken:
                continue
            used = sum(quantity_units(p.allocations.get(alias, '')) or 0 for p in self.doc.people)
            remain = f'잔여 {amount(limit - used)}' if limit is not None else '배정 형식 확인 필요'
            result.append((alias, f'{alias} — {project.name} ({remain})'))
        return result

    def row_problem(self, person, alias=None):
        """참여 사업 행의 문제. alias를 주면 그 행만, 아니면 사람의 첫 문제를 돌려준다."""
        available = self.available()
        known = {p.alias.strip() for p in self.projects()}
        rows = [(alias, person.allocations.get(alias, ''))] if alias is not None else person.allocations.items()
        for key, value in rows:
            if not key:
                return '사업을 고르세요.'
            if key not in known:
                return '사업 목록에 없는 약칭입니다. 사업 입력 탭에서 추가하거나 행을 지우세요.'
            if key not in available:
                return f'{self.doc.year} 배정이 없거나 사업 기간이 아닌 사업입니다 (계산하면 오류).'
        return ''

    def show_person(self):
        person = self.current()
        self.title.setText(f'{person.name}의 참여 사업' if person else '사람을 고르세요')
        table = self.allocations
        table.blockSignals(True)
        rows = list(person.allocations.items()) if person else []
        table.setRowCount(len(rows))
        names = {p.alias.strip(): p.name for p in self.projects()}
        for row, (alias, value) in enumerate(rows):
            table.set_cell(row, self.PROJECT, alias, problem=self.row_problem(person, alias),
                           note=names.get(alias, '') + ('\n월별 고정값이 있습니다.' if alias in person.fixed else ''))
            rate = QUANTITY_HINT if value and quantity_units(value) is None else (
                '참여율을 입력하세요.' if not value else '')
            table.set_cell(row, self.RATE, value, problem=rate)
            table.set_cell(row, self.PROJECT_NAME, names.get(alias, ''), editable=False)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        for button in (self.add_row_button, self.remove_row_button, self.add_absence_button,
                       self.remove_absence_button, self.add_fixed_button, self.remove_fixed_button):
            button.setEnabled(person is not None)
        self.show_totals()
        self.show_absences()
        self.show_fixed()

    def show_totals(self):
        person = self.current()
        if person is None:
            self.totals.setText('')
            return
        targets, absence = person_totals(person, self.doc.year)
        texts = [amount(v) if v is not None else '?' for v in (targets, absence)]
        total = amount(targets + absence) if targets is not None and absence is not None else '?'
        problem = self.total_problem(person)
        self.totals.setText(f'사업 합계 {texts[0]} · 공백 {texts[1]} · 합계 {total}'
                            + (f'  ⚠ {problem}' if problem else ''))
        self.totals.setStyleSheet(theme.error_stylesheet() if problem else '')

    def allocation_edited(self, item):
        person = self.current()
        if person is None:
            return
        row, text = item.row(), item.text().strip()
        if not 0 <= row < len(person.allocations):
            return
        alias = list(person.allocations)[row]
        if item.column() == self.PROJECT_NAME:
            return
        if item.column() == self.PROJECT:
            if text == alias:
                return
            if alias in person.fixed:
                self.message.setText('월 고정 사업을 다른 사업으로 바꾸려면 먼저 월 고정을 해제하세요.')
                self.show_person()
                return
            if text in person.allocations:
                self.message.setText(f'{person.name}에게 "{text}" 사업이 이미 있습니다.')
                self.show_person()
                return
            self.message.setText('')
            person.allocations = rename_key(person.allocations, alias, text)
        else:
            person.allocations[alias] = text
        self.show_person()
        self.mark_person(self.people_list.currentRow())
        self.notify()

    def add_row(self):
        """빈 행을 추가하고 사업 드롭다운을 연다. 아직 사업을 고르지 않은 행이 있으면 그 행으로 간다."""
        person = self.current()
        if person is None:
            return
        person.allocations.setdefault('', '')
        self.show_person()
        row = list(person.allocations).index('')
        self.allocations.setCurrentCell(row, self.PROJECT)
        self.allocations.editItem(self.allocations.item(row, self.PROJECT))
        self.mark_person(self.people_list.currentRow())
        self.notify()

    def remove_row(self):
        person, row = self.current(), self.allocations.currentRow()
        if person is None or not 0 <= row < len(person.allocations):
            return
        alias = list(person.allocations)[row]
        if alias in person.fixed:
            self.message.setText('월 고정 사업의 참여율 행을 삭제하려면 먼저 월 고정을 해제하세요.')
            return
        person.allocations.pop(alias)
        self.show_person()
        self.mark_person(self.people_list.currentRow())
        self.notify()

    # --- 사업 입력과의 연결 ---

    def count_alias(self, alias, doc=None):
        target = self.doc if doc is None else doc
        return sum(1 for person in target.people if person.allocations.get(alias, '').strip() or alias in person.fixed)

    def rename_alias(self, old, new, doc=None):
        target = self.doc if doc is None else doc
        for person in target.people:
            if old in person.allocations and not person.allocations.get(new):
                person.allocations = rename_key({k: v for k, v in person.allocations.items() if k != new}, old, new)
            if old in person.fixed and new not in person.fixed:
                person.fixed = rename_key(person.fixed, old, new)
        if target is self.doc:
            self.refresh()

    def remove_alias(self, alias, doc=None):
        target = self.doc if doc is None else doc
        for person in target.people:
            person.allocations.pop(alias, None)
            person.fixed.pop(alias, None)
        if target is self.doc:
            self.refresh()

    # --- 월별 고정 ---

    @staticmethod
    def fixed_problem(person, alias):
        if alias not in person.allocations:
            return '고정 사업의 연간 참여율을 allocations에 입력하세요.'
        units = [quantity_units(value) for value in person.fixed[alias]]
        if len(units) != 12 or None in units:
            return '고정값은 소수 5자리 이하의 비음수 수량 12개여야 합니다.'
        target = quantity_units(person.allocations[alias])
        if target is not None and sum(units) != target:
            return f'고정 합계 {amount(sum(units))}와 연간 참여율 {amount(target)}이 다릅니다.'
        return ''

    def show_fixed(self):
        person = self.current()
        entries = list(person.fixed.items()) if person else []
        self.fixed.blockSignals(True)
        self.fixed.setRowCount(len(entries))
        for row, (alias, values) in enumerate(entries):
            problem = self.fixed_problem(person, alias)
            self.fixed.set_cell(row, 0, alias, editable=False, problem=problem)
            for month, value in enumerate(values):
                self.fixed.set_cell(row, month + 1, value,
                                    problem=QUANTITY_HINT if quantity_units(value) is None else problem)
            units = [quantity_units(value) for value in values]
            self.fixed.set_cell(row, 13, amount(sum(units)) if None not in units else '?',
                                editable=False, computed=True, problem=problem)
        self.fixed.blockSignals(False)
        self.fixed.resizeColumnsToContents()
        # fixed가 없는 사람도 표에서 바로 추가할 수 있도록 패널은 항상 보여 준다.

    def add_fixed(self):
        person, row = self.current(), self.allocations.currentRow()
        if person is None or not 0 <= row < len(person.allocations):
            self.message.setText('참여 사업 표에서 월별로 고정할 사업을 먼저 선택하세요.')
            return
        alias = list(person.allocations)[row]
        if not alias:
            self.message.setText('먼저 사업을 선택하세요.')
            return
        if alias not in person.fixed:
            person.fixed[alias] = ['0.00000'] * 12
            self.mark_person(self.people_list.currentRow())
            self.notify()
        self.show_person()
        self.fixed.setCurrentCell(list(person.fixed).index(alias), 1)

    def fixed_edited(self, item):
        person = self.current()
        if person is None or not 0 <= item.row() < len(person.fixed) or not 1 <= item.column() <= 12:
            return
        alias = list(person.fixed)[item.row()]
        person.fixed[alias][item.column() - 1] = item.text().strip()
        self.mark_person(self.people_list.currentRow())
        self.notify()
        # 편집 중인 셀을 다시 만들지 않고 안내 색만 갱신한다.
        problem = self.fixed_problem(person, alias)
        self.fixed.blockSignals(True)
        try:
            units = [quantity_units(value) for value in person.fixed[alias]]
            self.fixed.item(item.row(), 13).setText(amount(sum(units)) if None not in units else '?')
            for column in range(14):
                value = self.fixed.item(item.row(), column).text()
                self.fixed.mark(item.row(), column,
                                QUANTITY_HINT if 1 <= column <= 12 and quantity_units(value) is None else problem)
        finally:
            self.fixed.blockSignals(False)

    def remove_fixed(self):
        person, row = self.current(), self.fixed.currentRow()
        if person is None or not 0 <= row < len(person.fixed):
            return
        person.fixed.pop(list(person.fixed)[row])
        self.show_person()
        self.mark_person(self.people_list.currentRow())
        self.notify()

    # --- 공백 기간 ---

    def show_absences(self):
        person = self.current()
        entries = person.absences if person else []
        self.absences.blockSignals(True)
        self.absences.setRowCount(len(entries))
        problems = absence_problems(entries)
        for row, entry in enumerate(entries):
            for column, text in enumerate((entry.start, entry.end, entry.reason)):
                self.absences.set_cell(row, column, text, problem=problems.get((row, column), ''))
        self.absences.blockSignals(False)
        self.absences.resizeColumnsToContents()

    def absence_edited(self, item):
        person = self.current()
        if person is None or not 0 <= item.row() < len(person.absences):
            return
        entry = person.absences[item.row()]
        setattr(entry, ('start', 'end', 'reason')[item.column()], item.text().strip())
        problems = absence_problems(person.absences)
        for row in range(len(person.absences)):
            for column in range(3):
                self.absences.mark(row, column, problems.get((row, column), ''))
        self.show_totals()
        self.mark_person(self.people_list.currentRow())
        self.notify()

    def add_absence(self):
        person = self.current()
        if person is None:
            return
        person.absences.append(AbsenceEntry())
        self.show_absences()
        self.absences.setCurrentCell(len(person.absences) - 1, 0)
        self.show_totals()
        self.mark_person(self.people_list.currentRow())
        self.notify()

    def remove_absence(self):
        person, row = self.current(), self.absences.currentRow()
        if person is None or not 0 <= row < len(person.absences):
            return
        person.absences.pop(row)
        self.show_absences()
        self.show_totals()
        self.mark_person(self.people_list.currentRow())
        self.notify()


class PeopleOverview(QWidget):
    """사람 × 사업 참여율 종합 표(읽기 전용). 행을 두 번 누르면 그 사람의 입력 화면으로 간다."""

    person_requested = Signal(int)
    projects_order_requested = Signal(list)

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.matrix = EditTable()
        self.matrix.row_order_committed = self.editor.reorder_people
        self.matrix.horizontalHeader().viewport().installEventFilter(self)
        self.matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix.cellDoubleClicked.connect(self._double_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel('전체 사람의 사업 참여율입니다. 편집은 사람 입력 탭에서 합니다 '
                                '(행을 두 번 누르면 그 사람으로 이동). '
                                '이름 셀을 드래그하면 행, 사업 머리글을 드래그하면 열 순서가 바뀝니다. '
                                '맨 아래 사업별합계 행은 고정 행입니다.'))
        layout.addWidget(self.matrix)

    def eventFilter(self, watched, event):
        if watched is self.matrix.horizontalHeader().viewport() and event.type() == QEvent.MouseButtonRelease:
            QTimer.singleShot(0, self.commit_project_order)
        return super().eventFilter(watched, event)

    def commit_project_order(self):
        matrix = self.matrix
        keys = matrix._column_keys
        aliases = [keys[logical] for logical in sorted(keys, key=matrix.horizontalHeader().visualIndex)]
        known = [project.alias.strip() for project in self.editor.projects() if project.alias.strip()]
        if len(known) == len(aliases) and set(known) == set(aliases) and known != aliases:
            self.projects_order_requested.emit(aliases)

    def _double_clicked(self, row, column):
        """합계 행을 두 번 눌러도 사람 입력 화면으로 이동하지 않는다."""
        if 0 <= row < len(self.editor.doc.people):
            self.person_requested.emit(row)

    def refresh(self):
        doc, projects = self.editor.doc, self.editor.projects()
        known = list(dict.fromkeys(p.alias.strip() for p in projects if p.alias.strip()))
        used = {alias for person in doc.people for alias, value in person.allocations.items() if alias and value}
        columns = [(alias, True) for alias in known] + [(alias, False) for alias in sorted(used - set(known))]
        names = {p.alias.strip(): p.name for p in projects}
        headers = ['이름'] + [alias if ok else f'{alias} (미등록)' for alias, ok in columns]
        matrix = self.matrix
        matrix._ordering_update = True
        matrix.setColumnCount(len(headers) + 3)
        matrix.setHorizontalHeaderLabels(headers + ['할당합계', '공백', '합계'])
        for column, (alias, ok) in enumerate(columns, 1):
            matrix.horizontalHeaderItem(column).setToolTip(names.get(alias, '사업 목록에 없는 약칭입니다.')
                                                          + '\n드래그하여 사업 열 순서를 바꿀 수 있습니다.')
        matrix.horizontalHeaderItem(0).setToolTip('사람 이름 셀을 드래그하면 행 전체가 이동합니다.')
        for column in range(len(headers), matrix.columnCount()):
            matrix.horizontalHeaderItem(column).setToolTip('고정 열입니다. 드래그로 이동하지 않습니다.')
        matrix.setRowCount(len(doc.people) + (1 if doc.people else 0))
        for row, person in enumerate(doc.people):
            matrix.set_cell(row, 0, person.name, editable=False, problem=self.editor.name_problem(person))
            for column, (alias, ok) in enumerate(columns, 1):
                value = person.allocations.get(alias, '')
                problem = QUANTITY_HINT if value and quantity_units(value) is None else ''
                if value and not ok:
                    problem = problem or '사업 목록에 없는 약칭입니다.'
                matrix.set_cell(row, column, value, editable=False, problem=problem)
            targets, absence = person_totals(person, doc.year)
            total = targets + absence if targets is not None and absence is not None else None
            base = len(columns) + 1
            for offset, value in enumerate((targets, absence, total)):
                matrix.set_cell(row, base + offset, amount(value) if value is not None else '?', editable=False,
                                computed=True, problem=self.editor.total_problem(person) if offset == 2 else '')
        if doc.people:
            total_row = len(doc.people)
            matrix.set_cell(total_row, 0, '사업별합계', editable=False,
                            note='각 사업 열의 사람 합계입니다. 드래그로 이동하지 않습니다.')
            for column, (alias, ok) in enumerate(columns, 1):
                bad, subtotal = False, 0
                for person in doc.people:
                    raw = person.allocations.get(alias, '')
                    if not raw:
                        continue
                    unit = quantity_units(raw)
                    if unit is None:
                        bad = True
                        break
                    subtotal += unit
                matrix.set_cell(total_row, column, '?' if bad else amount(subtotal), editable=False,
                                computed=True)
            business, absence_total, bad_business, bad_absence = 0, 0, False, False
            for person in doc.people:
                targets, absence = person_totals(person, doc.year)
                if targets is None:
                    bad_business = True
                else:
                    business += targets
                if absence is None:
                    bad_absence = True
                else:
                    absence_total += absence
            base = len(columns) + 1
            for offset, (value, bad) in enumerate(((business, bad_business),
                                                  (absence_total, bad_absence),
                                                  (business + absence_total, bad_business or bad_absence))):
                matrix.set_cell(total_row, base + offset, '?' if bad else amount(value), editable=False,
                                computed=True)
        # 종합은 별도 표시 순서를 갖지 않는다. 입력 문서의 목록 순서가 항상 기준이다.
        matrix._order_cache.pop(('overview', doc.year), None)
        matrix.configure_order(('overview', doc.year),
                               rows={row: person.name for row, person in enumerate(doc.people)},
                               columns={column: alias for column, (alias, _) in enumerate(columns, 1)})
        matrix.resizeColumnsToContents()
