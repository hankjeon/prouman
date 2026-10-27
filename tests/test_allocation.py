import copy
import io
import itertools
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

# src 레이아웃을 설치 없이 테스트합니다. CLI는 src/main.py에서 실행합니다.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from allocation.cli import main
from allocation.engine import BASE, calculate, validate_result
from allocation.flow import transport
from allocation.loading import load_inputs
from allocation.models import amount
from allocation.rendering import render_result


def project(alias='사업1', year=2027, excluded=(), limit='10.00000', period=None):
    return dict(name=alias, alias=alias, period=period or f'{year}-01-01~{year}-12-31',
                yearly={year: dict(allocated_manpower=limit, excluded_months=list(excluded))})


def person(targets=None, name='홍길동', absences=()):
    return dict(name=name, allocations=targets if targets is not None else {'사업1': '1.00000'},
                absences=[dict(period=p) for p in absences])


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def write(self, projects, people, year=2027):
        p, h = self.path / 'projects.yaml', self.path / 'people.yaml'
        p.write_text(yaml.safe_dump({'projects': projects}, allow_unicode=True, sort_keys=False), encoding='utf-8')
        h.write_text(yaml.safe_dump({'year': year, 'people': people}, allow_unicode=True, sort_keys=False), encoding='utf-8')
        return p, h

    def run_files(self, paths):
        inputs, issues = load_inputs(*paths)
        return calculate(inputs, issues) if inputs else None, issues

    def ok(self, projects=None, people=None, year=2027):
        projects = projects if projects is not None else [project(year=year)]
        people = people if people is not None else [person()]
        paths = self.write(projects, people, year)
        result, issues = self.run_files(paths)
        self.assertIsNotNone(result, issues)
        self.assertFalse(result.has_errors, result.issues)
        self.assertEqual(validate_result(result), [])
        # 모든 성공 fixture에 대해 반복 및 YAML 순서 변경의 결정성을 확인한다.
        repeated, _ = self.run_files(paths)
        self.assertEqual(result.allocations, repeated.allocations)
        shuffled_projects = copy.deepcopy(list(reversed(projects)))
        shuffled_people = copy.deepcopy(list(reversed(people)))
        for p in shuffled_people:
            p['allocations'] = dict(reversed(list(p['allocations'].items())))
            if 'absences' in p:
                p['absences'].reverse()
        shuffled, _ = self.run_files(self.write(shuffled_projects, shuffled_people, year))
        self.assertIsNotNone(shuffled)
        self.assertFalse(shuffled.has_errors, shuffled.issues)
        self.assertEqual(result.allocations, shuffled.allocations)
        self.assertEqual(result.absence, shuffled.absence)
        # 출력량과 모든 합계는 5자리 문자열로도 검사한다.
        for name, rows in result.allocations.items():
            for m in range(12):
                total = result.absence[name][m] + sum(row[m] for row in rows.values())
                self.assertEqual(amount(total), '0.08334' if m % 3 == 2 else '0.08333')
            for alias, row in rows.items():
                target = result.inputs.people[name].targets[alias]
                self.assertEqual(amount(sum(row)), amount(target))
        return result

    def bad(self, projects=None, people=None, year=2027, code=None):
        projects = projects if projects is not None else [project()]
        people = people if people is not None else [person()]
        result, issues = self.run_files(self.write(projects, people, year))
        issues = result.issues if result else issues
        self.assertTrue(any(i.severity == 'ERROR' for i in issues), issues)
        if code:
            self.assertIn(code, [i.code for i in issues])
        if result:
            self.assertEqual(result.allocations, {})
            self.assertNotIn('개인별 연간 요약', render_result(result))
        return issues

    def test_T01_single_project(self):
        result = self.ok()
        self.assertEqual(result.allocations['홍길동']['사업1'], list(BASE))

    def test_T02_uniform(self):
        r = self.ok([project(), project('사업2')], [person({'사업1': '0.60000', '사업2': '0.40000'})])
        self.assertEqual(r.allocations['홍길동']['사업1'], [5000] * 12)
        self.assertEqual(r.allocations['홍길동']['사업2'], [b - 5000 for b in BASE])

    def test_T03_excluded_quarter(self):
        r = self.ok([project(excluded=[4, 5, 6]), project('사업2')],
                    [person({'사업1': '0.60000', '사업2': '0.40000'})])
        self.assertEqual(r.allocations['홍길동']['사업1'], [6667] * 3 + [0] * 3 + [6667] * 3 + [6666] * 3)

    def test_T04_partial_months(self):
        r = self.ok([project(period='2027-04-15~2027-09-10'), project('사업2')],
                    [person({'사업1': '0.50000', '사업2': '0.50000'})])
        self.assertEqual(r.allocations['홍길동']['사업1'], [0] * 3 + list(BASE[3:9]) + [0] * 3)

    def test_T05_partial_absence(self):
        r = self.ok(people=[person({'사업1': '0.95430'}, absences=['2026-07-15~2026-07-31'])],
                    projects=[project(year=2026)], year=2026)
        self.assertEqual(amount(r.absence['홍길동'][6]), '0.04570')
        self.assertEqual(amount(r.allocations['홍길동']['사업1'][6]), '0.03763')

    def test_T06_leap_year(self):
        r = self.ok([project(year=2028)], [person({'사업1': '0.99713'}, absences=['2028-02-29~2028-02-29'])], 2028)
        self.assertEqual(amount(r.absence['홍길동'][1]), '0.00287')

    def test_T07_overlapping_absences(self):
        r = self.ok(people=[person({'사업1': '0.95968'}, absences=['2027-07-01~2027-07-10', '2027-07-05~2027-07-15'])])
        self.assertEqual(amount(r.absence['홍길동'][6]), '0.04032')
        self.assertEqual([i.code for i in r.issues], ['W004'])

    def test_T08_cross_year_absence(self):
        r = self.ok(people=[person({'사업1': '0.97312'}, absences=['2026-12-20~2027-01-10'])])
        self.assertEqual(amount(r.absence['홍길동'][0]), '0.02688')
        self.assertEqual([i.code for i in r.issues], ['W003'])

    def test_T09_target_sum(self):
        for value in ('0.90000', '1.10000'):
            with self.subTest(value=value):
                self.bad(people=[person({'사업1': value})], code='E101')

    def test_T10_absence_plus_target(self):
        self.bad([project(year=2026)], [person(absences=['2026-07-15~2026-07-31'])], 2026, 'E101')

    def test_T11_no_project_for_excluded_months(self):
        self.bad([project(excluded=[4, 5, 6])], code='E201')

    def test_T12_joint_capacity_conflict(self):
        issues = self.bad([project(excluded=[4, 5, 6]), project('사업2', excluded=[4, 5, 6])],
                         [person({'사업1': '0.50000', '사업2': '0.50000'})], code='E201')
        self.assertIn('0.25000', next(i.current for i in issues if i.code == 'E201'))

    def test_T13_project_limit(self):
        people = [person({'사업1': '0.60000', '사업2': '0.40000'}, name=n) for n in ('가', '나')]
        issues = self.bad([project(limit='1.00000'), project('사업2')], people, code='E103')
        self.assertIn('0.20000', next(i.current for i in issues if i.code == 'E103'))
        self.ok([project(limit='1.50000'), project('사업2')], people)

    def test_T14_zero_or_missing_budget(self):
        for missing in (False, True):
            p = project(limit='0')
            if missing:
                p['yearly'] = {}
            with self.subTest(missing=missing):
                r = self.ok([p, project('사업2')], [person({'사업2': '1.00000'})])
                self.assertEqual([i.code for i in r.issues], ['W005'])
                self.bad([p], code='E102')

    def test_T15_alias_name_reference_integrity(self):
        self.bad([project(), project()], code='E004')
        p = project()
        del p['name']
        self.bad([p], code='E003')
        self.bad(people=[person(), person()], code='E004')
        self.bad(people=[person({'없음': '1'})], code='E007')

    def test_T15_duplicate_yaml_keys(self):
        paths = self.write([project()], [person()])
        for text in ('year: 2027\nyear: 2028\npeople: []\n',
                     'year: 2027\npeople:\n- name: 홍길동\n  allocations:\n    사업1: "1"\n    사업1: "0"\n'):
            with self.subTest(text=text):
                paths[1].write_text(text, encoding='utf-8')
                inputs, issues = load_inputs(*paths)
                self.assertIsNone(inputs)
                self.assertIn('E002', [i.code for i in issues])

    def test_T16_bad_periods(self):
        for period in ('2027-02-29~2027-03-01', '2027-02-02~2027-02-01', '2027/2/1~2027-03-01'):
            p = project(period=period)
            self.bad([p], code='E006')

    def test_date_formats_preserve_project_and_absence_calculation(self):
        formats = ('{y}-{m:02d}-{d:02d}', '{y}-{m}-{d}',
                   '{y}.{m:02d}.{d:02d}', '{y}.{m}.{d}', '{y}.{m}.{d}.')
        baseline = self.ok([project(year=2026)],
                           [person({'사업1': '0.95430'}, absences=['2026-07-15~2026-07-31'])], 2026)
        # 시작·종료 표기를 독립적으로 바꿔 사업과 휴직 모두에 적용한다.
        for start_format, end_format in itertools.product(formats, repeat=2):
            with self.subTest(start=start_format, end=end_format):
                def period(m1, d1, m2, d2):
                    return (start_format.format(y=2026, m=m1, d=d1) + '~'
                            + end_format.format(y=2026, m=m2, d=d2))
                r = self.ok([project(year=2026, period=period(1, 1, 12, 31))],
                            [person({'사업1': '0.95430'}, absences=[period(7, 15, 7, 31)])], 2026)
                self.assertEqual(r.allocations, baseline.allocations)
                self.assertEqual(r.absence, baseline.absence)

    def test_dotted_leap_day_and_equal_endpoints(self):
        r = self.ok([project(year=2028, period='2028.1.1.~2028.12.31.')],
                    [person({'사업1': '0.99713'}, absences=['2028.2.29.~2028.2.29'])], 2028)
        self.assertEqual(amount(r.absence['홍길동'][1]), '0.00287')

    def test_flexible_dates_still_reject_invalid_calendar_and_syntax(self):
        for value in ('2027.2.29', '2026-4-31', '2026.13.1.', '2026.1.0',
                      '2026.1-1', '2026-1.1', '2026-1-1.', '2026.1.1..',
                      '26.1.1', '2026.001.1', '2026/1/1', '2026. 1. 1',
                      '2026.1.1T00:00:00', '2026.1.1junk', ''):
            with self.subTest(value=value):
                self.bad([project(period=f'{value}~2027-12-31')], code='E006')
                self.bad(people=[person(absences=[f'{value}~2027-12-31'])], code='E006')
        self.bad([project(period='2027.3.1.~2027-2-28')], code='E006')
        self.bad(people=[person(absences=['2027.3.1~2027.2.28.'])], code='E006')
        for value in ('2027.1.1', '2027.1.1~2027.2.1~2027.3.1'):
            self.bad([project(period=value)], code='E006')

    def test_T16_bad_quantities(self):
        for value in ('0.123456', '-1', 'NaN', '.inf', '1e0', True, None, '1_000', '+1', ' 1 '):
            with self.subTest(value=value):
                self.bad(people=[person({'사업1': value})], code='E005')
        self.bad([project(excluded=[13])], code='E003')

    def test_T17_optional_absences_and_zero_months(self):
        p = person()
        del p['absences']
        self.ok([project(excluded=[0, 0, 0])], [p])
        self.ok(people=[person(absences=[])])
        self.ok([project(excluded=[0, 4]), project('사업2')],
                [person({'사업1': '0.60000', '사업2': '0.40000'})])

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'src/main.py'), str(self.path / 'people.yaml'), *args],
                              cwd=self.path, capture_output=True, text=True)

    def test_T18_cli_views(self):
        self.write([project(), project('사업2')], [person(), person({'사업2': '1'}, name='김철수')])
        for args, text in (((), '개인별 연간 요약'), (('--person', '홍길동'), '개인 상세: 홍길동'),
                           (('--project', '사업1'), '사업 상세: 사업1'), (('--all',), '개인 상세: 홍길동')):
            with self.subTest(args=args):
                p = self.cli(*args)
                self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
                self.assertIn(text, p.stdout)
                self.assertIn('1.00000', p.stdout)
                for summary in ('개인별 연간 요약', '사업별 연간 요약'):
                    if not args or args == ('--all',):
                        self.assertIn(summary, p.stdout)
                    else:
                        self.assertNotIn(summary, p.stdout)
                if args and args[0] in ('--person', '--project'):
                    self.assertNotIn('김철수', p.stdout)
                    self.assertNotIn('사업2', p.stdout)
                if args and args[0] == '--project':
                    self.assertIn('연간 배정: 10.00000 / 할당: 1.00000 / 잔여: 9.00000', p.stdout)
                if not args:
                    self.assertNotIn('개인 상세:', p.stdout)
                    self.assertNotIn('사업 상세:', p.stdout)
                elif args[0] == '--person':
                    self.assertNotIn('사업 상세:', p.stdout)
                elif args[0] == '--project':
                    self.assertNotIn('개인 상세:', p.stdout)
        for flag in ('--person', '--project'):
            p = self.cli(flag, '없음')
            self.assertEqual(p.returncode, 1)
            self.assertIn('E008', p.stdout)
            self.assertNotIn('개인별 연간 요약', p.stdout)

    def test_all_displays_every_person_and_project_with_section_breaks(self):
        self.write([project(), project('사업2'), project('미할당사업', limit='0')],
                   [person(), person({'사업2': '1'}, name='김철수')])
        p = self.cli('--all')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        headings = ['개인별 연간 요약 (사업 / 공백 / 합계)', '사업별 연간 요약 (배정 / 할당 / 잔여)',
                    '개인 상세: 김철수', '개인 상세: 홍길동',
                    '사업 상세: 미할당사업 (미할당사업)', '사업 상세: 사업1 (사업1)', '사업 상세: 사업2 (사업2)']
        positions = []
        lines = p.stdout.splitlines()
        for heading in headings:
            self.assertEqual(lines.count(heading), 1)
            index = lines.index(heading)
            self.assertEqual(lines[index - 2], '')
            self.assertRegex(lines[index - 1], r'^-{3,}$')
            positions.append(index)
        self.assertEqual(positions, sorted(positions))
        self.assertIn('연간 배정: 0.00000 / 할당: 0.00000 / 잔여: 0.00000', p.stdout)
        self.assertIn('[WARNING W005]', p.stdout)

    def test_selected_views_keep_validation_warnings(self):
        self.write([project(excluded=[4, 4]), project('사업2')],
                   [person({'사업1': '0.60000', '사업2': '0.40000'})])
        for args in (('--person', '홍길동'), ('--project', '사업1')):
            with self.subTest(args=args):
                p = self.cli(*args)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn('[WARNING W001]', p.stdout)
                self.assertIn('ERROR: 0건 / WARNING: 1건', p.stdout)
                self.assertNotIn('개인별 연간 요약', p.stdout)
                self.assertNotIn('사업별 연간 요약', p.stdout)

    def test_T19_warnings(self):
        p = project(excluded=[4, 4])
        p['yearly'][2026] = dict(allocated_manpower='0', excluded_months=[])
        r = self.ok([p, project('사업2')], [person({'사업1': '0.6', '사업2': '0.4'})])
        self.assertEqual(sorted(i.code for i in r.issues), ['W001', 'W002'])

    def test_T20_trim_project_name_alias_and_reference(self):
        p = project('  사업1  ')
        p['name'] = '  연구  지원 사업  '
        r = self.ok([p], [person({' 사업1 ': '1'})])
        self.assertEqual(r.inputs.projects['사업1'].name, '연구  지원 사업')
        self.assertIn('사업1 (연구  지원 사업)', render_result(r))
        self.assertEqual(self.cli('--project', ' 사업1 ').returncode, 0)

    def test_T21_project_whitespace_errors(self):
        for field, value in [('name', ' \t '), ('alias', ' \n '), ('alias', '사업 1'),
                             ('alias', '사업\t1'), ('alias', '사업\n1'), ('alias', '사업\u30001')]:
            with self.subTest(field=field, value=value):
                p = project()
                p[field] = value
                self.bad([p])

    def test_T22_normalized_duplicates(self):
        self.bad([project(), project(' 사업1 ')], code='E004')
        self.bad(people=[person({'사업1': '0.6', ' 사업1 ': '0.4'})], code='E004')

    def test_T23_year_types_and_limits(self):
        for year in (999, 10000, True, '2027', 2027.0):
            with self.subTest(year=year):
                self.bad(year=year, code='E003')
        for year in (1000, 9999):
            self.ok([project(year=year)], [person()], year)
        self.bad([project(excluded=[True])], code='E003')
        p = project()
        p['yearly'] = {True: dict(allocated_manpower='1', excluded_months=[])}
        self.bad([p], code='E003')

    def test_T23_required_fields_and_nonempty_lists(self):
        self.bad(projects=[], code='E003')
        self.bad(people=[], code='E003')
        p = project()
        del p['yearly']
        self.bad([p], code='E003')

    def test_T24_full_absence_empty_targets(self):
        p = project()
        p['yearly'] = {}
        r = self.ok([p], [person({}, absences=['2027-01-01~2027-12-31'])])
        self.assertEqual(r.allocations['홍길동'], {})
        self.assertEqual(r.absence['홍길동'], list(BASE))
        self.bad(people=[person({})], code='E101')

    def test_T25_options_and_monthly_annual_output(self):
        self.write([project()], [person()])
        for args in (('--all', '--person', '홍길동'), ('--all', '--project', '사업1'),
                     ('--person', '홍길동', '--project', '사업1')):
            self.assertEqual(self.cli(*args).returncode, 2)
        for args in (('--all',), ('--person', '홍길동'), ('--project', '사업1')):
            with self.subTest(args=args):
                p = self.cli(*args)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn('분기', p.stdout)
                lines = [line.split(' | ') for line in p.stdout.splitlines() if ' | ' in line]
                self.assertEqual(lines[0], ['사업/구분', '1월', '2월', '3월', '4월', '5월', '6월',
                                            '7월', '8월', '9월', '10월', '11월', '12월', '연간'])
                for row in lines[1:]:
                    self.assertEqual(len(row), 14)
                    if row[0] == '사업/구분':
                        self.assertEqual(row, lines[0])
                        continue
                    self.assertEqual(row[-1], '1.00000' if row[0] != '공백' else '0.00000')
        (self.path / 'projects.yaml').unlink()
        p = self.cli()
        self.assertEqual(p.returncode, 1)
        self.assertIn('E001', p.stdout)

    def test_T26_no_duplicate_warning_for_budget_error(self):
        for missing in (False, True):
            p = project(limit='0')
            if missing:
                p['yearly'] = {}
            issues = self.bad([p], code='E102')
            self.assertEqual(sum(i.code == 'E102' for i in issues), 1)
            self.assertNotIn('W005', [i.code for i in issues])

    def test_yaml_numeric_literals_keep_decimal_precision(self):
        paths = self.write([project()], [person()])
        paths[1].write_text('year: 2027\npeople:\n- name: 홍길동\n  allocations:\n    사업1: 1.00000\n', encoding='utf-8')
        r, issues = self.run_files(paths)
        self.assertIsNotNone(r, issues)
        self.assertFalse(r.has_errors)
        for literal in ('1.000001', '1e0', '.nan', '.inf', '-1', '0x01'):
            paths[1].write_text(f'year: 2027\npeople:\n- name: 홍길동\n  allocations:\n    사업1: {literal}\n', encoding='utf-8')
            inputs, issues = load_inputs(*paths)
            self.assertIsNone(inputs, literal)
            self.assertIn('E005', [i.code for i in issues])

    def test_schema_errors_aggregate(self):
        p = project()
        p['id'] = 1
        p['period'] = '잘못된 날짜'
        del p['yearly'][2027]['allocated_manpower']
        issues = self.bad([p])
        self.assertGreaterEqual(len(issues), 3)

    def test_omitted_excluded_months_matches_empty_list(self):
        explicit = self.ok()
        p = project()
        del p['yearly'][2027]['excluded_months']
        omitted = self.ok([p])
        self.assertEqual(omitted.issues, [])
        self.assertEqual(omitted.inputs.projects['사업1'].yearly[2027].excluded, frozenset())
        self.assertEqual(omitted.allocations, explicit.allocations)
        self.assertEqual(omitted.allocations['홍길동']['사업1'], list(BASE))

    def test_present_excluded_months_still_requires_list(self):
        for value in (None, 4, '4', {}):
            with self.subTest(value=value):
                p = project()
                p['yearly'][2027]['excluded_months'] = value
                self.bad([p], code='E003')

    def test_duplicate_year_values_never_overwrite(self):
        paths = self.write([project()], [person()])
        paths[0].write_text('''projects:
- name: 사업1
  alias: 사업1
  period: "2027-01-01~2027-12-31"
  yearly:
    2027: {allocated_manpower: "1", excluded_months: []}
    02027: {allocated_manpower: "2", excluded_months: []}
''', encoding='utf-8')
        inputs, issues = load_inputs(*paths)
        self.assertIsNone(inputs)
        self.assertIn('E002', [i.code for i in issues])

    def test_yaml_syntax_encoding_and_unsafe_tags(self):
        paths = self.write([project()], [person()])
        for text in ('[unclosed', '!!python/object:os.system {}', '---\n{}\n---\n{}', '? [a, b]\n: c', 'year: &x [*x]\npeople: []'):
            paths[1].write_text(text, encoding='utf-8')
            inputs, issues = load_inputs(*paths)
            self.assertIsNone(inputs, text)
        paths[1].write_bytes(b'\xff')
        self.assertIsNone(load_inputs(*paths)[0])

    def test_out_of_year_and_nested_absence(self):
        r = self.ok(people=[person(absences=['2025-01-01~2025-12-31'])])
        self.assertEqual(r.absence['홍길동'], [0] * 12)
        r = self.ok(people=[person({'사업1': '0.91667'}, absences=[
            '2027-01-01~2027-01-31', '2027-01-05~2027-01-10', '2027-01-10~2027-01-20'])])
        self.assertEqual(r.absence['홍길동'][0], 8333)

    def test_post_validation_rejects_corrupt_result(self):
        r = self.ok()
        r.allocations['홍길동']['사업1'][0] -= 1
        self.assertTrue(validate_result(r))

    def test_offline_execution_without_socket(self):
        self.write([project()], [person()])
        import os
        previous = Path.cwd()
        try:
            os.chdir(self.path)
            with patch('socket.socket', side_effect=AssertionError('외부 통신 불가')), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['people.yaml', '--all']), 0)
            self.assertIn('1.00000', output.getvalue())
        finally:
            os.chdir(previous)


