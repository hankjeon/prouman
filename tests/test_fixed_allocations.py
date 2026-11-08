"""사람별 사업의 12개월 고정값을 검증하고 나머지 사업만 배분한다."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from decimal import Decimal

import yaml
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from allocation.document import DocumentError, dump_people, plan_from_people, read_people
from allocation.engine import BASE, calculate, validate_result
from allocation.loading import load_inputs, load_plan
from allocation.models import amount
from test_allocation import person, project

HALF_YEAR = [amount(v) for v in BASE[:6]] + ['0.00000'] * 6


class FixedAllocationTests(unittest.TestCase):
    def texts(self, people, projects=None):
        return {'projects': yaml.safe_dump({'projects': projects or [project('A'), project('B')]}, allow_unicode=True),
                'people': yaml.safe_dump({'year': 2027, 'people': people}, allow_unicode=True, sort_keys=False)}

    def run_case(self, people, projects=None):
        inputs, issues = load_inputs('projects.yaml', 'people.yaml', self.texts(people, projects))
        self.assertIsNotNone(inputs, issues)
        return calculate(inputs, issues)

    def fixed_person(self):
        value = person({'A': '0.50000', 'B': '0.50000'})
        value['fixed'] = {'B': list(HALF_YEAR)}
        return value

    def assert_error(self, people, code, projects=None):
        inputs, issues = load_inputs('projects.yaml', 'people.yaml', self.texts(people, projects))
        if inputs is not None:
            result = calculate(inputs, issues)
            issues = result.issues
            self.assertTrue(result.has_errors)
            self.assertEqual(result.allocations, {})
        self.assertIn(code, [issue.code for issue in issues], issues)
        return issues

    def test_fixed_months_and_only_free_project_changes(self):
        data = self.fixed_person()
        original = deepcopy(data)
        result = self.run_case([data])
        self.assertFalse(result.has_errors, result.issues)
        self.assertEqual(result.allocations['홍길동']['B'], list(BASE[:6]) + [0] * 6)
        self.assertEqual(result.allocations['홍길동']['A'], [0] * 6 + list(BASE[6:]))
        self.assertEqual(validate_result(result), [])
        self.assertEqual(data, original)
        warnings = [i for i in result.issues if i.code == 'W006']
        self.assertEqual(len(warnings), 6)
        self.assertTrue(all(' / A / ' in i.location for i in warnings))
        self.assertFalse(any(' / B / ' in i.location for i in warnings))

    def test_fixed_zero_and_below_minimum_are_not_increased(self):
        for value in ('0.00000', '0.00001', '0.00005'):
            with self.subTest(value=value):
                units = int(value.replace('.', ''))
                data = person({'A': amount(100000 - units), 'B': value})
                data['fixed'] = {'B': [value] + ['0'] * 11}
                result = self.run_case([data])
                self.assertFalse(result.has_errors, result.issues)
                self.assertEqual(result.allocations['홍길동']['B'], [units] + [0] * 11)
                self.assertFalse(any(i.code == 'W006' for i in result.issues))

    def test_fixed_sum_must_equal_annual_target(self):
        data = self.fixed_person()
        data['fixed']['B'][-1] = '0.00001'
        issues = self.assert_error([data], 'E104')
        issue = next(i for i in issues if i.code == 'E104')
        self.assertIn('0.50001', issue.current)
        self.assertIn('0.50000', issue.current)

    def test_all_projects_can_be_fixed_including_full_absence(self):
        for absent in (False, True):
            with self.subTest(absent=absent):
                row = ([0] * 3 + list(BASE[3:])) if absent else list(BASE)
                data = person({'A': amount(sum(row))}, absences=['2027-1-1~2027-3-31'] if absent else [])
                data['fixed'] = {'A': [amount(v) for v in row]}
                result = self.run_case([data])
                self.assertFalse(result.has_errors, result.issues)
                self.assertEqual(result.allocations['홍길동']['A'], row)

    def test_multiple_fixed_projects_share_monthly_capacity(self):
        data = person({'A': '0.60000', 'B': '0.40000'})
        data['fixed'] = {'A': ['0.05000'] * 12, 'B': ['0.05000'] + ['0.03000'] * 10 + ['0.05000']}
        self.assert_error([data], 'E105')

    def test_forbidden_and_absent_months_cannot_have_positive_fixed_values(self):
        data = person({'A': '0.95000', 'B': '0.05000'})
        data['fixed'] = {'B': ['0.05000'] + ['0'] * 11}
        for b in (project('B', excluded=[1]), project('B', period='2027-7-1~2027-12-31')):
            self.assert_error([data], 'E105', [project('A'), b])
        data['absences'] = [{'period': '2027-1-1~2027-1-31'}]
        data['allocations']['A'] = '0.86667'
        self.assert_error([data], 'E105')

    def test_fixed_can_make_remaining_allocation_impossible(self):
        data = person({'A': '0.08333', 'B': '0.01000', 'C': '0.90667'})
        data['fixed'] = {'B': ['0.01000'] + ['0'] * 11}
        self.assert_error([data], 'E201', [project('A', period='2027-1-1~2027-1-31'), project('B'), project('C')])

    def test_post_validation_detects_fixed_changes_even_when_sums_still_match(self):
        result = self.run_case([self.fixed_person()])
        rows = result.allocations['홍길동']
        rows['B'][0] -= 1
        rows['B'][6] += 1
        rows['A'][0] += 1
        rows['A'][6] -= 1
        self.assertTrue(any('월 고정값 변경' in issue.location for issue in validate_result(result)))

    def test_input_structure_references_and_precision(self):
        misplaced = self.texts([person({'A': '0.5', 'B': '0.5'})])
        misplaced['people'] += 'fixed: {B: [' + ', '.join('"' + v + '"' for v in HALF_YEAR) + ']}\n'
        with self.assertRaisesRegex(DocumentError, '각 사람 아래'):
            read_people(misplaced['people'])
        self.assertIsNone(load_inputs('p', 'h', misplaced)[0])
        for bad in (None, [], {'B': None}, {'B': {}}, {'B': []}, {'B': ['0'] * 11}, {'B': ['0'] * 13}):
            with self.subTest(fixed=bad):
                data = self.fixed_person()
                data['fixed'] = bad
                self.assert_error([data], 'E003')
                with self.assertRaises(DocumentError):
                    read_people(self.texts([data])['people'])
        for bad in ('0.500000', '0.000001', '-1', True, None):
            with self.subTest(value=bad):
                data = self.fixed_person()
                data['fixed']['B'][0] = bad
                self.assert_error([data], 'E005')
        data = self.fixed_person()
        data['fixed'] = {'A': list(HALF_YEAR), ' B ': list(HALF_YEAR), 'B': list(HALF_YEAR)}
        self.assert_error([data], 'E004')
        with self.assertRaises(DocumentError):
            read_people(self.texts([data])['people'])
        data = self.fixed_person()
        data['fixed'] = {'C': ['0'] * 12}
        self.assert_error([data], 'E003')

    def test_exact_duplicate_fixed_keys_are_not_silently_overwritten(self):
        texts = self.texts([self.fixed_person()])
        line = '      B: [' + ', '.join('"' + value + '"' for value in HALF_YEAR) + ']\n'
        texts['people'] = '''year: 2027
people:
  - name: 홍길동
    allocations: {A: "0.5", B: "0.5"}
    fixed:
''' + line + line
        inputs, issues = load_inputs('p', 'h', texts)
        self.assertIsNone(inputs)
        self.assertIn('E002', [i.code for i in issues])
        with self.assertRaises(DocumentError):
            read_people(texts['people'])
        empty_first = texts['people'].replace('    fixed:\n', '    fixed: {}\n    fixed:\n')
        with self.assertRaises(DocumentError):
            read_people(empty_first)

    def test_roundtrip_preserves_fixed_and_invalid_numbers(self):
        for bad in (False, True):
            with self.subTest(bad=bad):
                data = self.fixed_person()
                if bad:
                    data['fixed']['B'][0] = '0.500000'
                doc, dropped = read_people(self.texts([data])['people'])
                self.assertEqual(dropped, [])
                output = dump_people(doc)
                restored, dropped = read_people(output)
                self.assertEqual(restored, doc)
                self.assertEqual(dropped, [])
                self.assertEqual(restored.people[0].fixed['B'], data['fixed']['B'])
                texts = self.texts([data])
                texts['people'] = output
                inputs, issues = load_inputs('p', 'h', texts)
                self.assertEqual(inputs is not None, not bad, issues)
                if inputs is not None:
                    self.assertFalse(calculate(inputs, issues).has_errors)

    def test_fixed_is_per_person_and_order_independent(self):
        fixed = self.fixed_person()
        free = person({'A': '0.5', 'B': '0.5'}, name='다른사람')
        one = self.run_case([fixed, free])
        two = self.run_case([free, fixed], [project('B'), project('A')])
        self.assertFalse(one.has_errors, one.issues)
        self.assertFalse(two.has_errors, two.issues)
        self.assertEqual(one.allocations, two.allocations)
        self.assertTrue(all(v > 0 for v in one.allocations['다른사람']['B']))

    def test_fixed_is_not_carried_to_next_year_plan(self):
        doc, _ = read_people(self.texts([self.fixed_person()])['people'])
        plan = plan_from_people(doc, '2028')
        self.assertEqual(plan.people[0].draft, doc.people[0].allocations)
        self.assertFalse(hasattr(plan.people[0], 'fixed'))
        self.assertEqual(doc.people[0].fixed['B'], HALF_YEAR)

    def test_plan_files_reject_fixed_instead_of_dropping_it(self):
        texts = self.texts([self.fixed_person()])
        plan = self.fixed_person()
        plan['draft'] = plan.pop('allocations')
        plan_text = yaml.safe_dump({'year': 2027, 'stage': 'planning', 'people': [plan]})
        result, issues = load_plan('p', 'h', {'projects': texts['projects'], 'plan': plan_text})
        self.assertIsNone(result)
        self.assertTrue(any(i.code == 'E003' and 'fixed' in i.current for i in issues))

    def test_cli_and_excel_preserve_fixed_monthly_values(self):
        texts = self.texts([self.fixed_person()])
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'projects.yaml').write_text(texts['projects'], encoding='utf-8')
            (folder/'people.yaml').write_text(texts['people'], encoding='utf-8')
            proc = subprocess.run([sys.executable, str(ROOT/'src/main.py'), 'people.yaml',
                                   '--person', '홍길동', '--excel', 'fixed.xlsx'],
                                  cwd=folder, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertNotIn('E104', proc.stdout)
            book = load_workbook(folder/'fixed.xlsx')
            try:
                row = next(row for row in book['개인상세'].values if row[0] == 'B')
                self.assertEqual([format(Decimal(str(v)), '.5f') for v in row[1:13]], HALF_YEAR)
                self.assertEqual(format(Decimal(str(row[13])), '.5f'), '0.50000')
            finally:
                book.close()
