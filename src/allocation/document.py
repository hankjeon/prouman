"""GUI 표 편집기가 다루는 입력 문서.

화면에 입력한 값은 문자열 그대로 보관하고, 저장·계산할 때만 YAML로 만든다. 만든 YAML은 파일과 똑같이
loading.load_inputs로 검증하므로 편집기는 계산 규칙을 따로 두지 않는다. 다시 쓸 때 원래 파일의 주석과
서식은 유지하지 않는다.
"""
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import yaml

from .engine import absence_units
from .loading import QUANTITY_PATTERN, Loader, parse_date
from .models import Person

HEADER = '# 인력 배분 GUI에서 저장한 파일입니다.'
MONTH_SPLIT = re.compile(r'[\s,]+')


class DocumentError(Exception):
    """표 편집기로 열 수 없는 YAML 구조."""


@dataclass
class YearEntry:
    year: str = ''
    manpower: str = ''
    excluded: str = ''  # 쉼표로 구분한 제외 월, 예: '4, 5, 6'


@dataclass
class ProjectEntry:
    name: str = ''
    alias: str = ''
    start: str = ''
    end: str = ''
    years: list = field(default_factory=list)


@dataclass
class AbsenceEntry:
    start: str = ''
    end: str = ''
    reason: str = ''


@dataclass
class PersonEntry:
    name: str = ''
    allocations: dict = field(default_factory=dict)  # 사업약칭 → 참여율 문자열
    absences: list = field(default_factory=list)


@dataclass
class PeopleDoc:
    year: str = field(default_factory=lambda: str(date.today().year))
    people: list = field(default_factory=list)


# --- YAML 읽기 -------------------------------------------------------------------------------

class _Reader:
    def __init__(self):
        self.dropped = []  # 편집기에 없는 필드·중복 키. 저장하면 사라진다는 안내에 쓴다.

    def parse(self, text):
        loader = Loader(io.StringIO(text))
        try:
            value = loader.get_single_data()
            self.dropped += [f'{line}행의 중복 키 {key}' for line, key in loader.duplicates]
            return value
        except yaml.YAMLError as exc:
            raise DocumentError(f'YAML 문법 오류: {exc}') from exc
        finally:
            loader.dispose()

    def mapping(self, value, where, fields=None):
        if value is None:
            return {}
        if type(value) is not dict:
            raise DocumentError(f'{where}: 항목 묶음(mapping)이어야 합니다.')
        if fields is not None:
            self.dropped += [f'{where}.{key}' for key in value if key not in fields]
        return value

    @staticmethod
    def sequence(value, where):
        if value is None:
            return []
        if type(value) is not list:
            raise DocumentError(f'{where}: 목록이어야 합니다.')
        return value

    @staticmethod
    def scalar(value, where):
        # Number는 YAML 원문을 담은 str이므로 그대로 문자열이 된다.
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            raise DocumentError(f'{where}: 값 하나가 필요합니다.')
        return str(value).lower() if type(value) is bool else str(value)

    def period(self, value, where):
        text = self.scalar(value, where)
        parts = text.split('~')
        return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (text.strip(), '')


def read_projects(text):
    """사업 YAML을 (ProjectEntry 목록, 무시한 항목)으로 읽는다."""
    reader = _Reader()
    root = reader.mapping(reader.parse(text), 'projects.yaml', {'projects'})
    projects = []
    for idx, raw in enumerate(reader.sequence(root.get('projects'), 'projects')):
        where = f'projects[{idx}]'
        item = reader.mapping(raw, where, {'name', 'alias', 'period', 'yearly'})
        project = ProjectEntry(reader.scalar(item.get('name'), where + '.name').strip(),
                               reader.scalar(item.get('alias'), where + '.alias').strip(),
                               *reader.period(item.get('period'), where + '.period'))
        for key, raw_budget in reader.mapping(item.get('yearly'), where + '.yearly').items():
            loc = f'{where}.yearly[{key}]'
            budget = reader.mapping(raw_budget, loc, {'allocated_manpower', 'excluded_months'})
            months = budget.get('excluded_months')
            months = (', '.join(reader.scalar(m, loc) for m in months) if type(months) is list
                      else reader.scalar(months, loc))
            project.years.append(YearEntry(reader.scalar(key, loc),
                                           reader.scalar(budget.get('allocated_manpower'), loc).strip(), months))
        projects.append(project)
    return projects, reader.dropped


