"""연간 계획 편집 화면. 수치 판정은 loading/planning/engine에 맡긴다."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from .document import (AbsenceEntry, PlanDoc, PlanPersonEntry, dump_plan, dump_projects, quantity_units,
                       to_date, valid_year)
from .editor import EditTable, ProjectDelegate
from .loading import load_plan
from .models import amount
from .planning import generate_plan, review_plan


class PlanningPage(QWidget):
    changed = Signal()
    save_requested = Signal()
    confirm_requested = Signal()

    def __init__(self, projects):
        super().__init__()
        self.projects = projects
        self.doc = PlanDoc()
        self.plan_path = '(새 계획 입력)'
        self.last_generation = None
        self._refreshing = False
        self.title = QLabel('연간 계획')
        self.people_list = QListWidget()
        self.people_list.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.people_list.currentRowChanged.connect(self.show_person)
        self.people_list.itemChanged.connect(self.name_edited)
        add_person = QPushButton('사람 추가')
        add_person.clicked.connect(self.add_person)
        remove_person = QPushButton('사람 삭제')
        remove_person.clicked.connect(self.remove_person)
        person_actions = QHBoxLayout()
        person_actions.addWidget(add_person)
        person_actions.addWidget(remove_person)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel('계획 사람'))
        left_layout.addWidget(self.people_list)
        left_layout.addLayout(person_actions)

        self.priorities = EditTable(['사업', '참고 점수', '명시 순위 (선택)'])
        self.priorities.setItemDelegateForColumn(0, ProjectDelegate(self.priorities, self.choices))
        self.priorities.itemChanged.connect(self.priority_edited)
        add_project = QPushButton('참여 사업 추가')
        add_project.clicked.connect(self.add_project)
        remove_project = QPushButton('참여 사업 삭제')
        remove_project.clicked.connect(self.remove_project)
        priority_actions = QHBoxLayout()
        priority_actions.addWidget(add_project)
        priority_actions.addWidget(remove_project)
        self.absences = EditTable(['휴직 시작일', '휴직 종료일', '사유'], stretch_last=True)
        self.absences.set_date_columns(0, 1)
        self.absences.itemChanged.connect(self.absence_edited)
        add_absence = QPushButton('휴직 추가')
        add_absence.clicked.connect(self.add_absence)
        remove_absence = QPushButton('휴직 삭제')
        remove_absence.clicked.connect(self.remove_absence)
        absence_actions = QHBoxLayout()
        absence_actions.addWidget(add_absence)
        absence_actions.addWidget(remove_absence)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel('참고 점수는 큰 값 우선 · 명시 순위는 작은 값 우선 (하나라도 입력하면 빈 순위는 최하위)'))
        right_layout.addWidget(self.priorities)
        right_layout.addLayout(priority_actions)
        right_layout.addWidget(QLabel('휴직 기간'))
        right_layout.addWidget(self.absences)
        right_layout.addLayout(absence_actions)
        upper = QSplitter(Qt.Horizontal)
        upper.addWidget(left)
        upper.addWidget(right)
        upper.setSizes([230, 950])
        self.input_widget = upper

        self.matrix = EditTable()
        self.matrix.itemChanged.connect(self.draft_edited)
        self.column_aliases = []
        self.issues = EditTable(['등급', '코드', '위치', '현재 값', '요구값', '조치'])
        self.issues.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.status = QLabel()
        self.generate_button = QPushButton('계획 계산 / 재계산')
        self.generate_button.clicked.connect(self.generate)
        self.save_button = QPushButton('계획 저장')
        self.save_button.clicked.connect(self.save_requested.emit)
        self.confirm_button = QPushButton('내보내기 — 확정 자료')
        self.confirm_button.clicked.connect(self.confirm_requested.emit)
        actions = QHBoxLayout()
        for button in (self.generate_button, self.save_button, self.confirm_button):
            actions.addWidget(button)
        actions.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(QLabel('사람 × 사업 연간 참여율 초안 (선택 사업 칸만 직접 편집)'))
        layout.addWidget(self.matrix, 2)
        layout.addWidget(self.status)
        layout.addWidget(self.issues, 1)
        layout.addLayout(actions)
        self.refresh()

    def set_doc(self, doc, path='(새 계획 입력)'):
        self.doc, self.plan_path = doc, path
        self.last_generation = None
        self.refresh()

    def current(self):
        row = self.people_list.currentRow()
        return self.doc.people[row] if 0 <= row < len(self.doc.people) else None

    def _changed(self):
        self.last_generation = None
        self.changed.emit()
        self.refresh_review()

    def refresh(self):
        self._refreshing = True
        selected = self.people_list.currentRow()
        self.people_list.clear()
        for person in self.doc.people:
            item = QListWidgetItem(person.name)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.people_list.addItem(item)
        if self.doc.people:
            self.people_list.setCurrentRow(max(0, min(selected, len(self.doc.people) - 1)))
        self._refreshing = False
        self.show_person()
        self.refresh_review()

    def show_person(self, *_):
        if self._refreshing:
            return
        self._refreshing = True
        person = self.current()
        self.priorities.setRowCount(len(self.person_keys(person)) if person else 0)
        if person:
            for row, alias in enumerate(self.person_keys(person)):
                self.priorities.set_cell(row, 0, alias)
                self.priorities.set_cell(row, 1, person.draft.get(alias, ''))
                self.priorities.set_cell(row, 2, person.priorities.get(alias, ''))
        self.absences.setRowCount(len(person.absences) if person else 0)
        if person:
            for row, entry in enumerate(person.absences):
                for column, value in enumerate((entry.start, entry.end, entry.reason)):
                    self.absences.set_cell(row, column, value)
        self.priorities.resizeColumnsToContents()
        self.absences.resizeColumnsToContents()
        self._refreshing = False

    def name_edited(self, item):
        if self._refreshing:
            return
        person = self.doc.people[self.people_list.row(item)]
        name = item.text().strip()
        if not name or any(p is not person and p.name == name for p in self.doc.people):
            self._refreshing = True
            item.setText(person.name)
            self._refreshing = False
            self.status.setText('사람 이름은 비어 있거나 다른 사람과 같을 수 없습니다.')
            return
        person.name = name
        self._changed()

    def add_person(self):
        names = {p.name for p in self.doc.people}
        n = 1
        while (name := '새 사람' if n == 1 else f'새 사람 {n}') in names:
            n += 1
        self.doc.people.append(PlanPersonEntry(name))
        self.refresh()
        self.people_list.setCurrentRow(len(self.doc.people) - 1)
        self._changed()

    def remove_person(self):
        row = self.people_list.currentRow()
        if 0 <= row < len(self.doc.people):
            self.doc.people.pop(row)
            self.refresh()
            self._changed()

    def available(self):
        year = self.doc.year
        result = []
        for project in self.projects():
            start, end = to_date(project.start), to_date(project.end)
            if (valid_year(year) and start and end
                    and start.year <= int(year) <= end.year):
                result.append((project.alias.strip(), f'{project.alias} — {project.name}'))
        return sorted(result)

    @staticmethod
    def person_keys(person):
        return list(dict.fromkeys(list(person.draft) + list(person.priorities)))

    def choices(self, row):
        person = self.current()
        if person is None:
            return []
        keys = self.person_keys(person)
        current = keys[row] if row < len(keys) else ''
        taken = set(keys) - {current}
        return [(alias, label) for alias, label in self.available() if alias not in taken]

    def priority_edited(self, item):
        if self._refreshing:
            return
        person = self.current()
        if person is None:
            return
        keys = self.person_keys(person)
        old = keys[item.row()]
        value = item.text().strip()
        if item.column() == 0:
            if not value or value in set(keys) - {old}:
                self.show_person()
                return
            person.priorities = {value if key == old else key: rank for key, rank in person.priorities.items()}
            person.draft = {value if key == old else key: score for key, score in person.draft.items()}
            if person.allocations is not None and old in person.allocations:
                person.allocations = {value if key == old else key: rate
                                      for key, rate in person.allocations.items()}
        elif item.column() == 1:
            person.draft[old] = value
        else:
            if value:
                person.priorities[old] = value
            else:
                person.priorities.pop(old, None)
        self.show_person()
        self._changed()

    def add_project(self):
        person = self.current()
        if person is None:
            return
        choices = self.choices(len(self.person_keys(person)))
        if not choices:
            self.status.setText('선택할 수 있는 사업이 없습니다. 사업 입력의 해당 연도 배정을 확인하세요.')
            return
        alias = choices[0][0]
        person.draft[alias] = '0.0'
        self.show_person()
        self.priorities.setCurrentCell(len(self.person_keys(person)) - 1, 0)
        self.priorities.editItem(self.priorities.currentItem())
        self._changed()

    def remove_project(self):
        person, row = self.current(), self.priorities.currentRow()
        if person is None or not 0 <= row < len(self.person_keys(person)):
            return
        alias = self.person_keys(person)[row]
        person.priorities.pop(alias, None)
        person.draft.pop(alias, None)
        if person.allocations is not None:
            person.allocations.pop(alias, None)
        self.show_person()
        self._changed()

    def absence_edited(self, item):
        if self._refreshing:
            return
        person = self.current()
        setattr(person.absences[item.row()], ('start', 'end', 'reason')[item.column()], item.text().strip())
        self._changed()

    def add_absence(self):
        person = self.current()
        if person is not None:
            person.absences.append(AbsenceEntry())
            self.show_person()
            self._changed()

    def remove_absence(self):
        person, row = self.current(), self.absences.currentRow()
        if person is not None and 0 <= row < len(person.absences):
            person.absences.pop(row)
            self.show_person()
            self._changed()

    def load_current(self, include_results=True):
        doc = self.doc
        if not include_results:
            doc = deepcopy(doc)
            for person in doc.people:
                person.allocations = None
        return load_plan('(현재 사업 입력)', self.plan_path,
                         {'projects': dump_projects(self.projects()), 'plan': dump_plan(doc)})

    def refresh_review(self):
        plan, load_issues = self.load_current()
        review = review_plan(plan) if plan is not None else None
        issues = list(load_issues) + (review.issues if review else [])
        self.confirm_button.setEnabled(bool(plan is not None and review and not review.has_errors))
        self._show_matrix(plan, issues)
        person = self.current()
        if person is not None:
            for row, alias in enumerate(self.person_keys(person)):
                self.priorities.mark(row, 0, '')
                self.priorities.mark(row, 1, '')
                self.priorities.mark(row, 2, '')
                for issue in issues:
                    if issue.severity != 'ERROR' or person.name not in issue.location:
                        continue
                    if f'.priorities[{alias}]' in issue.location:
                        self.priorities.mark(row, 2, issue.hint)
                    if f'.draft[{alias}]' in issue.location:
                        self.priorities.mark(row, 1, issue.hint)
            for row in range(len(person.absences)):
                self.absences.mark(row, 0, '')
                self.absences.mark(row, 1, '')
                for issue in issues:
                    if issue.severity == 'ERROR' and person.name in issue.location and f'.absences[{row}]' in issue.location:
                        self.absences.mark(row, 0, issue.hint)
                        self.absences.mark(row, 1, issue.hint)
        self.issues.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            for column, value in enumerate((issue.severity, issue.code, issue.location, issue.current,
                                            issue.expected, issue.hint)):
                self.issues.set_cell(row, column, value, editable=False,
                                     problem=issue.hint if issue.severity == 'ERROR' else '')
        self.issues.resizeColumnsToContents()
        errors = sum(issue.severity == 'ERROR' for issue in issues)
        warnings = sum(issue.severity == 'WARNING' for issue in issues)
        detail = f' · {self.last_generation.precision}' if self.last_generation else ''
        if self.doc.migrated:
            detail += ' · 이전 형식 변환: 참고점수 0, 기존 순위·결과 보존 (저장하면 새 형식)'
        self.status.setText(f'{self.doc.year}년 계획 · ERROR {errors}건 / WARNING {warnings}건{detail}')
        return plan, review

    def _show_matrix(self, plan, issues):
        known = [alias for alias, _ in self.available()]
        used = {alias for person in self.doc.people
                for alias in set(self.person_keys(person)) | set(person.allocations or {})}
        self.column_aliases = list(dict.fromkeys(known)) + sorted(used - set(known))
        aliases = self.column_aliases
        matrix = self.matrix
        self._refreshing = True
        matrix.setColumnCount(len(aliases) + 4)
        matrix.setHorizontalHeaderLabels(['사람'] + aliases + ['사업 합계', '공백', '총합'])
        matrix.setRowCount(len(self.doc.people) + 3)
        person_errors = {name: [] for name in (p.name for p in self.doc.people)}
        project_errors = {alias: [] for alias in aliases}
        for issue in issues:
            if issue.severity != 'ERROR':
                continue
            for name in person_errors:
                if name in issue.location:
                    person_errors[name].append(issue)
            for alias in project_errors:
                if alias in issue.location:
                    project_errors[alias].append(issue)
        totals = {alias: 0 for alias in aliases}
        invalid = set()
        for row, person in enumerate(self.doc.people):
            matrix.set_cell(row, 0, person.name, editable=False)
            values = person.allocations or {}
            total = 0
            for column, alias in enumerate(aliases, 1):
                selected = alias in person.draft
                value = values.get(alias, '')
                units = quantity_units(value) if value != '' else 0
                problem = ''
                if value and units is None:
                    problem = '소수 5자리 이하의 비음수 수량을 입력하세요.'
                    invalid.add(alias)
                elif units is not None:
                    total += units
                    totals[alias] += units
                    if units < 1000 and plan is not None and any(i.code == 'P102' and person.name in i.location
                                                                and alias in i.location for i in issues):
                        problem = '선택 사업은 최소 0.01000이 필요합니다.'
                    elif any(i.code == 'E103' for i in project_errors.get(alias, [])):
                        problem = '이 사업의 연간 할당 합계가 상한을 초과했습니다.'
                    elif any(i.code == 'E201' for i in person_errors.get(person.name, [])):
                        problem = '이 사람의 월별 허용량에 배분할 수 없습니다.'
                if value and not selected:
                    problem = '참여 목록에 없는 결과입니다. 입력의 참여 사업을 확인하세요.'
                matrix.set_cell(row, column, value, editable=selected or alias in values, problem=problem,
                                note='선택한 사업의 연간 참여율을 직접 수정합니다.' if selected else '선택하지 않은 사업')
            absence = None
            if plan is not None:
                from .engine import absence_units
                from .models import Person
                absence = sum(absence_units(Person(person.name, plan.people[person.name].absences, {}),
                                            plan.year, self.plan_path)[0])
            base = len(aliases) + 1
            total_problem = '\n'.join(f'{i.current}\n{i.hint}' for i in person_errors.get(person.name, [])
                                      if i.code in ('E101', 'P201'))
            matrix.set_cell(row, base, amount(total), editable=False, computed=True)
            matrix.set_cell(row, base + 1, amount(absence) if absence is not None else '?',
                            editable=False, computed=True)
            matrix.set_cell(row, base + 2, amount(total + absence) if absence is not None else '?',
                            editable=False, computed=True, problem=total_problem)
        base = len(aliases) + 1
        labels = ('할당 합계', '상한', '잔여')
        raw_projects = {project.alias.strip(): project for project in self.projects()}
        for offset, label in enumerate(labels):
            row = len(self.doc.people) + offset
            matrix.set_cell(row, 0, label, editable=False, computed=True)
            for column, alias in enumerate(aliases, 1):
                if plan is not None and alias in plan.projects:
                    budget = plan.projects[alias].yearly.get(plan.year)
                    limit = budget.limit if budget else 0
                else:
                    project = raw_projects.get(alias)
                    entry = next((entry for entry in project.years if entry.year.strip() == self.doc.year), None) if project else None
                    limit = quantity_units(entry.manpower) if entry else 0
                value = (totals[alias], limit, limit - totals[alias] if limit is not None else None)[offset]
                problem = ('\n'.join(f'{i.current}\n{i.hint}' for i in project_errors.get(alias, [])
                                     if i.code == 'E103')
                           if offset == 0 else '')
                matrix.set_cell(row, column, amount(value) if value is not None and alias not in invalid else '?',
                                editable=False, computed=True, problem=problem)
            for column in range(base, base + 3):
                matrix.set_cell(row, column, '', editable=False)
        matrix.resizeColumnsToContents()
        self._refreshing = False

    def draft_edited(self, item):
        if self._refreshing or item.row() >= len(self.doc.people) or not 1 <= item.column() <= len(self.column_aliases):
            return
        person = self.doc.people[item.row()]
        alias = self.column_aliases[item.column() - 1]
        if alias not in person.draft and alias not in (person.allocations or {}):
            return
        if person.allocations is None:
            person.allocations = {}
        value = item.text().strip()
        if value:
            person.allocations[alias] = value
        else:
            person.allocations.pop(alias, None)
        self.last_generation = None
        self._changed()

    def generate(self):
        plan, _ = self.load_current(include_results=False)
        if plan is None:
            self.refresh_review()
            return False
        if any(person.allocations is not None for person in self.doc.people):
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, '초안 다시 생성', '현재 초안과 수동 수정값을 덮어쓸까요?') != QMessageBox.Yes:
                return False
        generated = generate_plan(plan)
        for person in self.doc.people:
            person.allocations = {alias: amount(value) for alias, value in generated.targets[person.name].items()}
        self._changed()
        self.last_generation = generated
        self.refresh_review()
        return generated.complete

    def confirmed_doc(self):
        from .document import PeopleDoc, PersonEntry
        people = [PersonEntry(person.name, deepcopy(person.allocations or {}), deepcopy(person.absences))
                  for person in self.doc.people]
        return PeopleDoc(self.doc.year, people)
