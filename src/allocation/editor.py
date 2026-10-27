"""YAML을 몰라도 사업·사람 입력을 표로 편집하는 위젯."""
from PySide6.QtCore import QDate, QEvent, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QDateEdit, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .document import (QUANTITY_HINT, AbsenceEntry, PeopleDoc, PersonEntry, ProjectEntry, YearEntry,
                       absence_problems, person_totals, project_problems, quantity_units, to_date, valid_year,
                       year_problems)
from .models import amount

SELECTION_COLOR = QColor('#b7dcff')
CURRENT_CELL_COLOR = QColor('#1a73e8')
PROBLEM_COLOR = QColor('#ffd6d6')
COMPUTED_COLOR = QColor('#f0f0f0')


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

    def __init__(self, headers=(), stretch_last=False):
        super().__init__(0, len(headers))
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

    def set_cell(self, row, column, text, editable=True, problem='', computed=False):
        item = QTableWidgetItem(text)
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        item.setFlags(flags | Qt.ItemIsEditable if editable else flags)
        if computed:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setBackground(COMPUTED_COLOR)
        self.setItem(row, column, item)
        self.mark(row, column, problem)
        return item

    def mark(self, row, column, problem):
        item = self.item(row, column)
        if item is None:
            return
        if problem:
            item.setBackground(PROBLEM_COLOR)
        elif item.flags() & Qt.ItemIsEditable:
            item.setBackground(QColor(0, 0, 0, 0))
        item.setToolTip(problem)

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


class ProjectEditor(QWidget):
    """사업 목록(사업명·약칭·기간)과 선택한 사업의 연도별 배정인력·제외 월."""

    changed = Signal()
    alias_renamed = Signal(str, str)
    alias_removed = Signal(str)

    def __init__(self):
        super().__init__()
        self.projects = []
        self.default_year = lambda: str(QDate.currentDate().year())
        self.allow_remove = lambda project: True
        self.table = EditTable(['사업명', '약칭', '시작일', '종료일'])
        self.table.set_date_columns(2, 3)
        self.years = EditTable(['연도', '배정인력', '제외 월'], stretch_last=True)
        self.years.setToolTip('제외 월은 쉼표로 구분합니다 (예: 4, 5, 6).')
        self.add_button, self.remove_button = QPushButton('사업 추가'), QPushButton('사업 삭제')
        self.add_year_button, self.remove_year_button = QPushButton('연도 추가'), QPushButton('연도 삭제')
        self.add_button.clicked.connect(self.add_project)
        self.remove_button.clicked.connect(self.remove_project)
        self.add_year_button.clicked.connect(self.add_year)
        self.remove_year_button.clicked.connect(self.remove_year)
        self.table.itemChanged.connect(self.project_edited)
        self.table.currentCellChanged.connect(lambda *args: self.show_years())
        self.years.itemChanged.connect(self.year_edited)
        self.years_title = QLabel()

        split = QSplitter(Qt.Vertical)
        split.addWidget(titled('사업 목록', self.table, button_row(self.add_button, self.remove_button)))
        split.addWidget(titled(self.years_title, self.years,
                               button_row(self.add_year_button, self.remove_year_button)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.refresh()

    def set_projects(self, projects):
        self.projects = projects
        self.refresh()
        if projects:
            self.table.setCurrentCell(0, 0)

    def current(self):
        row = self.table.currentRow()
        return self.projects[row] if 0 <= row < len(self.projects) else None

    def refresh(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.projects))
        problems = project_problems(self.projects)
        for row, project in enumerate(self.projects):
            for column, text in enumerate((project.name, project.alias, project.start, project.end)):
                self.table.set_cell(row, column, text, problem=problems.get((row, column), ''))
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.remove_button.setEnabled(bool(self.projects))
        self.show_years()

    def revalidate(self):
        problems = project_problems(self.projects)
        for row in range(len(self.projects)):
            for column in range(4):
                self.table.mark(row, column, problems.get((row, column), ''))

    def project_edited(self, item):
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
        if field in ('name', 'alias'):
            self.show_years()
        self.changed.emit()

    def add_project(self):
        self.projects.append(ProjectEntry(years=[YearEntry(self.default_year())]))
        self.refresh()
        self.table.setCurrentCell(len(self.projects) - 1, 0)
        self.table.editItem(self.table.item(len(self.projects) - 1, 0))
        self.changed.emit()

    def remove_project(self):
        project = self.current()
        if project is None or not self.allow_remove(project):
            return
        row = self.table.currentRow()
        self.projects.pop(row)
        self.refresh()
        if self.projects:
            self.table.setCurrentCell(min(row, len(self.projects) - 1), 0)
        if project.alias:
            self.alias_removed.emit(project.alias)
        self.changed.emit()

    def show_years(self):
        project = self.current()
        self.years.blockSignals(True)
        entries = project.years if project else []
        self.years.setRowCount(len(entries))
        problems = year_problems(entries)
        for row, entry in enumerate(entries):
            for column, text in enumerate((entry.year, entry.manpower, entry.excluded)):
                self.years.set_cell(row, column, text, problem=problems.get((row, column), ''))
        self.years.blockSignals(False)
        self.years.resizeColumnsToContents()
        label = (project.name or project.alias or '(이름 없음)') if project else '사업을 고르세요'
        self.years_title.setText(f'연도별 배정인력·제외 월: {label}')
        for button in (self.add_year_button, self.remove_year_button):
            button.setEnabled(project is not None)

    def year_edited(self, item):
        entry = self.current().years[item.row()]
        setattr(entry, ('year', 'manpower', 'excluded')[item.column()], item.text().strip())
        problems = year_problems(self.current().years)
        for row in range(len(self.current().years)):
            for column in range(3):
                self.years.mark(row, column, problems.get((row, column), ''))
        self.changed.emit()

    def add_year(self):
        project = self.current()
        if project is None:
            return
        years = [int(e.year) for e in project.years if valid_year(e.year)]
        project.years.append(YearEntry(str(max(years) + 1) if years else self.default_year()))
        self.show_years()
        self.years.setCurrentCell(len(project.years) - 1, 1)
        self.changed.emit()

    def remove_year(self):
        project, row = self.current(), self.years.currentRow()
        if project is None or not 0 <= row < len(project.years):
            return
        project.years.pop(row)
        self.show_years()
        self.changed.emit()


class PeopleEditor(QWidget):
    """관리 연도, 사람 × 사업 참여율 행렬, 선택한 사람의 휴직 기간."""

    changed = Signal()

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
