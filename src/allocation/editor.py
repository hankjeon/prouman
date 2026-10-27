"""YAML을 몰라도 사업·사람 입력을 표로 편집하는 위젯."""
from datetime import date

from PySide6.QtCore import QDate, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QDateEdit, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QSplitter, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
                               QWidget)

from .document import (QUANTITY_HINT, AbsenceEntry, PeopleDoc, PersonEntry, ProjectEntry, YearEntry,
                       absence_problems, person_totals, project_problems, quantity_units, to_date, valid_year,
                       year_problems)
from .models import amount

SELECTION_COLOR = QColor('#b7dcff')
CURRENT_CELL_COLOR = QColor('#1a73e8')
CURRENT_ROW_COLOR = QColor('#eef5ff')
PROBLEM_COLOR = QColor('#ffd6d6')
COMPUTED_COLOR = QColor('#f0f0f0')
EXCLUDED_COLOR = QColor('#e6dcff')  # 제외 월이 있는 연도의 배정인력
OUTSIDE_COLOR = QColor('#e4e4e4')  # 사업 기간 밖 연도, 입력할 수 없는 칸
TRANSPARENT = QColor(0, 0, 0, 0)
BASE_ROLE, NOTE_ROLE = Qt.UserRole + 1, Qt.UserRole + 2  # 칸의 기본 배경색과 안내(오류가 없을 때 보임)


class SelectionDelegate(QStyledItemDelegate):
    """선택 셀을 직접 칠한다.

    Windows 11 기본 스타일은 선택 셀을 거의 보이지 않는 회색으로, 현재 셀은 흰 바탕으로 그리고
    스타일시트로도 포커스 상태에 따라 일관되게 바뀌지 않는다. 포커스와 관계없이 선택 범위는 같은 색,
    현재 셀은 테두리로 표시한다. 글자 색(검증 행 색 등)은 그대로 둔다. 표의 row_band가 켜져 있으면
    현재 행을 옅은 띠로 칠한다. 칸 자체의 배경색은 띠 위에 그려지므로 띠보다 우선한다.
    """

    selected = False

    def initStyleOption(self, option, index):
        # 칸의 배경색(오류·제외 월 등)보다 선택 색을 우선해야 색이 있는 칸도 선택이 보인다.
        super().initStyleOption(option, index)
        if self.selected:
            option.backgroundBrush = QBrush(SELECTION_COLOR)

    def paint(self, painter, option, index):
        state = option.state
        plain = QStyleOptionViewItem(option)
        plain.state = state & ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        view = self.parent()
        self.selected = bool(state & QStyle.StateFlag.State_Selected)
        if self.selected:
            painter.fillRect(option.rect, SELECTION_COLOR)
        elif getattr(view, 'row_band', False) and index.row() == view.currentRow():
            painter.fillRect(option.rect, CURRENT_ROW_COLOR)
        super().paint(painter, plain, index)
        self.selected = False
        if state & QStyle.StateFlag.State_HasFocus:
            painter.save()
            painter.setPen(CURRENT_CELL_COLOR)
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


class EditTable(QTableWidget):
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
            base = base or COMPUTED_COLOR
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
        item.setBackground(PROBLEM_COLOR if problem else base if base is not None else TRANSPARENT)
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
        cells = {(i.row(), i.column()): i.text() for i in self.selectedItems()}
        if not cells:
            return ''
        rows = range(min(r for r, _ in cells), max(r for r, _ in cells) + 1)
        columns = range(min(c for _, c in cells), max(c for _, c in cells) + 1)
        text = '\n'.join('\t'.join(cells.get((r, c), '') for c in columns) for r in rows)
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
    label = QLabel(f'  {text}  ')
    label.setStyleSheet(f'background-color: {color.name()}; border: 1px solid #c8c8c8; padding: 2px;')
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return label


