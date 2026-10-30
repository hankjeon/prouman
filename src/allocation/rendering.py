from .models import amount


def issue_sort_key(issue):
    # 안정 정렬로 ERROR를 앞에 두고 같은 심각도 안에서는 검증 순서(사업 파일 → 사람 파일 → 계산, 파일 안은 항목 순서)를 유지한다.
    return issue.severity != 'ERROR'


def issue_counts(issues):
    return (sum(i.severity == 'ERROR' for i in issues), sum(i.severity == 'WARNING' for i in issues))


def render_issues(issues):
    lines = []
    for issue in sorted(issues, key=issue_sort_key):
        lines.extend([f'[{issue.severity} {issue.code}] {issue.location}',
                      f'  현재: {issue.current}', f'  요구: {issue.expected}', f'  조치: {issue.hint}'])
    errors, warnings = issue_counts(issues)
    lines.append(f'ERROR: {errors}건 / WARNING: {warnings}건')
    return '\n'.join(lines)


# 텍스트와 엑셀 출력이 함께 쓰는 집계. 모든 값은 0.00001 단위 정수다.
def person_summary(result, name):
    total = sum(sum(row) for row in result.allocations[name].values())
    absence = sum(result.absence[name])
    return total, absence, total + absence


def person_table(result, name):
    rows = result.allocations[name]
    table = [(alias, rows[alias]) for alias in sorted(rows)]
    table.append(('공백', result.absence[name]))
    table.append(('합계', [result.absence[name][m] + sum(row[m] for row in rows.values()) for m in range(12)]))
    return table


def project_summary(result, alias):
    """(사업, 배정 상한, 배정 미등록 여부, 월별 할당 합계)를 반환한다."""
    item = result.inputs.projects[alias]
    budget = item.yearly.get(result.inputs.year)
    totals = [sum(rows.get(alias, [0] * 12)[m] for rows in result.allocations.values()) for m in range(12)]
    return item, budget.limit if budget else 0, budget is None, totals


def table_row(label, values):
    return ' | '.join([label, *(amount(value) for value in values + [sum(values)])])


def render_result(result, person=None, project=None, show_all=False, *, all_people=False, all_projects=False):
    inputs = result.inputs
    lines = [f'관리 연도: {inputs.year}', f'사업 파일: {inputs.project_file}', f'사람 파일: {inputs.people_file}',
             render_issues(result.issues)]
    if result.has_errors:
        return '\n'.join(lines + ['결과: 계산 중단'])
    if person is None and project is None and not all_people and not all_projects:
        lines.extend(['', '-' * 72, '개인별 연간 요약 (사업 / 공백 / 합계)'])
        for name in result.allocations:
            lines.append(f'{name}: ' + ' / '.join(amount(value) for value in person_summary(result, name)))
        lines.extend(['', '-' * 72, '사업별 연간 요약 (배정 / 할당 / 잔여)'])
        for alias in inputs.projects:
            item, limit, missing, totals = project_summary(result, alias)
            total = sum(totals)
            lines.append(f'{alias} ({item.name}): {amount(limit)} / {amount(total)} / {amount(limit - total)}'
                         + (' (배정 미등록)' if missing else ''))
    header = ' | '.join(['사업/구분', *(f'{m}월' for m in range(1, 13)), '연간'])
    names = list(inputs.people) if show_all or all_people else [person] if person is not None else []
    for name in names:
        lines.extend(['', '-' * 72, f'개인 상세: {name}', header])
        lines.extend(table_row(label, values) for label, values in person_table(result, name))
    projects = list(inputs.projects) if show_all or all_projects else [project] if project is not None else []
    for alias in projects:
        item, limit, missing, totals = project_summary(result, alias)
        total = sum(totals)
        lines.extend(['', '-' * 72, f'사업 상세: {alias} ({item.name})',
                      f'연간 배정: {amount(limit)} / 할당: {amount(total)} / 잔여: {amount(limit - total)}'
                      + (' (배정 미등록)' if missing else ''), header])
        for name in sorted(result.allocations):
            values = result.allocations[name].get(alias)
            if values and sum(values) > 0:
                lines.append(table_row(name, values))
        lines.append(table_row('할당', totals))
    return '\n'.join(lines)