class TransportTests(unittest.TestCase):
    def test_reassignment_needed(self):
        # A를 1월에 우선 확정하면 B가 부족하다. A를 2월로 옮겨야 한다.
        values, shortage, _, _ = transport({'A': 1, 'B': 3}, {'A': {0, 1}, 'B': {0, 2}}, [2, 1, 1])
        self.assertEqual(shortage, 0)
        self.assertEqual(values, {'A': [0, 1, 0], 'B': [2, 0, 1]})

    def test_small_problems_against_exhaustive_oracle(self):
        rng = random.Random(527)
        for case in range(200):
            capacities = [rng.randrange(3) for _ in range(3)]
            total = sum(capacities)
            cuts = sorted([0, total, rng.randrange(total + 1), rng.randrange(total + 1)])
            targets = {chr(65 + p): cuts[p + 1] - cuts[p] for p in range(3)}
            allowed = {alias: {m for m in range(3) if rng.randrange(2)} for alias in targets}
            order = sorted(targets, key=lambda a: (len(allowed[a]), a))
            solutions = []
            def enumerate_rows(index, remaining, rows):
                if index == len(order):
                    if not any(remaining):
                        solutions.append(copy.deepcopy(rows))
                    return
                alias = order[index]
                for row in itertools.product(*(range(c + 1) if m in allowed[alias] else [0]
                                               for m, c in enumerate(remaining))):
                    if sum(row) == targets[alias]:
                        rows[alias] = list(row)
                        enumerate_rows(index + 1, [c - v for c, v in zip(remaining, row)], rows)
            enumerate_rows(0, capacities, {})
            values, shortage, _, _ = transport(targets, allowed, capacities)
            with self.subTest(case=case):
                self.assertEqual(values is not None, bool(solutions))
                if solutions:
                    def deviation(rows):
                        errors = []
                        for alias in order:
                            months = sorted(allowed[alias])
                            if months:
                                q, r = divmod(targets[alias], len(months))
                                errors.extend(abs(rows[alias][m] - q - (j < r)) for j, m in enumerate(months))
                        return tuple(errors)
                    self.assertEqual(deviation(values), min(map(deviation, solutions)))
                    self.assertEqual(shortage, 0)
                else:
                    self.assertGreater(shortage, 0)


if __name__ == '__main__':
    unittest.main()
