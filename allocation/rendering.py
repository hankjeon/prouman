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
    quarters = [sum(values[q:q + 3]) for q in (0, 3, 6, 9)]
    return ' | '.join([label, *(amount(value) for value in values + quarters + [sum(values)])])


def render_result(result, person=None, project=None, show_all=False):
    inputs = result.inputs
    lines = [f'관리 연도: {inputs.year}', f'사업 파일: {inputs.project_file}', f'사람 파일: {inputs.people_file}',
             render_issues(result.issues)]
    if result.has_errors:
        return '\n'.join(lines + ['결과: 계산 중단'])
    lines.append('개인별 연간 요약 (사업 / 공백 / 합계)')
    for name, rows in result.allocations.items():
        total = sum(sum(row) for row in rows.values())
        absence = sum(result.absence[name])
        lines.append(f'{name}: {amount(total)} / {amount(absence)} / {amount(total + absence)}')
    lines.append('사업별 연간 요약 (배정 / 할당 / 잔여)')
    for alias, item in inputs.projects.items():
        budget = item.yearly.get(inputs.year)
        limit = budget.limit if budget else 0
        total = sum(sum(rows.get(alias, [])) for rows in result.allocations.values())
        lines.append(f'{alias} ({item.name}): {amount(limit)} / {amount(total)} / {amount(limit - total)}'
                     + (' (배정 미등록)' if budget is None else ''))
    header = ' | '.join(['사업/구분', *(f'{m}월' for m in range(1, 13)), '1분기', '2분기', '3분기', '4분기', '연간'])
    names = list(inputs.people) if show_all else [person] if person is not None else []
    for name in names:
        lines.extend([f'개인 상세: {name}', header])
        rows = result.allocations[name]
        for alias in sorted(rows):
            lines.append(table_row(alias, rows[alias]))
        lines.append(table_row('공백', result.absence[name]))
        totals = [result.absence[name][m] + sum(row[m] for row in rows.values()) for m in range(12)]
        lines.append(table_row('합계', totals))
    if project is not None:
        item = inputs.projects[project]
        lines.extend([f'사업 상세: {project} ({item.name})', header])
        totals = [sum(rows.get(project, [0] * 12)[m] for rows in result.allocations.values()) for m in range(12)]
        lines.append(table_row('할당', totals))
    return '\n'.join(lines)
