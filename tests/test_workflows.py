"""2026-11-07 계획작업 재정의: 새 계약·독립 작업·자료 보호 회귀."""
from copy import deepcopy
import importlib.util
import os
import random
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from allocation.document import (AbsenceEntry, DocumentError, PeopleDoc, PersonEntry, PlanDoc,
                                 PlanPersonEntry, dump_plan, dump_projects, plan_from_people,
                                 read_plan, read_projects)
from allocation.engine import calculate
from allocation.loading import load_inputs, load_plan
from allocation.planning import _weights, generate_plan, review_plan
from allocation.storage import write_bundle
from test_planning import project


def plan_text(people, year=2027):
    return yaml.safe_dump({'year': year, 'stage': 'planning', 'people': people},
                          allow_unicode=True, sort_keys=False)


def project_text(projects):
    return yaml.safe_dump({'projects': projects}, allow_unicode=True, sort_keys=False)


class WorkflowModelTests(unittest.TestCase):
    def load(self, projects, people):
        plan, issues = load_plan('projects.yaml', 'plan.yaml',
                                {'projects': project_text(projects), 'plan': plan_text(people)})
        self.assertIsNotNone(plan, issues)
        return plan

    def test_NP02_NP11_previous_actual_is_only_a_reference_copy(self):
        source = PeopleDoc('2026', [PersonEntry('계속', {'A': '0.6', 'B': '0.4'},
                                              [AbsenceEntry('2026-07-01', '2026-07-31', '휴직')])])
        original = deepcopy(source)
        result = plan_from_people(source, '2027')
        self.assertEqual(result.year, '2027')
        self.assertEqual(result.people[0].draft, source.people[0].allocations)
        self.assertIsNone(result.people[0].allocations)
        self.assertEqual(result.people[0].absences[0].start, '2026-07-01')
        result.people[0].draft['C'] = '0.8'
        self.assertEqual(source, original)
        with self.assertRaises(DocumentError):
            plan_from_people(source, 'not-a-year')

    def test_NP03_missing_budget_includes_unselected_active_projects(self):
        missing = project('B')
        missing['yearly'] = {}
        texts = {'projects': project_text([project('A'), missing]),
                 'plan': plan_text([{'name': '가', 'draft': {'A': '1'}}])}
        plan, issues = load_plan('p', 'h', texts)
        self.assertIsNone(plan)
        self.assertTrue(any(i.code == 'P103' and 'B' in i.location for i in issues))
        # 실적의 미참여 사업 누락 판정은 그대로다.
        inputs, issues = load_inputs('p', 'h', {'projects': texts['projects'],
            'people': yaml.safe_dump({'year': 2027, 'people': [{'name': '가', 'allocations': {'A': '1'}}]})})
        self.assertIsNotNone(inputs, issues)
        self.assertFalse(calculate(inputs, issues).has_errors)
        zero = generate_plan(self.load([project('A', '0')], [{'name': '가', 'draft': {'A': '0'}}]))
        self.assertFalse(zero.complete)
        self.assertEqual(zero.targets['가']['A'], 0)

    def test_NP04_expired_history_is_retained_and_stale_participation_is_not_ignored(self):
        old = project('종료', period='2026-01-01~2026-12-31')
        old['yearly'] = {}
        plan = self.load([project('A'), old], [{'name': '가', 'draft': {'A': '1', '종료': '0'}}])
        self.assertIn('종료', plan.projects)
        result = generate_plan(plan)
        self.assertFalse(result.complete)
        self.assertEqual(result.targets['가']['종료'], 0)

    def test_NP05_NP06_scores_mean_order_not_ratio_and_zero_participates(self):
        plan = self.load([project('A'), project('B')], [{'name': '가', 'draft': {'A': '0.9', 'B': '0.1'}}])
        self.assertEqual(generate_plan(plan).targets['가'], {'A': 60000, 'B': 40000})
        for score, ranks in [('0.8', {'A': 2, 'B': 3, 'C': 1}),
                             ('0', {'A': 1, 'B': 2, 'C': 3})]:
            with self.subTest(score=score):
                plan = self.load([project(a) for a in 'ABC'],
                    [{'name': '가', 'draft': {'A': '0.6', 'B': '0.4', 'C': score}}])
                self.assertEqual(plan.people['가'].priorities, ranks)
                generated = generate_plan(plan)
                self.assertTrue(generated.complete)
                self.assertGreaterEqual(generated.targets['가']['C'], 1000)

    def test_NP07_NP08_empty_or_partial_explicit_priorities(self):
        projects = [project(a) for a in 'ABCD']
        people = [{'name': '가', 'draft': {a: '0' for a in 'ABCD'}}]
        equal = generate_plan(self.load(projects, people))
        self.assertEqual(set(equal.targets['가'].values()), {25000})
        person = {'name': '가', 'draft': {'A': '100', 'B': '99', 'C': '1', 'D': '0'},
                  'priorities': {'C': 1, 'D': 3}}
        plan = self.load(projects, [person])
        self.assertEqual(plan.people['가'].priorities, {'A': 4, 'B': 4, 'C': 1, 'D': 3})
        person['priorities'] = {}
        self.assertEqual(self.load(projects, [person]).people['가'].priorities,
                         {'A': 1, 'B': 2, 'C': 3, 'D': 4})

    def test_NP09_bad_references_and_duplicate_keys(self):
        for person in ({'name': '가', 'draft': {'A': '1'}, 'priorities': {'B': 1}},
                       {'name': '가', 'draft': {'B': '1'}},
                       {'name': '가', 'draft': {'A': '1'}, 'allocations': {'B': '1'}},
                       {'name': '가', 'draft': {'A': '-1'}},
                       {'name': '가', 'draft': {'A': '1'}, 'priorities': {'A': True}}):
            with self.subTest(person=person):
                plan, issues = load_plan('p', 'h', {'projects': project_text([project('A')]),
                                                  'plan': plan_text([person])})
                self.assertIsNone(plan)
                self.assertTrue(any(i.severity == 'ERROR' for i in issues))
        duplicate = 'year: 2027\nstage: planning\npeople:\n- name: 가\n  draft:\n    A: "1"\n    A: "0"\n'
        with self.assertRaises(DocumentError):
            read_plan(duplicate)
        self.assertIsNone(load_plan('p', 'h', {'projects': project_text([project('A')]), 'plan': duplicate})[0])

    def test_NP10_rank_weights_and_numeric_spacing(self):
        self.assertEqual(_weights({'A': 1, 'B': 1, 'C': 3}), {'A': 3, 'B': 3, 'C': 2})
        self.assertEqual(_weights({'A': 1, 'B': 3, 'C': 10}), {'A': 3, 'B': 2, 'C': 1})
        self.assertEqual(_weights({'A': 1, 'B': 2, 'C': 3, 'D': 4}), {'A': 4, 'B': 3, 'C': 2, 'D': 1})

    def test_NP12_caps_months_and_order_independence(self):
        projects = [project('A', '0.7', excluded=(4, 5, 6)), project('B'), project('C')]
        people = [{'name': '가', 'draft': {'A': '9', 'B': '1'}},
                  {'name': '나', 'draft': {'A': '9', 'C': '1'}}]
        first = generate_plan(self.load(projects, people))
        second = generate_plan(self.load(list(reversed(projects)), list(reversed(people))))
        self.assertTrue(first.complete, first.review.issues)
        self.assertEqual(first.targets, second.targets)
        self.assertLessEqual(sum(row.get('A', 0) for row in first.targets.values()), 70000)
        self.assertFalse(first.review.monthly.has_errors)

    def test_NP12_seeded_score_and_partial_rank_order_regression(self):
        rng = random.Random(20261107)
        for case in range(20):
            projects = [project(alias, '1.5', excluded=rng.sample(range(1, 13), 2)) for alias in 'ABCD']
            people = [{'name': str(index), 'draft': {alias: str(rng.randrange(4)) for alias in 'ABCD'}}
                      for index in range(4)]
            for person in people:
                if rng.randrange(2):
                    person['priorities'] = {'D': 1, 'B': 3}
            reversed_people = deepcopy(list(reversed(people)))
            for person in reversed_people:
                person['draft'] = dict(reversed(list(person['draft'].items())))
                if 'priorities' in person:
                    person['priorities'] = dict(reversed(list(person['priorities'].items())))
            with self.subTest(case=case):
                first = generate_plan(self.load(projects, people))
                second = generate_plan(self.load(list(reversed(projects)), reversed_people))
                self.assertEqual(first.targets, second.targets)
                self.assertEqual(first.complete, second.complete)
                for alias in 'ABCD':
                    self.assertLessEqual(sum(rows[alias] for rows in first.targets.values()), 150000)
                if first.complete:
                    self.assertFalse(first.review.monthly.has_errors)
                else:
                    self.assertTrue(first.review.has_errors)
                    self.assertTrue(any(i.code == 'P201' for i in first.review.issues))

    def test_NP13_full_absence_and_precision(self):
        person = {'name': '가', 'draft': {}, 'absences': [{'period': '2027-01-01~2027-12-31'}]}
        result = generate_plan(self.load([project('A')], [person]))
        self.assertTrue(result.complete, result.review.issues)
        self.assertEqual(result.targets['가'], {})
        person = {'name': '가', 'draft': {'A': '1000.00001'},
                  'absences': [{'period': '2027-07-15~2027-07-31'}]}
        result = generate_plan(self.load([project('A')], [person]))
        self.assertEqual(result.targets['가']['A'], 95430)
        self.assertEqual(result.precision, '5자리 사용')

    def test_NP14_invalid_editing_strings_survive_roundtrip(self):
        doc = PlanDoc('2027', [PlanPersonEntry('가', {'A': 'bad-score'}, {'A': 'bad-rank'},
                                               {'A': 'bad-result'})])
        restored = read_plan(dump_plan(doc))
        self.assertEqual(restored.people, doc.people)
        self.assertEqual(restored.people[0].draft['A'], 'bad-score')
        for field in ('draft', 'priorities', 'allocations'):
            data = {'name': '가', 'draft': {'A': '1'}, field: None}
            with self.subTest(field=field), self.assertRaises(DocumentError):
                read_plan(plan_text([data]))

    def test_NP17_legacy_conversion_preserves_results_not_as_scores(self):
        legacy = plan_text([{'name': '가', 'priorities': {'A': 1, 'B': 2},
                             'draft_allocations': {'A': '0.7', 'B': '0.4'}}])
        doc = read_plan(legacy)
        self.assertTrue(doc.migrated)
        self.assertEqual(doc.people[0].draft, {'A': '0', 'B': '0'})
        self.assertEqual(doc.people[0].allocations, {'A': '0.7', 'B': '0.4'})
        canonical = dump_plan(doc)
        self.assertNotIn('draft_allocations:', canonical)
        plan, issues = load_plan('p', 'h', {'projects': project_text([project('A'), project('B')]), 'plan': legacy})
        self.assertIsNotNone(plan, issues)
        self.assertTrue(any(i.code == 'W007' for i in issues))
        self.assertTrue(review_plan(plan).has_errors)


