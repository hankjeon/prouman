import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .flow import transport
from .models import Issue, Result, amount

BASE = tuple(8334 if month % 3 == 0 else 8333 for month in range(1, 13))


def allowed_months(project, year):
    budget = project.yearly.get(year)
    if budget is None:
        return set()
    return {month - 1 for month in range(1, 13)
            if month not in budget.excluded
            and project.start <= date(year, month, calendar.monthrange(year, month)[1])
            and project.end >= date(year, month, 1)}


def absence_units(person, year, path):
    issues = []
    start_year, end_year = date(year, 1, 1), date(year, 12, 31)
    merged = []
    # 중복 경고는 원래 기간을 기준으로 판단하고, 계산은 연도 교집합을 쓴다.
    last_end = None
    for start, end in sorted(person.absences):
        where = f'{path}: {person.name} / {start}~{end}'
        if last_end is not None and start <= last_end:
            issues.append(Issue('WARNING', 'W004', where, f'{start}~{end}', '중복 없는 공백 날짜',
                                '겹치는 날짜는 합집합에서 한 번만 계산합니다.'))
        last_end = max(last_end, end) if last_end is not None else end
        if start < start_year or end > end_year:
            issues.append(Issue('WARNING', 'W003', where, f'{start}~{end}', str(year),
                                '대상 연도와 겹치는 날짜만 계산합니다.'))
        start, end = max(start, start_year), min(end, end_year)
        if start > end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    values = []
    for month in range(1, 13):
        days = calendar.monthrange(year, month)[1]
        first, last = date(year, month, 1), date(year, month, days)
        absent_days = sum(max(0, (min(end, last) - max(start, first)).days + 1) for start, end in merged)
        value = (Decimal(BASE[month - 1]) * absent_days / days).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        values.append(int(value))
    return values, issues


def calculate(inputs, issues=()):
    result = Result(inputs, list(issues))
    year = inputs.year
    for name, person in inputs.people.items():
        absence, warnings = absence_units(person, year, inputs.people_file)
        result.absence[name] = absence
        result.issues.extend(warnings)
        total = sum(person.targets.values()) + sum(absence)
        if total != 100000:
            result.issues.append(Issue('ERROR', 'E101', f'{inputs.people_file}: {name} / {year}',
                f'사업 {amount(sum(person.targets.values()))} + 공백 {amount(sum(absence))} = {amount(total)}',
                f'1.00000 (차이 {amount(total - 100000)})', '공백을 반영한 연간 사업 목표를 확인하세요.'))
    for alias, project in inputs.projects.items():
        total = sum(person.targets.get(alias, 0) for person in inputs.people.values())
        budget = project.yearly.get(year)
        where = f'{inputs.project_file}: {alias} / {year}'
        if budget is None or budget.limit == 0:
            status = '미등록' if budget is None else '0'
            result.issues.append(Issue('ERROR' if total else 'WARNING', 'E102' if total else 'W005',
                where, f'배정 {status}, 목표 {amount(total)}', '양수 목표가 있으면 양수 배정정보 필요',
                '해당 연도 배정정보를 확인하세요. 목표가 없으면 계산을 계속합니다.'))
        elif total > budget.limit:
            result.issues.append(Issue('ERROR', 'E103', where,
                f'목표 {amount(total)}, 상한 {amount(budget.limit)}, 초과 {amount(total - budget.limit)}',
                '목표 합계 ≤ 배정 상한', '입력 목표 또는 사업 배정정보를 확인하세요.'))
    for name, person in inputs.people.items():
        capacities = [base - absence for base, absence in zip(BASE, result.absence[name])]
        allowed = {alias: allowed_months(inputs.projects[alias], year) &
                   {m for m, capacity in enumerate(capacities) if capacity > 0} for alias in person.targets}
        if sum(person.targets.values()) != sum(capacities):
            continue  # 합계가 잘못된 입력의 배분 가능성 진단은 의미가 없다.
        values, shortage, cut_projects, cut_months = transport(person.targets, allowed, capacities)
        if values is None:
            result.issues.append(Issue('ERROR', 'E201', f'{inputs.people_file}: {name} / {year}',
                f'목표 {amount(sum(person.targets.values()))}, 부족 {amount(shortage)}; '
                f'충돌 사업 {cut_projects}, 허용 월 {cut_months}',
                '모든 사업 목표를 허용 월의 개인 가용량에 배분 가능',
                '충돌 사업의 목표·수행 기간·제외 월·공백을 확인하세요.'))
        else:
            result.allocations[name] = values
    if not result.has_errors:
        result.issues.extend(validate_result(result))
    if result.has_errors:
        result.allocations.clear()
    return result


def validate_result(result):
    """출력 직전 해에서 독립적으로 합계와 모든 필수 제약을 재검증한다."""
    inputs = result.inputs
    violations = []
    project_totals = {alias: 0 for alias in inputs.projects}
    for name, person in inputs.people.items():
        rows = result.allocations.get(name, {})
        absence = result.absence.get(name, [])
        if set(rows) != set(person.targets) or len(absence) != 12:
            violations.append(f'{name}: 사업 집합 또는 공백 월 수 불일치')
            continue
        if any(type(v) is not int or not 0 <= v <= BASE[m] for m, v in enumerate(absence)):
            violations.append(f'{name}: 공백 범위·단위 불일치')
        for alias, row in rows.items():
            allowed = allowed_months(inputs.projects[alias], inputs.year)
            if len(row) != 12 or any(type(v) is not int or v < 0 or (m not in allowed and v != 0)
                                     for m, v in enumerate(row)):
                violations.append(f'{name}/{alias}: 월 수·범위·단위·금지 월 불일치')
                continue
            if sum(row) != person.targets[alias]:
                violations.append(f'{name}/{alias}: 고정 연간 목표 불일치')
            project_totals[alias] += sum(row)
        if any(len(row) != 12 for row in rows.values()):
            continue
        totals = [absence[m] + sum(row[m] for row in rows.values()) for m in range(12)]
        if tuple(totals) != BASE or any(sum(totals[q:q + 3]) != 25000 for q in (0, 3, 6, 9)) or sum(totals) != 100000:
            violations.append(f'{name}: 월·분기·연간 합계 불일치')
    for alias, total in project_totals.items():
        budget = inputs.projects[alias].yearly.get(inputs.year)
        if total > (budget.limit if budget else 0):
            violations.append(f'{alias}: 사업 상한 초과')
    return [Issue('ERROR', 'E301', location, '내부 계산 제약 위반', '모든 사후 제약 충족',
                  '결과를 폐기합니다. 입력과 오류 내용을 개발자에게 전달하세요.') for location in violations]
