import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from allocation.document import (PeopleDoc, PersonEntry, PlanDoc, PlanPersonEntry, ProjectEntry,
                                 YearEntry, dump_people, dump_plan, dump_projects, read_people)
from allocation.storage import write_bundle
from allocation.text_encoding import ENCODINGS, decode_yaml, encode_yaml


def projects_text():
    return dump_projects([ProjectEntry('연구 사업', '연구', '2027-01-01', '2027-12-31',
                                       [YearEntry('2027', '2')])])


def people_text(name='홍길동'):
    return dump_people(PeopleDoc('2027', [PersonEntry(name, {'연구': '1'})]))


class EncodingTests(unittest.TestCase):
    def test_auto_and_explicit_round_trips(self):
        for code in ENCODINGS:
            with self.subTest(code=code):
                data = encode_yaml(people_text(), code)
                decoded = decode_yaml(data, code)
                self.assertEqual(decoded.text, people_text())
                self.assertEqual(decoded.encoding, code)
                self.assertEqual(encode_yaml(decoded.text, decoded.encoding), data)
                auto = decode_yaml(data)
                self.assertEqual(auto.text, people_text())
                self.assertEqual(auto.encoding, 'cp949' if code == 'euc-kr' else code)

    def test_ambiguity_is_reported_without_false_euckr_detection(self):
        ascii_text = decode_yaml(b'year: 2027\n')
        self.assertEqual(ascii_text.encoding, 'utf-8')
        self.assertIn('ASCII', ascii_text.note)
        shared = decode_yaml('홍길동'.encode('euc-kr'))
        self.assertEqual(shared.encoding, 'cp949')
        self.assertIn('공통', shared.note)
        self.assertEqual(decode_yaml('힣'.encode('cp949')).encoding, 'cp949')

    def test_explicit_wrong_encoding_and_unrepresentable_text_fail_strictly(self):
        with self.assertRaises(UnicodeDecodeError):
            decode_yaml('홍길동'.encode('cp949'), 'utf-8')
        with self.assertRaises(UnicodeDecodeError):
            decode_yaml(b'\xff')
        for code in ('cp949', 'euc-kr'):
            with self.assertRaises(UnicodeEncodeError):
                encode_yaml('사람😀', code)
        with self.assertRaises(ValueError):
            decode_yaml(b'abc', 'unknown')
        # 명시적 UTF-8 선택은 읽기 BOM을 제거하고 저장에서는 BOM 없이 쓴다.
        decoded = decode_yaml(b'\xef\xbb\xbfyear: 2027\n', 'utf-8')
        self.assertEqual(encode_yaml(decoded.text, decoded.encoding), b'year: 2027\n')

    def test_bundle_accepts_bytes_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'p.yaml'
            old = encode_yaml(projects_text(), 'cp949')
            new = encode_yaml(projects_text(), 'utf-8-sig')
            path.write_bytes(old)
            backups = set()
            write_bundle([(path, new)], backups)
            self.assertEqual(path.read_bytes(), new)
            self.assertEqual(path.with_name('p.yaml.bak').read_bytes(), old)
            write_bundle([(path, b'next')], backups)
            self.assertEqual(path.with_name('p.yaml.bak').read_bytes(), old)

    def test_bundle_bytes_replacement_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'p', Path(directory) / 'h'
            originals = [encode_yaml('연구', 'cp949'), encode_yaml('사람', 'utf-8-sig')]
            for path, data in zip((first, second), originals):
                path.write_bytes(data)
            replace = os.replace
            calls = []

            def fail_second(source, destination):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError('교체 실패')
                replace(source, destination)

            backups = set()
            with patch('allocation.storage.os.replace', side_effect=fail_second):
                with self.assertRaises(OSError):
                    write_bundle([(first, b'one'), (second, b'two')], backups)
            self.assertEqual([p.read_bytes() for p in (first, second)], originals)
            self.assertEqual(backups, set())
            self.assertEqual(list(Path(directory).glob('.prouman-*')), [])

    def test_cli_reads_mixed_bom_and_legacy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'projects.yaml').write_bytes(encode_yaml(projects_text(), 'utf-8-sig'))
            (folder / 'people.yaml').write_bytes(encode_yaml(people_text(), 'euc-kr'))
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, str(ROOT / 'src/main.py'), 'people.yaml', '--all'],
                                    cwd=folder, env=env, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('홍길동', result.stdout)


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
    from allocation.encoding_ui import EncodingFileDialog, choose_files, choose_folder
    from allocation.gui import MainWindow, WorkspaceWindow


