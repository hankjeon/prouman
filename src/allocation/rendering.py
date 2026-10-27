from .models import amount


def render_issues(issues):
    lines = []
    for issue in sorted(issues, key=lambda i: (i.severity, i.location, i.code, i.current)):
        lines.extend([f'[{issue.severity} {issue.code}] {issue.location}',
                      f'  현재: {issue.current}', f'  요구: {issue.expected}', f'  조치: {issue.hint}'])
    lines.append(f"ERROR: {sum(i.severity == 'ERROR' for i in issues)}건 / "
                 f"WARNING: {sum(i.severity == 'WARNING' for i in issues)}건")
    return '\n'.join(lines)


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
        for name, rows in result.allocations.items():
            total = sum(sum(row) for row in rows.values())
            absence = sum(result.absence[name])
            lines.append(f'{name}: {amount(total)} / {amount(absence)} / {amount(total + absence)}')
        lines.extend(['', '-' * 72, '사업별 연간 요약 (배정 / 할당 / 잔여)'])
        for alias, item in inputs.projects.items():
            budget = item.yearly.get(inputs.year)
            limit = budget.limit if budget else 0
            total = sum(sum(rows.get(alias, [])) for rows in result.allocations.values())
            lines.append(f'{alias} ({item.name}): {amount(limit)} / {amount(total)} / {amount(limit - total)}'
                         + (' (배정 미등록)' if budget is None else ''))
    header = ' | '.join(['사업/구분', *(f'{m}월' for m in range(1, 13)), '연간'])
    names = list(inputs.people) if show_all or all_people else [person] if person is not None else []
    for name in names:
        lines.extend(['', '-' * 72, f'개인 상세: {name}', header])
        rows = result.allocations[name]
        for alias in sorted(rows):
            lines.append(table_row(alias, rows[alias]))
        lines.append(table_row('공백', result.absence[name]))
        totals = [result.absence[name][m] + sum(row[m] for row in rows.values()) for m in range(12)]
        lines.append(table_row('합계', totals))
    projects = list(inputs.projects) if show_all or all_projects else [project] if project is not None else []
    for alias in projects:
        item = inputs.projects[alias]
        totals = [sum(rows.get(alias, [0] * 12)[m] for rows in result.allocations.values()) for m in range(12)]
        budget = item.yearly.get(inputs.year)
        limit = budget.limit if budget else 0
        total = sum(totals)
        lines.extend(['', '-' * 72, f'사업 상세: {alias} ({item.name})',
                      f'연간 배정: {amount(limit)} / 할당: {amount(total)} / 잔여: {amount(limit - total)}'
                      + (' (배정 미등록)' if budget is None else ''), header])
        lines.append(table_row('할당', totals))
    return '\n'.join(lines)
