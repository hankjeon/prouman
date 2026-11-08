"""인력 수량의 모든 입력 경로에서 원문 정밀도를 지킨다."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from allocation.document import (PeopleDoc, PersonEntry, PlanDoc, PlanPersonEntry,
                                 ProjectEntry, YearEntry, dump_people, dump_plan, dump_projects,
                                 quantity_units, read_people, read_plan, read_projects)
from allocation.loading import QUANTITY_HINT, load_inputs, load_plan

PROJECTS = '''projects:
  - name: 사업1
    alias: 사업1
    period: "2027-1-1~2027-12-31"
    yearly:
      2027: {allocated_manpower: LIMIT}
'''
PEOPLE = '''year: 2027
people:
  - name: 홍길동
    allocations: {사업1: VALUE}
'''
PLAN = '''year: 2027
stage: planning
people:
  - name: 홍길동
    draft: {사업1: SCORE}
    allocations: {사업1: VALUE}
'''
LEGACY_PLAN = '''year: 2027
stage: planning
people:
  - name: 홍길동
    priorities: {사업1: 1}
    draft_allocations: {사업1: VALUE}
'''


class QuantityPrecisionTests(unittest.TestCase):
    def load_field(self, field, literal):
        projects = PROJECTS.replace('LIMIT', literal if field == 'budget' else '"1"')
        if field in ('budget', 'actual'):
            people = PEOPLE.replace('VALUE', literal if field == 'actual' else '"1"')
            return load_inputs('projects.yaml', 'people.yaml', {'projects': projects, 'people': people})
        plan = (LEGACY_PLAN.replace('VALUE', literal) if field == 'legacy_result' else
                PLAN.replace('SCORE', literal if field == 'score' else '"1"')
                    .replace('VALUE', literal if field == 'plan_result' else '"1"'))
        return load_plan('projects.yaml', 'plan.yaml', {'projects': projects, 'plan': plan})

    def test_all_quantity_fields_reject_excess_precision_without_rounding(self):
        locations = {'budget': 'allocated_manpower', 'actual': 'allocations',
                     'score': 'draft', 'plan_result': 'allocations', 'legacy_result': 'allocations'}
        for field, location in locations.items():
            for value in ('0.123456', '0.000001', '0.500000', '1.000000', '1000.000010'):
                for quoted in (False, True):
                    with self.subTest(field=field, value=value, quoted=quoted):
                        literal = f'"{value}"' if quoted else value
                        result, issues = self.load_field(field, literal)
                        self.assertIsNone(result)
                        errors = [i for i in issues if i.code == 'E005' and location in i.location]
                        self.assertEqual(len(errors), 1, issues)
                        self.assertEqual(errors[0].current, repr(value))
                        self.assertEqual(errors[0].hint, QUANTITY_HINT)
                        self.assertIn('자동으로 반올림하지 않습니다', errors[0].hint)

    def test_all_quantity_fields_accept_five_places_or_less(self):
        for field in ('budget', 'actual', 'score', 'plan_result', 'legacy_result'):
            for value in ('0', '0.0', '0.00000', '0.00001', '0.12345', '0.5', '1.00000'):
                for quoted in (False, True):
                    with self.subTest(field=field, value=value, quoted=quoted):
                        literal = f'"{value}"' if quoted else value
                        result, issues = self.load_field(field, literal)
                        self.assertIsNotNone(result, issues)
                        self.assertNotIn('E005', [i.code for i in issues])

    def test_gui_document_roundtrip_never_trims_decimal_places(self):
        for value in ('0.123456', '0.500000', '1.000000'):
            with self.subTest(value=value):
                self.assertIsNone(quantity_units(value))
                projects = [ProjectEntry('사업1', '사업1', '2027-1-1', '2027-12-31',
                                         [YearEntry('2027', value)])]
                project_text = dump_projects(projects)
                restored_projects, _ = read_projects(project_text)
                self.assertEqual(restored_projects[0].years[0].manpower, value)
                people = PeopleDoc('2027', [PersonEntry('홍길동', {'사업1': value})])
                people_text = dump_people(people)
                restored_people, _ = read_people(people_text)
                self.assertEqual(restored_people.people[0].allocations['사업1'], value)
                result, issues = load_inputs('p', 'h', {'projects': project_text, 'people': people_text})
                self.assertIsNone(result)
                self.assertEqual(sum(i.code == 'E005' for i in issues), 2)
                plan = PlanDoc('2027', [PlanPersonEntry('홍길동', draft={'사업1': value},
                                                       allocations={'사업1': value})])
                plan_text = dump_plan(plan)
                restored_plan = read_plan(plan_text)
                self.assertEqual(restored_plan.people[0].draft['사업1'], value)
                self.assertEqual(restored_plan.people[0].allocations['사업1'], value)
                result, issues = load_plan('p', 'h', {'projects': project_text, 'plan': plan_text})
                self.assertIsNone(result)
                self.assertEqual(sum(i.code == 'E005' for i in issues), 3)

    def test_cli_rejects_trailing_zero_precision_and_does_not_write_excel(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'projects.yaml').write_text(PROJECTS.replace('LIMIT', '"1"'), encoding='utf-8')
            (folder/'people.yaml').write_text(PEOPLE.replace('VALUE', '1.000000'), encoding='utf-8')
            result = subprocess.run([sys.executable, str(ROOT/'src/main.py'), 'people.yaml',
                                     '--excel', 'result.xlsx'], cwd=folder, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('E005', result.stdout)
            self.assertIn('1.000000', result.stdout)
            self.assertNotIn('개인별 연간 요약', result.stdout)
            self.assertFalse((folder/'result.xlsx').exists())
