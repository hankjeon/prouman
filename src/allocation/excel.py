"""계산 결과를 주제별 4개 시트의 .xlsx 파일로 저장한다. 조회 옵션과 관계없이 전체 결과를 담는다."""
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .rendering import issue_counts, issue_sort_key, person_summary, person_table, project_summary

NUMBER_FORMAT = '0.00000'
MONTHS = [f'{m}월' for m in range(1, 13)]
BOLD = Font(bold=True)


def numbers(*units):
    # 0.00001 단위 정수를 float 없이 Decimal로 바꾼다. 셀에는 정확한 십진 문자열로 기록된다.
    return [Decimal(value).scaleb(-5) for value in units]


class Sheet:
    def __init__(self, sheet):
        self.sheet = sheet

    def row(self, values, bold=False):
        self.sheet.append(values)
        for cell in self.sheet[self.sheet.max_row]:
            if isinstance(cell.value, Decimal):
                cell.number_format = NUMBER_FORMAT
            if bold:
                cell.font = BOLD

    def blank(self):
        self.sheet.append([None])

    def widths(self, *widths):
        for index, width in enumerate(widths, 1):
            self.sheet.column_dimensions[get_column_letter(index)].width = width


def write_workbook(result, path):
    inputs = result.inputs
    book = Workbook()

    check = Sheet(book.active)
    check.sheet.title = '검증'
    errors, warnings = issue_counts(result.issues)
    for label, value in (('관리 연도', inputs.year), ('사업 파일', inputs.project_file),
                         ('사람 파일', inputs.people_file), ('검증 결과', f'ERROR {errors}건 / WARNING {warnings}건')):
        check.row([label, value])
    check.blank()
    check.row(['구분', '코드', '위치', '현재', '요구', '조치'], bold=True)
    for issue in sorted(result.issues, key=issue_sort_key):
        check.row([issue.severity, issue.code, issue.location, issue.current, issue.expected, issue.hint])
    check.widths(12, 8, 30, 40, 30, 40)

    summary = Sheet(book.create_sheet('요약'))
    summary.row(['이름', '사업', '공백', '합계'], bold=True)
    for name in result.allocations:
        summary.row([name, *numbers(*person_summary(result, name))])
    summary.blank()
    summary.row(['사업약칭', '사업명', '배정', '할당', '잔여', '비고'], bold=True)
    for alias in inputs.projects:
        item, limit, missing, totals = project_summary(result, alias)
        summary.row([alias, item.name, *numbers(limit, sum(totals), limit - sum(totals)),
                     '배정 미등록' if missing else None])
    summary.sheet.freeze_panes = 'B2'
    summary.widths(16, 32, 10, 10, 10, 12)

    people = Sheet(book.create_sheet('개인상세'))
    for name in inputs.people:
        people.row([name], bold=True)
        people.row(['사업/구분', *MONTHS, '연간'], bold=True)
        for label, values in person_table(result, name):
            people.row([label, *numbers(*values, sum(values))], bold=label == '합계')
        people.blank()
    people.widths(16, *[10] * 13)

    projects = Sheet(book.create_sheet('사업상세'))
    projects.row(['사업약칭', '사업명', '배정', '할당', '잔여', '비고', *MONTHS], bold=True)
    for alias in inputs.projects:
        item, limit, missing, totals = project_summary(result, alias)
        projects.row([alias, item.name, *numbers(limit, sum(totals), limit - sum(totals)),
                      '배정 미등록' if missing else None, *numbers(*totals)])
    projects.sheet.freeze_panes = 'C2'
    projects.widths(16, 32, 10, 10, 10, 12, *[10] * 12)

    book.save(path)
