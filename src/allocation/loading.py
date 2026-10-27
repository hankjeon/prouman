"""YAML 숫자를 float로 구성하지 않고 원문으로 검증한다."""
import re
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

import yaml

from .models import Inputs, Issue, Person, Project, YearBudget


class Number(str):
    def __new__(cls, value, kind):
        obj = super().__new__(cls, value)
        obj.kind = kind
        return obj


class Missing:
    def __repr__(self):
        return '<누락>'


MISSING = Missing()


def parse_date(value):
    """하이픈·점 표기를 허용하되 달력 유효성은 date로 검사한다."""
    for pattern in (r'([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})',
                    r'([0-9]{4})\.([0-9]{1,2})\.([0-9]{1,2})\.?'):
        match = re.fullmatch(pattern, value)
        if match:
            return date(*(int(part) for part in match.groups()))
    raise ValueError('지원하지 않는 날짜 형식')


class Loader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.duplicates = []

    def construct_mapping(self, node, deep=False):
        pairs = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in pairs
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    None, None, 'mapping 키는 단일 값이어야 합니다', key_node.start_mark
                ) from exc
            value = self.construct_object(value_node, deep=deep)
            if duplicate:
                self.duplicates.append((key_node.start_mark.line + 1, repr(key)))
            else:
                pairs[key] = value
        return pairs


Loader.add_constructor('tag:yaml.org,2002:int', lambda loader, node: Number(node.value, 'int'))
Loader.add_constructor('tag:yaml.org,2002:float', lambda loader, node: Number(node.value, 'float'))
# 인용하지 않은 날짜는 문자열이 아니다. 날짜 객체로 만들지 않고 자료형 오류로 보고한다.
Loader.add_constructor('tag:yaml.org,2002:timestamp', lambda loader, node: Number(node.value, 'timestamp'))