def read_people(text):
    """사람 YAML을 (PeopleDoc, 무시한 항목)으로 읽는다."""
    reader = _Reader()
    root = reader.mapping(reader.parse(text), 'people.yaml', {'year', 'people'})
    doc = PeopleDoc(reader.scalar(root.get('year'), 'year'), [])
    for idx, raw in enumerate(reader.sequence(root.get('people'), 'people')):
        where = f'people[{idx}]'
        item = reader.mapping(raw, where, {'name', 'allocations', 'absences'})
        person = PersonEntry(reader.scalar(item.get('name'), where + '.name'))
        for key, value in reader.mapping(item.get('allocations'), where + '.allocations').items():
            alias = reader.scalar(key, where + '.allocations').strip()
            person.allocations[alias] = reader.scalar(value, f'{where}.allocations[{alias}]').strip()
        for j, raw_absence in enumerate(reader.sequence(item.get('absences'), where + '.absences')):
            loc = f'{where}.absences[{j}]'
            absence = reader.mapping(raw_absence, loc, {'period', 'reason'})
            person.absences.append(AbsenceEntry(*reader.period(absence.get('period'), loc + '.period'),
                                                reader.scalar(absence.get('reason'), loc + '.reason')))
        doc.people.append(person)
    return doc, reader.dropped


# --- YAML 쓰기 -------------------------------------------------------------------------------

def _text(value):
    # JSON 문자열은 YAML 큰따옴표 문자열과 호환된다. 숫자·날짜도 따옴표로 감싸 원문 그대로 검증받는다.
    return json.dumps(value, ensure_ascii=False)


def _integer(value):
    # 연도·월은 정수로 써야 한다. 숫자가 아니면 문자열로 써서 검증 오류로 보이게 한다.
    value = value.strip()
    return value if re.fullmatch(r'[0-9]+', value) else _text(value)


def month_tokens(text):
    return [token for token in MONTH_SPLIT.split(text.strip()) if token]


def dump_projects(projects):
    lines = [HEADER]
    if not projects:
        return '\n'.join(lines + ['projects: []']) + '\n'
    lines.append('projects:')
    for project in projects:
        lines += [f'  - name: {_text(project.name)}', f'    alias: {_text(project.alias)}',
                  f'    period: {_text(f"{project.start.strip()}~{project.end.strip()}")}']
        if not project.years:
            lines.append('    yearly: {}')
            continue
        lines.append('    yearly:')
        for entry in project.years:
            lines += [f'      {_integer(entry.year)}:', f'        allocated_manpower: {_text(entry.manpower)}']
            months = month_tokens(entry.excluded)
            if months:
                lines.append(f'        excluded_months: [{", ".join(_integer(m) for m in months)}]')
    return '\n'.join(lines) + '\n'


def dump_people(doc):
    lines = [HEADER, f'year: {_integer(doc.year)}']
    if not doc.people:
        return '\n'.join(lines + ['people: []']) + '\n'
    lines.append('people:')
    for person in doc.people:
        lines.append(f'  - name: {_text(person.name)}')
        if person.absences:
            lines.append('    absences:')
            for absence in person.absences:
                lines.append(f'      - period: {_text(f"{absence.start.strip()}~{absence.end.strip()}")}')
                if absence.reason:
                    lines.append(f'        reason: {_text(absence.reason)}')
        allocations = {alias: value for alias, value in person.allocations.items() if value.strip()}
        if not allocations:
            lines.append('    allocations: {}')
            continue
        lines.append('    allocations:')
        lines += [f'      {_text(alias)}: {_text(value.strip())}' for alias, value in allocations.items()]
    return '\n'.join(lines) + '\n'


