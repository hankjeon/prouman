"""엑셀과 GUI가 함께 쓰는 결과 표. 셀 값은 글자(str) 또는 0.00001 단위 수량(int)이다."""
from .rendering import issue_sort_key, person_summary, person_table, project_people, project_summary

MONTHS = [f'{m}월' for m in range(1, 13)]
PROJECT_COLUMNS = ['사업약칭', '사업명', '배정', '할당', '잔여', '비고']


def issue_table(issues):
    rows = [[i.severity, i.code, i.location, i.current, i.expected, i.hint] for i in sorted(issues, key=issue_sort_key)]
    return ['구분', '코드', '위치', '현재', '요구', '조치'], rows


def people_summary_table(result):
    return ['이름', '사업', '공백', '합계'], [[name, *person_summary(result, name)] for name in result.allocations]


def _project_row(result, alias):
    item, limit, missing, totals = project_summary(result, alias)
    total = sum(totals)
    return [alias, item.name, limit, total, limit - total, '배정 미등록' if missing else ''], totals


def project_summary_table(result):
    return PROJECT_COLUMNS, [_project_row(result, alias)[0] for alias in result.inputs.projects]


def person_detail_table(result, name):
    return (['사업/구분', *MONTHS, '연간'],
            [[label, *values, sum(values)] for label, values in person_table(result, name)])


def project_people_table(result, alias):
    rows = [[name, *values, sum(values)] for name, values in project_people(result, alias)]
    totals = project_summary(result, alias)[3]
    rows.append(['합계', *totals, sum(totals)])
    return ['이름/구분', *MONTHS, '연간'], rows


def project_detail_table(result):
    rows = []
    for alias in result.inputs.projects:
        row, totals = _project_row(result, alias)
        rows.append(row + totals)
    return PROJECT_COLUMNS + MONTHS, rows
