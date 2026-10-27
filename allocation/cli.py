import argparse

from .engine import calculate
from .loading import load_inputs
from .models import Issue
from .rendering import render_issues, render_result


def main(argv=None):
    parser = argparse.ArgumentParser(description='한 연도의 인력 배분 및 검증 (오프라인)')
    parser.add_argument('people_file', help='year와 people을 포함한 YAML 파일')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--person', help='정확히 일치하는 사람 이름')
    group.add_argument('--project', help='사업약칭 (앞뒤 공백 제거)')
    group.add_argument('--all', action='store_true', dest='show_all', help='모든 개인 상세')
    args = parser.parse_args(argv)
    inputs, issues = load_inputs('projects.yaml', args.people_file)
    if inputs is None:
        print(render_issues(issues))
        print('결과: 계산 중단')
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
    print(render_result(result, args.person, args.project, args.show_all))
    return 1 if result.has_errors else 0