# --- 입력 중 확인 -------------------------------------------------------------------------------
# 화면에서 바로 표시하는 안내다. 최종 판정은 생성할 때 load_inputs와 calculate가 한다.

def to_date(text):
    try:
        return parse_date(text.strip())
    except ValueError:
        return None


def quantity_units(text):
    """참여율·배정인력 문자열을 0.00001 단위 정수로 바꾼다. 형식이 틀리면 None."""
    text = text.strip()
    if not QUANTITY_PATTERN.fullmatch(text):
        return None
    whole, _, fraction = text.partition('.')
    return int(whole) * 100000 + int(fraction.ljust(5, '0'))


def valid_year(text):
    text = text.strip()
    return bool(re.fullmatch(r'[0-9]{4}', text)) and 1000 <= int(text) <= 9999


DATE_HINT = '날짜를 YYYY-MM-DD 형식으로 입력하세요 (예: 2026-03-01).'
QUANTITY_HINT = '0 이상, 소수 5자리 이하 숫자를 입력하세요 (예: 0.5).'


def project_problems(projects):
    """(행, 열) → 안내 문구. 열은 사업명·약칭·시작일·종료일 순서다."""
    problems = {}
    aliases = Counter(p.alias.strip() for p in projects)
    for row, project in enumerate(projects):
        if not project.name.strip():
            problems[row, 0] = '사업명을 입력하세요.'
        alias = project.alias.strip()
        if not alias:
            problems[row, 1] = '약칭을 입력하세요.'
        elif any(c.isspace() for c in alias):
            problems[row, 1] = '약칭에는 공백을 넣을 수 없습니다.'
        elif aliases[alias] > 1:
            problems[row, 1] = '다른 사업과 약칭이 같습니다.'
        start, end = to_date(project.start), to_date(project.end)
        if start is None:
            problems[row, 2] = DATE_HINT
        if end is None:
            problems[row, 3] = DATE_HINT
        if start and end and start > end:
            problems[row, 3] = '종료일이 시작일보다 빠릅니다.'
    return problems


def year_problems(entries):
    """(행, 열) → 안내 문구. 열은 연도·배정인력·제외 월 순서다."""
    problems = {}
    years = Counter(entry.year.strip() for entry in entries)
    for row, entry in enumerate(entries):
        if not valid_year(entry.year):
            problems[row, 0] = '4자리 연도를 입력하세요 (예: 2026).'
        elif years[entry.year.strip()] > 1:
            problems[row, 0] = '같은 연도가 이미 있습니다.'
        if quantity_units(entry.manpower) is None:
            problems[row, 1] = QUANTITY_HINT
        if any(not re.fullmatch(r'[0-9]{1,2}', m) or int(m) > 12 for m in month_tokens(entry.excluded)):
            problems[row, 2] = '1~12월을 쉼표로 구분해 입력하세요 (예: 4, 5, 6).'
    return problems


def absence_problems(absences):
    """(행, 열) → 안내 문구. 열은 시작일·종료일·사유 순서다."""
    problems = {}
    for row, absence in enumerate(absences):
        start, end = to_date(absence.start), to_date(absence.end)
        if start is None:
            problems[row, 0] = DATE_HINT
        if end is None:
            problems[row, 1] = DATE_HINT
        if start and end and start > end:
            problems[row, 1] = '종료일이 시작일보다 빠릅니다.'
    return problems


def person_totals(person, year):
    """(사업 합계, 공백) 단위 정수. 계산할 수 없는 값이 있으면 None."""
    units = [quantity_units(value) for value in person.allocations.values() if value.strip()]
    targets = None if None in units else sum(units)
    if not valid_year(year) or absence_problems(person.absences):
        return targets, None
    periods = tuple(sorted((to_date(a.start), to_date(a.end)) for a in person.absences))
    absence, _ = absence_units(Person(person.name, periods, {}), int(year), '')
    return targets, sum(absence)