class BundleTests(unittest.TestCase):
    def test_NP17_staging_failure_does_not_touch_either_original(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'projects.yaml'
            first.write_text('original', encoding='utf-8')
            blocked = Path(directory) / 'not-a-folder'
            blocked.write_text('block', encoding='utf-8')
            with self.assertRaises(OSError):
                write_bundle([(first, 'replacement'), (blocked / 'people.yaml', 'people')], set())
            self.assertEqual(first.read_text(encoding='utf-8'), 'original')
            self.assertEqual(list(Path(directory).glob('.prouman-*')), [])

    def test_NP17_rollback_failure_reports_recovery_file(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first', Path(directory) / 'second'
            first.write_text('original-first', encoding='utf-8')
            second.write_text('original-second', encoding='utf-8')
            real_replace, calls = os.replace, []

            def fail_after_first(source, dest):
                calls.append(dest)
                if len(calls) >= 2:
                    raise OSError('injected persistent failure')
                real_replace(source, dest)

            with patch('allocation.storage.os.replace', side_effect=fail_after_first):
                with self.assertRaisesRegex(OSError, '원본 복구 실패'):
                    write_bundle([(first, 'new-first'), (second, 'new-second')], set())
            recovery = list(Path(directory).glob('.prouman-restore-*'))
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_text(encoding='utf-8'), 'original-first')

    def test_NP17_replacement_failure_restores_both_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'projects.yaml', Path(directory) / 'people.yaml'
            first.write_text('old-projects', encoding='utf-8')
            second.write_text('old-people', encoding='utf-8')
            real_replace, calls = os.replace, []

            def fail_second(source, dest):
                calls.append(dest)
                if len(calls) == 2:
                    raise OSError('injected second-file failure')
                real_replace(source, dest)

            with patch('allocation.storage.os.replace', side_effect=fail_second):
                with self.assertRaises(OSError):
                    write_bundle([(first, 'new-projects'), (second, 'new-people')], set())
            self.assertEqual(first.read_text(encoding='utf-8'), 'old-projects')
            self.assertEqual(second.read_text(encoding='utf-8'), 'old-people')
            self.assertEqual(list(Path(directory).glob('.prouman-*')), [])

    def test_NP17_success_keeps_first_backup_and_new_file_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.yaml'
            path.write_text('original', encoding='utf-8')
            backups = set()
            write_bundle([(path, 'first')], backups)
            write_bundle([(path, 'second')], backups)
            self.assertEqual(path.with_suffix('.yaml.bak').read_text(encoding='utf-8'), 'original')
            fresh = Path(directory) / 'new.yaml'
            real_replace = os.replace

            def fail_existing(source, dest):
                if Path(dest) == path.resolve():
                    raise OSError('injected failure')
                real_replace(source, dest)

            with patch('allocation.storage.os.replace', side_effect=fail_existing):
                with self.assertRaises(OSError):
                    write_bundle([(fresh, 'temporary'), (path, 'third')], backups)
            self.assertFalse(fresh.exists())
            self.assertEqual(path.read_text(encoding='utf-8'), 'second')


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget
    from allocation.gui import MainWindow


@unittest.skipUnless(HAS_QT, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class WorkflowGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.settings = QSettings(str(self.path / 'settings.ini'), QSettings.IniFormat)
        self.window = MainWindow(self.settings)
        self.addCleanup(self.window.deleteLater)
        self.warnings = []
        for workspace in self.window.workspaces:
            workspace.warn = lambda title, text: self.warnings.append((title, text))
            workspace.confirm = lambda *_: True
            workspace.ask_export = lambda *_: 'discard'

    def prepare(self):
        workspace = self.window.plan
        entries, _ = read_projects(project_text([project('A'), project('B')]))
        workspace.project_editor.set_projects(entries)
        self.assertTrue(workspace.prepare_plan(PeopleDoc('2026', [PersonEntry('가', {'A': '0.9', 'B': '0.1'})]), '2027'))
        return workspace

    def test_NP01_mode_tabs_menu_and_isolated_project_documents(self):
        window = self.window
        self.assertEqual([window.mode_combo.itemText(i) for i in range(window.mode_combo.count())],
                         ['실적작업', '계획작업'])
        self.assertEqual([window.actual.pages.tabText(i) for i in range(2)], ['입력', '결과'])
        self.assertEqual(window.actual.pages.tabPosition(), QTabWidget.West)
        menus = {a.text(): a.menu() for a in window.menuBar().actions()}
        self.assertEqual([a.text() for a in menus['파일'].actions()],
                         ['새로 만들기', '불러오기', '저장', '실적 파일에서 계획 준비', '내보내기', '종료'])
        self.assertEqual([a.text() for a in menus['보기'].actions()], ['글자 크게', '글자 작게', '기본 크기'])
        plan = self.prepare()
        self.assertEqual(window.actual.project_editor.projects, [])
        window.set_mode(window.PLANNING)
        self.assertEqual(window.mode_combo.currentIndex(), window.PLANNING)
        plan.project_editor.projects[0].name = '계획에서만 수정'
        window.set_mode(window.ACTUAL)
        self.assertEqual(window.mode_combo.currentIndex(), window.ACTUAL)
        self.assertEqual(window.actual.project_editor.projects, [])
        window.set_mode(window.PLANNING)
        self.assertEqual(plan.project_editor.projects[0].name, '계획에서만 수정')
        self.assertEqual(plan.planning_page.doc.people[0].draft['A'], '0.9')

    def test_NP01_actual_mode_combo_and_file_actions_route_to_active_workspace(self):
        window = self.window
        window.show()
        window.mode_combo.setCurrentIndex(window.PLANNING)
        self.assertIs(window.active_workspace, window.plan)
        calls = []
        window.plan.browse_save_plan = lambda: calls.append('save-plan')
        window.plan.browse_export = lambda: calls.append('export-plan')
        window.plan.browse_prepare_plan = lambda: calls.append('prepare-plan') or True
        window.actual.browse_export = lambda: calls.append('export-actual')
        actions = {a.text(): a for a in window.menuBar().actions()[0].menu().actions()}
        self.assertTrue(actions['실적 파일에서 계획 준비'].isEnabled())
        actions['실적 파일에서 계획 준비'].trigger()
        actions['저장'].trigger()
        actions['내보내기'].trigger()
        window.mode_combo.setCurrentIndex(window.ACTUAL)
        self.assertFalse(actions['실적 파일에서 계획 준비'].isEnabled())
        actions['저장'].trigger()
        self.assertEqual(calls, ['prepare-plan', 'save-plan', 'export-plan', 'export-actual'])
        self.assertEqual(window.actual.pages.currentIndex(), 0)
        window.actual.pages.setCurrentIndex(1)
        self.assertTrue(window.actual.issue_view.model().rowCount())

    def test_NP14_NP15_results_persist_and_can_regenerate_invalid_results(self):
        workspace = self.prepare()
        page = workspace.planning_page
        self.assertTrue(page.generate())
        page.matrix.item(0, 1).setText('broken')
        self.assertFalse(page.confirm_button.isEnabled())
        workspace.show_page(workspace.INPUT_PAGE)
        workspace.show_page(workspace.RESULT_PAGE)
        self.assertEqual(page.doc.people[0].allocations['A'], 'broken')
        self.window.set_mode(self.window.PLANNING)
        self.window.set_mode(self.window.ACTUAL)
        self.window.set_mode(self.window.PLANNING)
        self.assertEqual(page.doc.people[0].allocations['A'], 'broken')
        folder = self.path / 'work'
        self.assertTrue(workspace.save_plan(folder))
        saved = read_plan((folder / 'plans/plan_2027.yaml').read_text(encoding='utf-8'))
        self.assertEqual(saved.people[0].allocations['A'], 'broken')
        self.assertEqual(saved.people[0].draft['A'], '0.9')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.assertFalse(page.generate())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.assertTrue(page.generate())
        self.assertEqual(page.doc.people[0].allocations['A'], '0.60000')

    def test_NP16_export_bundle_does_not_modify_plan_or_actual(self):
        workspace = self.prepare()
        page = workspace.planning_page
        self.assertTrue(page.generate())
        work, output = self.path / 'work', self.path / 'output'
        self.assertTrue(workspace.save_plan(work))
        original = (work / 'plans/plan_2027.yaml').read_bytes()
        self.assertFalse(workspace.confirm_plan(work))
        self.assertTrue(workspace.confirm_plan(output), self.warnings)
        self.assertEqual((work / 'plans/plan_2027.yaml').read_bytes(), original)
        data = yaml.safe_load((output / 'people/people_2027.yaml').read_text(encoding='utf-8'))
        self.assertEqual(data['stage'], 'confirmed')
        self.assertEqual(set(data['people'][0]), {'name', 'allocations'})
        self.assertFalse((output / 'plans').exists())
        self.assertEqual(self.window.actual.people_editor.doc.people, [])
        inputs, issues = load_inputs(output / 'projects.yaml', output / 'people/people_2027.yaml')
        self.assertIsNotNone(inputs, issues)
        self.assertFalse(calculate(inputs, issues).has_errors)
        errors, _ = self.window.actual.import_files([str(output / 'people/people_2027.yaml')])
        self.assertEqual(errors, [])
        self.window.actual.show_page(1)
        self.assertIsNotNone(self.window.actual.result)

    def test_NP04_NP11_people_participation_editor_and_expired_choices(self):
        workspace = self.prepare()
        page = workspace.planning_page
        page.priorities.item(0, 1).setText('0.2')
        page.priorities.item(0, 2).setText('1')
        self.assertEqual(page.doc.people[0].draft['A'], '0.2')
        self.assertEqual(page.doc.people[0].priorities, {'A': '1'})
        page.priorities.item(0, 2).setText('')
        self.assertEqual(page.doc.people[0].priorities, {})
        page.add_person()
        page.add_project()
        self.assertEqual(set(page.current().draft.values()), {'0.0'})
        page.remove_person()
        self.assertEqual(len(page.doc.people), 1)
        expired, _ = read_projects(project_text([project('종료', period='2026-01-01~2026-12-31')]))
        workspace.project_editor.projects.extend(expired)
        self.assertNotIn('종료', dict(page.available()))

    def test_NP01_restore_modes_independently_and_close_checks_both(self):
        workspace = self.prepare()
        self.assertTrue(workspace.save_plan(self.path / 'plan'))
        self.window.set_mode(self.window.PLANNING)
        restored = MainWindow(self.settings)
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.mode_stack.currentIndex(), restored.PLANNING)
        self.assertEqual(restored.plan.planning_page.doc.people[0].draft, {'A': '0.9', 'B': '0.1'})
        self.assertEqual(restored.actual.project_editor.projects, [])
        self.window.show()
        self.window.plan.keep_all = lambda *_: False
        self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        self.window.plan.keep_all = lambda *_: True
        self.assertTrue(self.window.close())

    def test_NP14_save_as_replaces_restored_source_paths(self):
        workspace = self.prepare()
        first, second = self.path / 'first', self.path / 'second'
        self.assertTrue(workspace.save_plan(first))
        self.assertTrue(workspace.save_plan(second))
        self.assertEqual(set(workspace.loaded_files),
                         {str(second / 'projects.yaml'), str(second / 'plans/plan_2027.yaml')})
        warnings = []
        with patch('allocation.gui.WorkspaceWindow.warn', side_effect=lambda *args: warnings.append(args)):
            reopened = MainWindow(self.settings)
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(warnings, [])
        self.assertEqual(len(reopened.plan.planning_page.doc.people), 1)
        self.assertEqual(reopened.plan.projects_sources, [str(second / 'projects.yaml')])

    def test_NP17_previous_settings_split_and_legacy_notice(self):
        project_path, legacy_path = self.path / 'projects.yaml', self.path / 'legacy.yaml'
        project_path.write_text(project_text([project('A'), project('B')]), encoding='utf-8')
        legacy_path.write_text(plan_text([{'name': '이전', 'priorities': {'A': 1, 'B': 2},
                                          'draft_allocations': {'A': '0.7', 'B': '0.3'}}]), encoding='utf-8')
        settings = QSettings(str(self.path / 'old-settings.ini'), QSettings.IniFormat)
        settings.setValue('loaded_files', [str(project_path), str(legacy_path)])
        restored = MainWindow(settings)
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.actual.people_editor.doc.people, [])
        self.assertTrue(restored.plan.planning_page.doc.migrated)
        self.assertEqual(restored.plan.planning_page.doc.people[0].allocations, {'A': '0.7', 'B': '0.3'})
        self.assertIn('이전 형식 변환', restored.plan.planning_page.status.text())
        restored.plan.project_editor.projects[0].name = '독립 사본'
        self.assertNotEqual(restored.actual.project_editor.projects[0].name, '독립 사본')


if __name__ == '__main__':
    unittest.main()