@unittest.skipUnless(HAS_QT, 'PySide6가 없습니다.')
class EncodingGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.warnings = []
        self.windows = []

    def window(self, planning=False, settings=None):
        settings = settings or QSettings(str(self.path / f'settings-{len(self.windows)}.ini'), QSettings.IniFormat)
        window = WorkspaceWindow(settings, planning=planning)
        window.warn = lambda title, text: self.warnings.append((title, text))
        window.ask_export = lambda *_: 'discard'
        window.confirm = lambda *_: False
        self.windows.append(window)
        self.addCleanup(window.deleteLater)
        return window

    def inputs(self, project_code='utf-8', person_code='utf-8', name='홍길동', folder='source'):
        directory = self.path / folder
        directory.mkdir(parents=True, exist_ok=True)
        p, h = directory / 'projects.yaml', directory / 'people_2027.yaml'
        p.write_bytes(encode_yaml(projects_text(), project_code))
        h.write_bytes(encode_yaml(people_text(name), person_code))
        return p, h

    def test_gui_preserves_all_encodings_bom_and_fixed_round_trip(self):
        for code in ENCODINGS:
            with self.subTest(code=code):
                p, h = self.inputs(code, code, folder=code)
                w = self.window()
                self.assertEqual(w.import_files([p, h], encoding=code), ([], []))
                person = w.people_docs['2027'].people[0]
                person.fixed['연구'] = ['0.08333', '0.08333', '0.08334'] * 4
                output = self.path / (code + '-output')
                self.assertTrue(w.export_all(output), self.warnings)
                saved = output / 'people/people_2027.yaml'
                expected = dump_people(w.people_docs['2027'])
                self.assertEqual(saved.read_bytes(), encode_yaml(expected, code))
                self.assertEqual(read_people(decode_yaml(saved.read_bytes(), code).text)[0], w.people_docs['2027'])
                self.assertIn(ENCODINGS[code], w.encoding_label.text())

    def test_independent_project_people_encodings_and_existing_target_priority(self):
        p, h = self.inputs('utf-8-sig', 'cp949')
        w = self.window()
        self.assertEqual(w.import_files([p, h]), ([], []))
        output = self.path / 'output'
        self.assertTrue(w.export_all(output))
        self.assertTrue((output / 'projects.yaml').read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertEqual((output / 'people/people_2027.yaml').read_bytes(),
                         encode_yaml(dump_people(w.people_docs['2027']), 'cp949'))
        # 기존 대상의 인코딩을 원본 문서보다 우선하고 백업의 원본 바이트를 유지한다.
        target = output / 'projects.yaml'
        original = encode_yaml(projects_text(), 'cp949')
        target.write_bytes(original)
        w.read_encodings.pop(str(target.resolve()), None)
        self.assertTrue(w.export_all(output))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.with_name('projects.yaml.bak').read_bytes(), original)

    def test_manual_euckr_survives_restart_and_wrong_encoding_does_not_change_document(self):
        p, h = self.inputs('euc-kr', 'euc-kr')
        settings = QSettings(str(self.path / 'restart.ini'), QSettings.IniFormat)
        w = self.window(settings=settings)
        self.assertEqual(w.import_files([p, h], encoding='euc-kr'), ([], []))
        original = w.people_docs['2027']
        errors, _ = w.import_files([h], encoding='utf-8')
        self.assertTrue(errors)
        self.assertIs(w.people_docs['2027'], original)
        self.assertEqual(w.read_encodings[str(h.resolve())], 'euc-kr')
        restored = self.window(settings=settings)
        self.assertEqual(restored.document_encodings['people:2027'], {'euc-kr'})
        self.assertEqual(restored.people_docs['2027'], original)

    def test_mixed_sources_require_choice_or_explicit_output_encoding(self):
        p, h = self.inputs('utf-8', 'cp949')
        second = self.path / 'other.yaml'
        second.write_bytes(encode_yaml(people_text('김철수'), 'utf-8-sig'))
        w = self.window()
        self.assertEqual(w.import_files([p, h, second]), ([], []))
        output = self.path / 'mixed'
        self.assertFalse(w.export_all(output))
        self.assertFalse(output.exists())
        with patch.object(QInputDialog, 'getItem', return_value=('UTF-8', False)):
            self.assertFalse(w.export_all(output, interactive=True))
        self.assertFalse(output.exists())
        with patch.object(QInputDialog, 'getItem', return_value=('UTF-8 BOM', True)):
            self.assertTrue(w.export_all(output, interactive=True))
        self.assertTrue((output / 'people/people_2027.yaml').read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertEqual(w.document_encodings['people:2027'], {'utf-8-sig'})

    def test_unrepresentable_text_preflight_cancel_and_utf8_confirmation(self):
        p, h = self.inputs('cp949', 'cp949')
        w = self.window()
        w.import_files([p, h])
        w.people_docs['2027'].people[0].name = '사람😀'
        w.people_dirty['2027'] = True
        originals = [p.read_bytes(), h.read_bytes()]
        metadata = dict(w.document_encodings)
        output = self.path / 'blocked'
        self.assertFalse(w.export_all(output))
        self.assertFalse(output.exists())
        self.assertEqual(w.document_encodings, metadata)
        self.assertTrue(w.people_dirty['2027'])
        self.assertFalse(w.export_all(output, interactive=True))
        self.assertFalse(output.exists())
        w.confirm = lambda *_: True
        self.assertTrue(w.export_all(output, interactive=True))
        saved = output / 'people/people_2027.yaml'
        self.assertIn('😀', saved.read_text(encoding='utf-8'))
        self.assertEqual(w.document_encodings['people:2027'], {'utf-8'})
        self.assertEqual([p.read_bytes(), h.read_bytes()], originals)

    def test_preflight_preserves_existing_bundle_and_dirty_state(self):
        p, h = self.inputs('cp949', 'cp949')
        w = self.window()
        w.import_files([p, h])
        output = self.path / 'existing'
        self.assertTrue(w.export_all(output))
        paths = [output / 'projects.yaml', output / 'people/people_2027.yaml']
        original = [path.read_bytes() for path in paths]
        w.project_editor.projects[0].name = '새 이름'
        w.people_docs['2027'].people[0].name = '😀'
        w.projects_dirty = True
        w.people_dirty['2027'] = True
        choices = dict(w.read_encodings)
        self.assertFalse(w.export_all(output))
        self.assertEqual([path.read_bytes() for path in paths], original)
        self.assertEqual(w.read_encodings, choices)
        self.assertEqual(w.backed_up, set())
        self.assertTrue(w.projects_dirty)
        self.assertTrue(w.people_dirty['2027'])
        self.assertFalse(list(output.glob('*.bak')))

    def test_gui_replacement_failure_keeps_encoding_metadata_and_dirty_state(self):
        p, h = self.inputs('cp949', 'cp949')
        w = self.window()
        w.import_files([p, h])
        out = self.path / 'rollback'
        self.assertTrue(w.export_all(out))
        paths = [out / 'projects.yaml', out / 'people/people_2027.yaml']
        original = [path.read_bytes() for path in paths]
        codes = dict(w.read_encodings)
        w.projects_dirty = w.people_dirty['2027'] = True
        replace, calls = os.replace, []

        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError('저장 실패')
            replace(source, destination)

        with patch('allocation.storage.os.replace', side_effect=fail_second):
            self.assertFalse(w.export_all(out, encoding='utf-8-sig'))
        self.assertEqual([path.read_bytes() for path in paths], original)
        self.assertEqual(w.read_encodings, codes)
        self.assertEqual(w.document_encodings['people:2027'], {'cp949'})
        self.assertTrue(w.projects_dirty)
        self.assertTrue(w.people_dirty['2027'])

    def test_companion_project_detection_is_independent_of_manual_people_encoding(self):
        p, h = self.inputs('utf-8-sig', 'euc-kr')
        w = self.window()
        self.assertEqual(w.import_files([h], encoding='euc-kr'), ([], []))
        self.assertEqual(w.document_encodings['projects'], {'utf-8-sig'})
        self.assertEqual(w.document_encodings['people:2027'], {'euc-kr'})

    def test_plan_preparation_dialog_preserves_input_encoding_without_copying_fixed(self):
        _, h = self.inputs('euc-kr', 'euc-kr')
        w = self.window(planning=True)
        with patch('allocation.gui.choose_files', return_value=([str(h)], 'euc-kr')):
            with patch.object(QInputDialog, 'getText', return_value=('2028', True)):
                self.assertTrue(w.browse_prepare_plan())
        self.assertEqual(w.document_encodings['plan:2028'], {'euc-kr'})
        out = self.path / 'prepared'
        with patch('allocation.gui.choose_folder', return_value=(str(out), 'preserve')):
            self.assertTrue(w.browse_save_plan())
        self.assertEqual(decode_yaml((out / 'plans/plan_2028.yaml').read_bytes(), 'euc-kr').encoding, 'euc-kr')

    def test_plan_save_confirm_preserves_independent_encodings_and_source_metadata(self):
        p, _ = self.inputs('utf-8-sig')
        h = self.path / 'plan.yaml'
        h.write_bytes(encode_yaml(dump_plan(PlanDoc('2027', [PlanPersonEntry('홍길동', {'연구': '1'})])), 'cp949'))
        w = self.window(planning=True)
        self.assertEqual(w.import_files([p, h]), ([], []))
        self.assertTrue(w.planning_page.generate())
        work = self.path / 'plan-work'
        self.assertTrue(w.save_plan(work))
        self.assertEqual(decode_yaml((work / 'plans/plan_2027.yaml').read_bytes()).encoding, 'cp949')
        before = {key: set(value) for key, value in w.document_encodings.items()}
        out = self.path / 'confirmed'
        self.assertTrue(w.confirm_plan(out, encoding='utf-8'))
        self.assertEqual(w.document_encodings, before)
        self.assertIn('stage: confirmed', (out / 'people/people_2027.yaml').read_text(encoding='utf-8'))
        self.assertFalse(w.confirm_plan(work))  # 확정 내보내기의 원본 보호는 유지한다.

    def test_new_documents_and_reset_do_not_inherit_old_encoding(self):
        p, h = self.inputs('cp949', 'cp949')
        w = self.window()
        w.import_files([p, h])
        w.people_editor.year_edited('2028')
        self.assertTrue(w.flush_year_edit())
        self.assertEqual(w.document_encodings['people:2028'], {'cp949'})
        w.new_all()
        self.assertEqual(w.document_encodings, {})
        self.assertEqual(w.file_encodings, {})
        out = self.path / 'new'
        self.assertTrue(w.export_all(out))
        self.assertEqual(decode_yaml((out / 'projects.yaml').read_bytes()).encoding, 'utf-8')

    def test_file_dialog_options_preview_and_browse_routes(self):
        p, h = self.inputs('cp949', 'cp949')
        dialog = EncodingFileDialog(None, '인코딩', str(self.path))
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.testOption(QFileDialog.DontUseNativeDialog))
        self.assertEqual(dialog.encoding_combo.currentData(), 'auto')
        dialog.update_preview(str(h))
        self.assertIn('홍길동', dialog.preview.toPlainText())
        index = dialog.encoding_combo.findData('utf-8')
        dialog.encoding_combo.setCurrentIndex(index)
        self.assertIn('읽기 실패', dialog.preview.toPlainText())
        dialog.encoding_combo.setCurrentIndex(dialog.encoding_combo.findData('euc-kr'))
        self.assertIn('홍길동', dialog.preview.toPlainText())
        w = self.window()
        with patch('allocation.gui.choose_files', return_value=([str(p), str(h)], 'euc-kr')):
            w.browse_import()
        self.assertEqual(w.document_encodings['people:2027'], {'euc-kr'})
        out = self.path / 'browse-out'
        with patch('allocation.gui.choose_folder', return_value=(str(out), 'utf-8-sig')):
            self.assertTrue(w.browse_export())
        self.assertTrue((out / 'projects.yaml').read_bytes().startswith(b'\xef\xbb\xbf'))
        with patch('allocation.gui.choose_folder', return_value=('', 'preserve')):
            self.assertFalse(w.browse_export())

    def test_mode_encoding_metadata_is_isolated(self):
        p, h = self.inputs('cp949', 'cp949')
        settings = QSettings(str(self.path / 'main.ini'), QSettings.IniFormat)
        window = MainWindow(settings)
        self.addCleanup(window.deleteLater)
        window.actual.import_files([p, h], encoding='euc-kr')
        self.assertEqual(window.plan.document_encodings, {})
        self.assertEqual(window.plan.read_encodings, {})
        window.set_mode(window.PLANNING)
        self.assertIn('신규', window.plan.encoding_label.text())
        window.set_mode(window.ACTUAL)
        self.assertIn('EUC-KR', window.actual.encoding_label.text())

    def test_actual_file_dialog_accepts_encoding_and_cancel(self):
        _, h = self.inputs('cp949', 'cp949')

        def accept_read():
            dialog = QApplication.activeModalWidget()
            dialog.selectFile(str(h))
            dialog.encoding_combo.setCurrentIndex(dialog.encoding_combo.findData('cp949'))
            dialog.accept()

        QTimer.singleShot(0, accept_read)
        paths, encoding = choose_files(None, str(h.parent), single=True)
        self.assertEqual([Path(path) for path in paths], [h])
        self.assertEqual(encoding, 'cp949')

        def accept_save():
            dialog = QApplication.activeModalWidget()
            dialog.setDirectory(str(h.parent))
            dialog.encoding_combo.setCurrentIndex(dialog.encoding_combo.findData('utf-8-sig'))
            dialog.accept()

        QTimer.singleShot(0, accept_save)
        folder, encoding = choose_folder(None, str(h.parent))
        self.assertEqual(Path(folder), h.parent)
        self.assertEqual(encoding, 'utf-8-sig')
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        self.assertEqual(choose_files(None, str(h.parent)), ([], 'auto'))
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        self.assertEqual(choose_folder(None, str(h.parent)), ('', 'preserve'))
