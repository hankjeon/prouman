import argparse
from pathlib import Path

from .engine import calculate
from .loading import load_inputs
from .models import Issue
from .rendering import render_issues, render_result


def excel_path(value):
    if Path(value).suffix.lower() != '.xlsx':
        raise argparse.ArgumentTypeError(f'.xlsx 파일 경로가 필요합니다: {value!r}')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description='한 연도의 인력 배분 및 검증 (오프라인)')
    parser.add_argument('people_file', help='year와 people을 포함한 YAML 파일')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--person', help='정확히 일치하는 사람 이름')
    group.add_argument('--project', help='사업약칭 (앞뒤 공백 제거)')
    group.add_argument('--all', action='store_true', dest='show_all', help='전체 요약과 모든 개인·사업 상세')
    group.add_argument('--all-people', action='store_true', help='전체 개인 상세만 표시')
    group.add_argument('--all-projects', action='store_true', help='전체 사업 상세만 표시')
    parser.add_argument('--excel', type=excel_path, metavar='PATH.xlsx',
                        help='조회 옵션과 관계없이 전체 결과를 엑셀 파일로 저장')
    args = parser.parse_args(argv)
    inputs, issues = load_inputs('projects.yaml', args.people_file)
    if inputs is None:
        print(render_issues(issues))
        print('결과: 계산 중단')
        if args.excel:
            print('엑셀: 오류로 생성하지 않음')
        return 1
    if args.project is not None:
        args.project = args.project.strip()
        if not args.project or any(c.isspace() for c in args.project) or args.project not in inputs.projects:
            issues.append(Issue('ERROR', 'E008', '--project', repr(args.project), '등록된 공백 없는 사업약칭',
                                '사업 파일의 alias를 확인하세요.'))
    if args.person is not None and args.person not in inputs.people:
        issues.append(Issue('ERROR', 'E008', '--person', repr(args.person), '등록된 사람 이름',
                            '사람 파일의 name을 확인하세요.'))
    result = calculate(inputs, issues)
    print(render_result(result, args.person, args.project, args.show_all,
                        all_people=args.all_people, all_projects=args.all_projects))
    if result.has_errors:
        if args.excel:
            print('엑셀: 오류로 생성하지 않음')
        return 1
    if args.excel:
        # openpyxl은 엑셀 저장에만 필요하므로 이때 불러온다.
        from .excel import write_workbook
        try:
            write_workbook(result, args.excel)
        except OSError as exc:
            print(f'엑셀 저장 실패: {args.excel} ({exc.strerror or exc}) 파일이 열려 있으면 닫고 다시 실행하세요.')
            return 1
        print(f'엑셀 저장: {args.excel}')
    return 0