class ProjectEditor(QWidget):
    """좌측 사업 목록(사업명·약칭·기간·총 배정)과 우측 사업 × 연도 배정인력 표.

    두 표는 행 순서·행 높이·세로 스크롤·현재 행이 맞물린다. 우측은 연속한 5개 연도와, 현재 칸의
    연도를 따르는 맨 오른쪽 `YYYY 제외월` 열로 이루어진다.
    """

    changed = Signal()
    alias_renamed = Signal(str, str)
    alias_removed = Signal(str)
    NAME, ALIAS, START, END, TOTAL = range(5)
    YEARS = 5

    def __init__(self):
        super().__init__()
        self.projects = []
        self.managed_year = lambda: str(date.today().year)  # 관리 연도. 메인 창이 사람 입력과 연결한다.
        self.allow_remove = lambda project: True
        self.year_start = self.base_year()
        self.excluded_year = self.year_start + 1
        self.table = EditTable(['사업명', '약칭', '시작일', '종료일', '총 배정'], row_band=True)
        self.table.set_date_columns(self.START, self.END)
        self.table.horizontalHeader().setSectionResizeMode(self.NAME, QHeaderView.Stretch)
        self.grid = EditTable([''] * (self.YEARS + 1), row_band=True)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
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

        self.prev_button, self.next_button = QToolButton(), QToolButton()
        self.prev_button.setToolTip('이전 해로 이동')
        self.next_button.setToolTip('다음 해로 이동')
        self.prev_button.clicked.connect(lambda: self.shift_years(-1))
        self.next_button.clicked.connect(lambda: self.shift_years(1))
        self.range_label = QLabel()
        self.notice = QLabel()
        self.notice.setStyleSheet('color: #c62828;')
        title = QHBoxLayout()
        title.addWidget(QLabel('사업 목록'))
        title.addStretch()
        title.addWidget(self.notice)
        title.addWidget(QLabel('연도별 배정인력'))
        title.addWidget(self.prev_button)
        title.addWidget(self.range_label)
        title.addWidget(self.next_button)

        self.add_button, self.remove_button = QPushButton('사업 추가'), QPushButton('사업 삭제')
        self.add_button.clicked.connect(self.add_project)
        self.remove_button.clicked.connect(self.remove_project)
        bottom = button_row(self.add_button, self.remove_button)
        for color, text in ((OUTSIDE_COLOR, '사업 기간 밖 연도'), (EXCLUDED_COLOR, '제외 월 있음'),
                            (PROBLEM_COLOR, '입력 확인 필요')):
            bottom.addWidget(legend(color, text))

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self.table)
        self.split.addWidget(self.grid)
        self.split.setSizes([620, 620])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(title)
        layout.addWidget(self.split, 1)
        layout.addLayout(bottom)
        self.refresh()

    # --- 연도 범위 ---

    def base_year(self):
        year = self.managed_year()
        return (int(year) if valid_year(year) else date.today().year) - 1

    def years_shown(self):
        return [self.year_start + offset for offset in range(self.YEARS)]

    def reset_years(self):
        """처음 범위(관리 연도 - 1부터 5년)로 돌리고 제외월 열은 관리 연도로 둔다."""
        self.year_start = self.base_year()
        self.excluded_year = self.year_start + 1
        self.refresh_grid()

    def shift_years(self, step):
        self.year_start += step
        shown = self.years_shown()
        if self.excluded_year not in shown:
            self.excluded_year = shown[0] if step > 0 else shown[-1]
        self.refresh_grid()

    # --- 데이터 ---

    def set_projects(self, projects):
        self.projects = projects
        self.year_start = self.base_year()
        self.excluded_year = self.year_start + 1
        self.refresh()
        if projects:
            self.table.setCurrentCell(0, 0)

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
            # 제외월 열이 가리키는 연도와 제외월 열 머리글을 같은 색으로 강조한다.
            header.setForeground(CURRENT_CELL_COLOR if year == self.excluded_year else QColor('#000000'))
            header.setToolTip('관리 연도' if year == managed and column < self.YEARS else '')
        hidden = [0, 0]
        unshown = 0
        for row, project in enumerate(self.projects):
            problems = year_problems(project.years)
            index = {id(e): i for i, e in enumerate(project.years)}
            start, end = to_date(project.start), to_date(project.end)
            for entry in project.years:
                if (index[id(entry)], 0) in problems:
                    unshown += 1
                elif int(entry.year) < shown[0]:
                    hidden[0] += 1
                elif int(entry.year) > shown[-1]:
                    hidden[1] += 1
            for column, year in enumerate(shown):
                entry = self.entry(project, year)
                notes, base = [], None
                if start and end and not start.year <= year <= end.year:
                    notes.append('사업 기간 밖 연도입니다 (생성하면 경고).')
                    base = OUTSIDE_COLOR
                if entry and entry.excluded.strip():
                    notes.append(f'제외 월: {entry.excluded}')
                    base = EXCLUDED_COLOR
                problem = problems.get((index[id(entry)], 1), '') if entry else ''
                grid.set_cell(row, column, entry.manpower if entry else '', problem=problem, base=base,
                              note='\n'.join(notes))
            entry = self.entry(project, self.excluded_year)
            if entry is None:
                grid.set_cell(row, self.YEARS, '', editable=False, base=OUTSIDE_COLOR,
                              note=f'먼저 {self.excluded_year} 배정인력을 입력하세요.')
            else:
                grid.set_cell(row, self.YEARS, entry.excluded, problem=problems.get((index[id(entry)], 2), ''))
        grid.blockSignals(False)
        self.prev_button.setText('◀' + (f' {hidden[0]}' if hidden[0] else ''))
        self.next_button.setText((f'{hidden[1]} ' if hidden[1] else '') + '▶')
        self.prev_button.setToolTip(f'이전 해로 이동 (앞쪽에 값이 있는 연도 {hidden[0]}건)' if hidden[0]
                                    else '이전 해로 이동')
        self.next_button.setToolTip(f'다음 해로 이동 (뒤쪽에 값이 있는 연도 {hidden[1]}건)' if hidden[1]
                                    else '다음 해로 이동')
        self.range_label.setText(f'{shown[0]} ~ {shown[-1]}')
        self.notice.setText(f'표시할 수 없는 연도 항목 {unshown}건 (생성하면 오류로 표시)' if unshown else '')
        self.sync_headers()
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