class Validator:
    def __init__(self):
        self.issues = []

    def error(self, code, where, value, expected, hint='입력 파일의 해당 항목을 수정하세요.'):
        self.issues.append(Issue('ERROR', code, where, repr(value), expected, hint))

    def warning(self, code, where, value, expected, hint):
        self.issues.append(Issue('WARNING', code, where, repr(value), expected, hint))

    def read(self, path):
        try:
            with Path(path).open(encoding='utf-8') as stream:
                loader = Loader(stream)
                try:
                    value = loader.get_single_data()
                    for line, key in loader.duplicates:
                        self.error('E002', f'{path}:{line}', key, '중복 없는 mapping 키')
                    return value
                finally:
                    loader.dispose()
        except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
            self.error('E001', str(path), str(exc), '읽을 수 있는 UTF-8 단일 YAML 문서')
            return MISSING

    def mapping(self, value, where, required, optional=()):
        if type(value) is not dict:
            self.error('E003', where, value, 'mapping')
            return {}
        for key in sorted(required):
            if key not in value:
                self.error('E003', f'{where}.{key}', MISSING, '필수 필드')
        for key in value:
            if type(key) is not str or key not in set(required) | set(optional):
                self.error('E003', where, key, '문서에 정의된 필드')
        return value

    def sequence(self, value, where, nonempty=False):
        if type(value) is not list or (nonempty and not value):
            self.error('E003', where, value, '비어 있지 않은 목록' if nonempty else '목록')
            return []
        return value

    def text(self, value, where, trim=False, alias=False):
        if type(value) is not str or not value.strip():
            self.error('E003', where, value, '비어 있지 않은 문자열')
            return None
        value = value.strip() if trim else value
        if alias and any(c.isspace() for c in value):
            self.error('E004', where, value, '내부 공백 없는 사업약칭')
            return None
        return value

    def integer(self, value, where, low, high):
        if (not isinstance(value, Number) or value.kind != 'int'
                or not re.fullmatch(r'[0-9]+', value)
                or not low <= int(value) <= high):
            self.error('E003', where, value, f'{low}~{high} 범위의 정수 (불리언 불가)')
            return None
        return int(value)

    def quantity(self, value, where):
        if (not (type(value) is str or isinstance(value, Number) and value.kind in ('int', 'float'))
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,5})?', value)):
            self.error('E005', where, value, '소수 5자리 이하의 비음수 십진수 (지수 표기 불가)')
            return None
        with localcontext() as context:
            context.prec = max(28, len(value) + 6)
            return int(Decimal(value) * 100000)

    def period(self, value, where):
        if type(value) is str:
            try:
                start, end = (parse_date(part) for part in value.split('~'))
                if start <= end:
                    return start, end
            except ValueError:
                pass
        self.error('E006', where, value,
                   '실재하는 날짜의 시작일~종료일; YYYY-M-D 또는 YYYY.M.D (끝 점 선택), 시작일 ≤ 종료일')
        return None

    def projects(self, value, path):
        root = self.mapping(value, path, {'projects'})
        projects = {}
        for idx, raw in enumerate(self.sequence(root.get('projects', MISSING), f'{path}.projects', True)):
            where = f'{path}.projects[{idx}]'
            item = self.mapping(raw, where, {'name', 'alias', 'period', 'yearly'})
            name = self.text(item.get('name', MISSING), where + '.name', trim=True)
            alias = self.text(item.get('alias', MISSING), where + '.alias', trim=True, alias=True)
            period = self.period(item.get('period', MISSING), where + '.period')
            yearly = item.get('yearly', MISSING)
            budgets = {}
            if type(yearly) is not dict:
                self.error('E003', where + '.yearly', yearly, '연도 → 배정정보 mapping')
                yearly = {}
            for key, raw_budget in yearly.items():
                loc = f'{where}.yearly[{key}]'
                year = self.integer(key, loc, 1000, 9999)
                budget = self.mapping(raw_budget, loc, {'allocated_manpower'}, {'excluded_months'})
                limit = self.quantity(budget.get('allocated_manpower', MISSING), loc + '.allocated_manpower')
                excluded = set()
                for raw_month in self.sequence(budget.get('excluded_months', []), loc + '.excluded_months'):
                    month = self.integer(raw_month, loc + '.excluded_months', 0, 12)
                    if month:
                        if month in excluded:
                            self.warning('W001', loc, month, '중복 없는 제외 월', '중복 월은 한 번만 적용합니다.')
                        excluded.add(month)
                if year is not None and limit is not None:
                    if year in budgets:
                        self.error('E002', loc, key, '정수 값 기준으로 중복 없는 연도 키')
                    else:
                        budgets[year] = YearBudget(limit, frozenset(excluded))
                    if period and not period[0].year <= year <= period[1].year:
                        self.warning('W002', loc, year, '사업 기간과 겹치는 연도', '기간 밖 연도에는 할당하지 않습니다.')
            if alias in projects:
                self.error('E004', where + '.alias', alias, '공백 제거 후 고유한 사업약칭')
            elif name is not None and alias is not None and period:
                projects[alias] = Project(name, alias, *period, budgets)
        return dict(sorted(projects.items()))

    def people(self, value, path, projects):
        root = self.mapping(value, path, {'year', 'people'})
        year = self.integer(root.get('year', MISSING), path + '.year', 1000, 9999)
        people = {}
        for idx, raw in enumerate(self.sequence(root.get('people', MISSING), path + '.people', True)):
            where = f'{path}.people[{idx}]'
            item = self.mapping(raw, where, {'name', 'allocations'}, {'absences'})
            name = self.text(item.get('name', MISSING), where + '.name')
            absences = []
            for j, raw_absence in enumerate(self.sequence(item.get('absences', []), where + '.absences')):
                loc = f'{where}.absences[{j}]'
                absence = self.mapping(raw_absence, loc, {'period'}, {'reason'})
                period = self.period(absence.get('period', MISSING), loc + '.period')
                if 'reason' in absence and type(absence['reason']) is not str:
                    self.error('E003', loc + '.reason', absence['reason'], '문자열')
                if period:
                    absences.append(period)
            allocations = item.get('allocations', MISSING)
            targets = {}
            if type(allocations) is not dict:
                self.error('E003', where + '.allocations', allocations, '사업약칭 → 연간 목표 mapping')
                allocations = {}
            for raw_alias, raw_quantity in allocations.items():
                loc = f'{where}.allocations[{raw_alias}]'
                alias = self.text(raw_alias, loc, trim=True, alias=True)
                quantity = self.quantity(raw_quantity, loc)
                if alias is not None:
                    if alias in targets:
                        self.error('E004', loc, alias, '공백 제거 후 중복 없는 사업약칭')
                    elif quantity is not None:
                        targets[alias] = quantity
                    if alias not in projects:
                        self.error('E007', loc, alias, '사업 파일에 등록된 사업약칭')
            if name in people:
                self.error('E004', where + '.name', name, '고유한 사람 이름')
            elif name is not None:
                people[name] = Person(name, tuple(sorted(absences)), dict(sorted(targets.items())))
        return year, dict(sorted(people.items()))


def load_inputs(project_file='projects.yaml', people_file=None):
    validator = Validator()
    projects = validator.projects(validator.read(project_file), str(project_file))
    year, people = validator.people(validator.read(people_file), str(people_file), projects)
    inputs = None
    if not any(issue.severity == 'ERROR' for issue in validator.issues):
        inputs = Inputs(year, projects, people, str(project_file), str(people_file))
    return inputs, validator.issues
