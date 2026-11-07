import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from allocation.document import PlanDoc, PlanPersonEntry, dump_plan, read_plan
from allocation.loading import load_inputs, load_plan
from allocation.models import amount
from allocation.planning import generate_plan, review_plan


def project(alias, limit='2', period='2027-01-01~2027-12-31', excluded=()):
    return {'name': alias, 'alias': alias, 'period': period,
            'yearly': {2027: {'allocated_manpower': limit, 'excluded_months': list(excluded)}}}


def person(name='홍길동', priorities=None, absences=(), draft=None):
    value = {'name': name, 'priorities': priorities if priorities is not None else {'사업1': 1},
             'absences': [{'period': p} for p in absences]}
    if draft is not None:
        value['draft_allocations'] = draft
    return value


class PlanningTests(unittest.TestCase):
    def load(self, projects, people):
        texts = {'projects': yaml.safe_dump({'projects': projects}, allow_unicode=True, sort_keys=False),
                 'plan': yaml.safe_dump({'year': 2027, 'stage': 'planning', 'people': people},
                                        allow_unicode=True, sort_keys=False)}
        plan, issues = load_plan('projects.yaml', 'plan.yaml', texts)
        self.assertIsNotNone(plan, issues)
        return plan

    def test_P01_P02_P03_weights(self):
        projects = [project(alias) for alias in ('사업1', '사업2', '사업3')]
        for priorities, expected in (({'사업1': 1, '사업2': 2}, {'사업1': '0.60000', '사업2': '0.40000'}),
                                     ({'사업1': 1, '사업2': 1}, {'사업1': '0.50000', '사업2': '0.50000'}),
                                     ({'사업1': 1, '사업2': 1, '사업3': 3},
                                      {'사업1': '0.40000', '사업2': '0.40000', '사업3': '0.20000'})):
            with self.subTest(priorities=priorities):
                result = generate_plan(self.load(projects, [person(priorities=priorities)]))
                self.assertTrue(result.complete, result.review.issues)
                self.assertEqual({alias: amount(value) for alias, value in result.targets['홍길동'].items()}, expected)
                self.assertEqual(result.precision, '2자리 사용')

    def test_P04_P05_caps_order_and_unused_limit(self):
        projects = [project('사업1', '0.8'), project('사업2'), project('사업3')]
        people = [person('가', {'사업1': 1, '사업2': 2}), person('나', {'사업1': 1, '사업3': 2})]
        first = generate_plan(self.load(projects, people))
        second = generate_plan(self.load(list(reversed(projects)), list(reversed(people))))
        self.assertTrue(first.complete, first.review.issues)
        self.assertEqual(first.targets, second.targets)
        self.assertEqual(sum(row['사업1'] for row in first.targets.values()), 80000)
        self.assertEqual(first.targets['가']['사업1'], first.targets['나']['사업1'])
        self.assertLess(sum(row.get('사업2', 0) for row in first.targets.values()), 200000)

    def test_P06_P07_partial_and_minimum(self):
        feasible = generate_plan(self.load([project('사업1', excluded=(4, 5, 6)), project('사업2')],
                                           [person(priorities={'사업1': 1, '사업2': 2})]))
        self.assertTrue(feasible.complete, feasible.review.issues)
        self.assertLessEqual(feasible.targets['홍길동']['사업1'], 75000)
        self.assertEqual(sum(feasible.targets['홍길동'].values()), 100000)
        projects = [project('사업1', '0.5', excluded=(4, 5, 6))]
        result = generate_plan(self.load(projects, [person()]))
        self.assertFalse(result.complete)
        self.assertEqual(result.shortages['홍길동'], 50000)
        self.assertTrue(any(issue.code == 'P201' for issue in result.review.issues))
        low = generate_plan(self.load([project('사업1', '0.005')], [person()]))
        self.assertFalse(low.complete)
        self.assertTrue(any(issue.code == 'P102' for issue in low.review.issues))
        minimum_only = generate_plan(self.load([project('사업1', '0.005'), project('사업2')],
                                               [person(priorities={'사업1': 1, '사업2': 2})]))
        self.assertFalse(minimum_only.complete)
        self.assertEqual(minimum_only.shortages['홍길동'], 0)
        self.assertTrue(any(issue.code == 'P102' for issue in minimum_only.review.issues))

    def test_P08_manual_error_and_P09_roundtrip(self):
        plan = self.load([project('사업1', '0.5'), project('사업2')],
                         [person(priorities={'사업1': 1, '사업2': 2},
                                 draft={'사업1': '0.60000', '사업2': '0.40000'})])
        review = review_plan(plan)
        self.assertTrue(review.has_errors)
        self.assertTrue(any(issue.code == 'E103' for issue in review.issues))
        doc = PlanDoc('2027', [PlanPersonEntry('홍길동', {'사업1': '1', '사업2': '2'},
                                              {'사업1': '0.60000', '사업2': '0.40000'})])
        self.assertEqual(read_plan(dump_plan(doc)).people[0].draft_allocations,
                         {'사업1': '0.60000', '사업2': '0.40000'})

    def test_monthly_minimum_warning_allows_plan_confirmation(self):
        plan = self.load([project('사업1', period='2027-01-01~2027-01-31'), project('사업2')],
                         [person(priorities={'사업1': 1, '사업2': 2},
                                 draft={'사업1': '0.08333', '사업2': '0.91667'})])
        review = review_plan(plan)
        self.assertFalse(review.has_errors, review.issues)
        warnings = [issue for issue in review.issues if issue.code == 'W006']
        self.assertEqual(len(warnings), 1)
        self.assertIn('사업2', warnings[0].location)
        self.assertIn('1', warnings[0].location)

    def test_P10_P11_stage_and_schema(self):
        projects = yaml.safe_dump({'projects': [project('사업1')]}, allow_unicode=True)
        plan_text = yaml.safe_dump({'year': 2027, 'stage': 'planning', 'people': [person()]}, allow_unicode=True)
        inputs, issues = load_inputs('projects.yaml', 'plan.yaml', {'projects': projects, 'people': plan_text})
        self.assertIsNone(inputs)
        self.assertTrue(any(issue.code == 'E003' and '.stage' in issue.location for issue in issues))
        confirmed = yaml.safe_dump({'year': 2027, 'stage': 'confirmed', 'people':
                                    [{'name': '홍길동', 'allocations': {'사업1': '1'}}]}, allow_unicode=True)
        self.assertIsNotNone(load_inputs('p', 'h', {'projects': projects, 'people': confirmed})[0])
        self.assertIsNotNone(load_inputs('p', 'h', {'projects': projects,
                                                 'people': confirmed.replace('stage: confirmed\n', '')})[0])
        for bad in (plan_text.replace('priorities:', 'allocations:'),
                    plan_text.replace('사업1: 1', '사업1: 0'),
                    plan_text.replace('priorities:', 'priorities:\n      사업1: 1\n    draft_allocations:\n      사업2: "0.1"\n    ignored:')):
            value, issues = load_plan('p', 'h', {'projects': projects, 'plan': bad})
            self.assertIsNone(value)
            self.assertTrue(any(issue.severity == 'ERROR' for issue in issues))
        repeated = 'year: 2027\nstage: planning\npeople:\n  - name: 홍길동\n    priorities:\n      사업1: 1\n      사업1: 2\n'
        self.assertIsNone(load_plan('p', 'h', {'projects': projects, 'plan': repeated})[0])

    def test_P12_five_digit_for_absence(self):
        result = generate_plan(self.load([project('사업1')],
                                         [person(absences=('2027-07-15~2027-07-31',))]))
        self.assertTrue(result.complete, result.review.issues)
        self.assertEqual(result.targets['홍길동']['사업1'], 95430)
        self.assertEqual(result.precision, '5자리 사용')

    def test_cli_rejects_plan_and_accepts_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'projects.yaml').write_text(yaml.safe_dump({'projects': [project('사업1')]},
                                                                 allow_unicode=True), encoding='utf-8')
            plan_path = folder / 'plan.yaml'
            plan_path.write_text(yaml.safe_dump({'year': 2027, 'stage': 'planning',
                                                 'people': [person()]}, allow_unicode=True), encoding='utf-8')
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            command = [sys.executable, str(ROOT / 'src' / 'main.py'), str(plan_path)]
            rejected = subprocess.run(command, cwd=folder, env=env, text=True, encoding='utf-8',
                                      capture_output=True, check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn('stage', rejected.stdout + rejected.stderr)
            confirmed_path = folder / 'confirmed.yaml'
            confirmed_path.write_text(yaml.safe_dump({'year': 2027, 'stage': 'confirmed',
                'people': [{'name': '홍길동', 'allocations': {'사업1': '1'}}]}, allow_unicode=True), encoding='utf-8')
            accepted = subprocess.run(command[:-1] + [str(confirmed_path)], cwd=folder, env=env,
                                      text=True, encoding='utf-8', capture_output=True, check=False)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)


if __name__ == '__main__':
    unittest.main()