class PeopleEditor(QWidget):
    """관리 연도, 사람 × 사업 참여율 행렬, 선택한 사람의 휴직 기간."""

    changed = Signal()
    year_changed = Signal()

    def __init__(self, projects):
        super().__init__()
        self.projects = projects  # 사업 편집기의 현재 목록을 돌려주는 함수
        self.doc = PeopleDoc()
        self.columns = []  # (약칭, 사업 목록에 있는지)
        self.year_edit = QLineEdit()
        self.year_edit.setMaximumWidth(120)
        self.year_edit.textEdited.connect(self.year_edited)
        self.matrix = EditTable()
        self.matrix.itemChanged.connect(self.matrix_edited)
        self.matrix.currentCellChanged.connect(lambda *args: self.show_absences())
        self.absences = EditTable(['시작일', '종료일', '사유'], stretch_last=True)
        self.absences.set_date_columns(0, 1)
        self.absences.itemChanged.connect(self.absence_edited)
        self.add_button, self.remove_button = QPushButton('사람 추가'), QPushButton('사람 삭제')
        self.add_absence_button, self.remove_absence_button = QPushButton('휴직 추가'), QPushButton('휴직 삭제')
        self.add_button.clicked.connect(self.add_person)
        self.remove_button.clicked.connect(self.remove_person)
        self.add_absence_button.clicked.connect(self.add_absence)
        self.remove_absence_button.clicked.connect(self.remove_absence)
        self.absences_title = QLabel()

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel('관리 연도'))
        year_row.addWidget(self.year_edit)
        year_row.addWidget(QLabel('참여율은 사람마다 사업 합계 + 공백(휴직) = 1.00000이 되도록 입력합니다. '
                                  '엑셀에서 복사한 범위를 Ctrl+V로 붙여 넣을 수 있습니다.'))
        year_row.addStretch()
        split = QSplitter(Qt.Vertical)
        split.addWidget(titled('사람별 사업 참여율', self.matrix, button_row(self.add_button, self.remove_button)))
        split.addWidget(titled(self.absences_title, self.absences,
                               button_row(self.add_absence_button, self.remove_absence_button)))
        split.setSizes([500, 200])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(year_row)
        layout.addWidget(split)
        self.refresh()

    def set_doc(self, doc):
        self.doc = doc
        self.year_edit.setText(doc.year)
        self.refresh()
        if doc.people:
            self.matrix.setCurrentCell(0, 0)

    def current(self):
        row = self.matrix.currentRow()
        return self.doc.people[row] if 0 <= row < len(self.doc.people) else None

    def refresh(self):
        """사업 목록이 바뀌면 열을 다시 만든다. 사업 목록에 없는 약칭도 값이 남아 있으면 열로 보여 준다."""
        known = list(dict.fromkeys(p.alias.strip() for p in self.projects() if p.alias.strip()))
        used = {alias for person in self.doc.people for alias, value in person.allocations.items() if value}
        self.columns = [(alias, True) for alias in known] + [(alias, False) for alias in sorted(used - set(known))]
        names = {p.alias.strip(): p.name for p in self.projects()}
        headers = ['이름'] + [alias if ok else f'{alias} (미등록)' for alias, ok in self.columns]
        self.matrix.blockSignals(True)
        self.matrix.setColumnCount(len(headers) + 3)
        self.matrix.setHorizontalHeaderLabels(headers + ['사업 합계', '공백', '합계'])
        for column, (alias, ok) in enumerate(self.columns, 1):
            self.matrix.horizontalHeaderItem(column).setToolTip(names.get(alias, '사업 목록에 없는 약칭입니다.'))
        self.matrix.setRowCount(len(self.doc.people))
        for row, person in enumerate(self.doc.people):
            self.matrix.set_cell(row, 0, person.name)
            for column, (alias, ok) in enumerate(self.columns, 1):
                self.matrix.set_cell(row, column, person.allocations.get(alias, ''))
            for column in range(len(self.columns) + 1, len(headers) + 3):
                self.matrix.set_cell(row, column, '', editable=False, computed=True)
        self.matrix.blockSignals(False)
        for row in range(len(self.doc.people)):
            self.update_row(row)
        self.matrix.resizeColumnsToContents()
        self.remove_button.setEnabled(bool(self.doc.people))
        self.mark_year()
        self.show_absences()

    def update_row(self, row):
        """행의 입력 확인 표시와 합계 칸을 다시 계산한다."""
        person = self.doc.people[row]
        self.matrix.blockSignals(True)
        if not person.name.strip():
            name_problem = '이름을 입력하세요.'
        elif [p.name for p in self.doc.people].count(person.name) > 1:
            name_problem = '같은 이름이 이미 있습니다.'
        else:
            name_problem = ''
        self.matrix.mark(row, 0, name_problem)
        for column, (alias, ok) in enumerate(self.columns, 1):
            value = person.allocations.get(alias, '')
            problem = QUANTITY_HINT if value and quantity_units(value) is None else ''
            if value and not ok:
                problem = problem or '사업 목록에 없는 약칭입니다. 사업 입력 탭에서 추가하거나 값을 지우세요.'
            self.matrix.mark(row, column, problem)
        targets, absence = person_totals(person, self.doc.year)
        base = len(self.columns) + 1
        texts = [amount(targets) if targets is not None else '?', amount(absence) if absence is not None else '?']
        total = targets + absence if targets is not None and absence is not None else None
        texts.append(amount(total) if total is not None else '?')
        for offset, text in enumerate(texts):
            self.matrix.item(row, base + offset).setText(text)
        problem = '' if total == 100000 else f'사업 합계 + 공백이 1.00000이어야 합니다 (현재 {texts[2]}).'
        item = self.matrix.item(row, base + 2)
        item.setBackground(PROBLEM_COLOR if problem else COMPUTED_COLOR)
        item.setToolTip(problem)
        self.matrix.blockSignals(False)

    def mark_year(self):
        problem = '' if valid_year(self.doc.year) else '4자리 연도를 입력하세요 (예: 2026).'
        self.year_edit.setStyleSheet(f'background-color: {PROBLEM_COLOR.name()};' if problem else '')
        self.year_edit.setToolTip(problem)

    def year_edited(self, text):
        self.doc.year = text.strip()
        self.mark_year()
        for row in range(len(self.doc.people)):
            self.update_row(row)
        self.year_changed.emit()
        self.changed.emit()

    def matrix_edited(self, item):
        row, column = item.row(), item.column()
        person = self.doc.people[row]
        if column == 0:
            person.name = item.text()
            for other in range(len(self.doc.people)):
                self.update_row(other)
            self.show_absences()
        elif column <= len(self.columns):
            alias = self.columns[column - 1][0]
            if item.text().strip():
                person.allocations[alias] = item.text().strip()
            else:
                person.allocations.pop(alias, None)
            self.update_row(row)
        else:
            return
        self.changed.emit()

    def add_person(self):
        self.doc.people.append(PersonEntry())
        self.refresh()
        self.matrix.setCurrentCell(len(self.doc.people) - 1, 0)
        self.matrix.editItem(self.matrix.item(len(self.doc.people) - 1, 0))
        self.changed.emit()

    def remove_person(self):
        row = self.matrix.currentRow()
        if not 0 <= row < len(self.doc.people):
            return
        self.doc.people.pop(row)
        self.refresh()
        if self.doc.people:
            self.matrix.setCurrentCell(min(row, len(self.doc.people) - 1), 0)
        self.changed.emit()

    def count_alias(self, alias):
        return sum(1 for person in self.doc.people if person.allocations.get(alias, '').strip())

    def rename_alias(self, old, new):
        for person in self.doc.people:
            if old in person.allocations and not person.allocations.get(new):
                person.allocations[new] = person.allocations.pop(old)
        self.refresh()

    def remove_alias(self, alias):
        for person in self.doc.people:
            person.allocations.pop(alias, None)
        self.refresh()

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
        name = (person.name or '(이름 없음)') if person else '사람을 고르세요'
        self.absences_title.setText(f'휴직 기간: {name}')
        for button in (self.add_absence_button, self.remove_absence_button):
            button.setEnabled(person is not None)

    def absence_edited(self, item):
        person = self.current()
        entry = person.absences[item.row()]
        setattr(entry, ('start', 'end', 'reason')[item.column()], item.text().strip())
        problems = absence_problems(person.absences)
        for row in range(len(person.absences)):
            for column in range(3):
                self.absences.mark(row, column, problems.get((row, column), ''))
        self.update_row(self.matrix.currentRow())
        self.changed.emit()

    def add_absence(self):
        person = self.current()
        if person is None:
            return
        person.absences.append(AbsenceEntry())
        self.show_absences()
        self.absences.setCurrentCell(len(person.absences) - 1, 0)
        self.update_row(self.matrix.currentRow())
        self.changed.emit()

    def remove_absence(self):
        person, row = self.current(), self.absences.currentRow()
        if person is None or not 0 <= row < len(person.absences):
            return
        person.absences.pop(row)
        self.show_absences()
        self.update_row(self.matrix.currentRow())
        self.changed.emit()
