# 자동 저장·이력·테마 수용행렬 (2026-11-28)

이 표는 구현과 **실행한 시험의 범위**를 연결한다. 전체 자동화 통과가 모든 실제 장치·사용자 조작을 확인했다는 뜻은 아니다. Windows, Python 3.14, PySide6, 격리된 Git HEAD fixture와 임시 YAML/INI를 사용했다. GUI 시험은 별도 표기가 없으면 `QT_QPA_PLATFORM=offscreen`이다. 아래 시험명은 `tests/`의 파일과 메서드이며, 파일만 표기한 경우 해당 모듈의 관련 사례를 뜻한다.

| ID | 구현 / 시험 증거 | 남은 경계 |
|---|---|---|
| AS01 | `gui.py` flush_autosave, `editor.py`; `test_autosave_gui.py` rate, empty, input_fixed, person_project | 모든 가능한 셀 조합의 실마우스 조작은 미실시 |
| AS02 | `editor.py` 순서, `gui.py` 저장; `test_autosave_gui.py::test_project_and_people_order_saved`, `test_gui.py::test_input_drag_order_reaches_saved_yaml_and_excel` | 실제 마우스 드래그 장치 미실시 |
| AS03 | `gui.py` _finish_pin_change; `test_autosave_gui.py::test_pin_and_input_share_session`, `test_gui.py` pin 관련 사례 | 물리 디스크 장애 미실시 |
| AS04 | `autosave.py` TransactionalSession, `storage.py` write_bundle; `test_autosave_gui.py::test_alias_one_bundle_and_failure_restores`, `test_planning_autosave.py::test_project_delete_removes_all_plan_references_in_one_bundle` | OS 강제 종료·전원 장애 미실시 |
| AS05 | `planning_gui.py`, `gui.py`; `test_planning_autosave.py` edit_input_matrix, generate_and_cancel, import_navigation | 실사용 규모 성능 별도 미검증 |
| AS06 | `src/allocation/document.py`, `src/allocation/loading.py`; `test_planning_autosave.py::test_edit_input_matrix_and_empty_error_roundtrip`, `test_quantity_precision.py` | 모든 오류값 조합 미열거 |
| AS07 | `editor.py` NumericDelegate/paste; `test_gui.py` numeric_paste, numeric_editor, `test_workflows.py::test_NP14_invalid_editing_strings_survive_roundtrip` | IME별 실제 키보드 입력 미실시 |
| AS08 | `gui.py` _save_new_bindings; `test_source_binding_gui.py` new, merge, cancel, year 관련 사례 | 실제 파일 선택 대화상자 수동 조작 미실시 |
| AS09 | `gui.py` 사본/확정 내보내기; `test_copy_status_gui.py` copy_variants, plan_copy_error, confirmed_copy, overwrite_conflict | 실제 잠금/권한 오류 대신 실패 주입 |
| AS10 | `storage.py` write_bundle; `test_workflows.py::BundleTests`, `test_autosave_gui.py` encoding/rollback 사례 | 디스크 제거·전원 중단·실제 권한 거부 미실시 |
| AS11 | `autosave.py` fingerprints/guards, `global_history.py` move; `test_autosave.py::test_unchanged_dependency_external_change_blocks_commit`, `test_autosave_gui.py::test_pin_rejects_external_project_dependency_change`, `test_global_history_gui.py::test_external_unchanged_dependency_blocks_history` | 검사와 OS 교체 사이 극소 경쟁 창을 OS 잠금으로 제거하지는 않음 |
| AS12 | `gui.py` 바인딩/탐색; `test_autosave_gui.py::test_noop_people_and_project_clear_dirty_preserve_redo`, `test_planning_autosave.py::test_import_navigation_no_write_or_recalculation` | 모든 페이지 탐색 순열 미열거 |
| UH01 | `global_history.py`, `gui.py` 단축키; `test_global_history_gui.py::test_shortcuts_and_native_editor_undo` | 다른 입력기/플랫폼 실제 키보드 미실시 |
| UH02 | `global_history.py` move, `storage.py`; `test_global_history_gui.py` cross_mode_multistep, cross_mode_failure | 실제 전원 장애 미실시 |
| UH03 | `gui.py`, `editor.py`; `test_global_history_gui.py::test_paste_alias_pin_and_plan_calculation_are_single_entries` | 실제 드래그 장치 미실시 |
| UH04 | `history.py`, `autosave.py`; `test_global_history_gui.py::test_success_branch_only_discards_redo_and_noop_preserves`, `test_autosave.py::test_move_failure_and_redo_branch` | 인코딩 거절의 모든 분기 조합 미열거 |
| UH05 | `gui.py` 바인딩 이력 무효화; `test_global_history_gui.py` new_or_close, long_history, `test_source_binding_gui.py::test_cancel_keeps_global_history_and_unbound_content_during_undo` | 대화상자 수동 조작 미실시 |
| UH06 | `gui.py` history UI/편집 확정; `test_global_history_gui.py` new_window, long_history, `test_copy_status_gui.py::test_active_editor_commits_before_copy` | 활성 편집기 종료·모든 탭 경계는 별도 실기 확인 필요 |
| MD01 | `document.py` YAML metadata; `test_quantity_precision.py` metadata_roundtrip, `test_autosave.py::test_copies_metadata_precision_and_order` | 임의 비표준 YAML 자료형은 지원 범위 밖 |
| MD02 | `document.py`, `gui.py`; `test_source_binding_gui.py::test_merge_precise_metadata_then_undo_redo_and_restart`, `test_copy_status_gui.py::test_confirmed_copy_preserves_metadata_and_sources`, `test_workflows.py::test_NP02_NP11_previous_actual_is_only_a_reference_copy` | 모든 삭제/취소 조합 미열거 |
| MD03 | `document.py`, `loading.py`; `test_quantity_precision.py::test_metadata_rejects_duplicates_unsafe_tags_and_cycles`, `test_autosave_gui.py::test_unsupported_source_is_never_overwritten` | 새 미지원 YAML 태그 종류마다 별도 시험하지 않음 |
| TH01 | `theme.py`, `editor.py` PinListDelegate; `test_theme_rendering.py::test_actual_widgets_styles_states_and_capture`, `test_palette_contrast_pairs` | Windows 실제 플랫폼 시험은 비활성 숨김 위젯; 활성/초점 조합은 offscreen 모의 |
| TH02 | `theme.py` draw_pin_icon, `editor.py`, `gui.py`; `test_theme_rendering.py`, `test_gui.py` pin/list/font/drag | 물리 고DPI 모니터·마우스·IME 미실시 |
| TH03 | `theme.py` apply_window_theme, `gui.py` paletteChanged; `test_theme_rendering.py` 실제 위젯 및 runtime | Windows 플랫폼의 Fusion/Windows 스타일을 숨긴 창에서 렌더링; OS 고대비는 모의, OS 테마 전환·다른 OS 미실시 |
| RG01 | `src/allocation/loading.py`, `src/allocation/engine.py`, `src/main.py`; 전체 unittest와 `templates/01_basic` 기본/--person/--project/--all-people/--all-projects/--all 각각 종료 0 | Linux·오프라인 신규 설치 미실시 |
| RG02 | `storage.py`, `planning.py`, `gui.py`; 전체 unittest 및 `test_workflows.py`, `test_quantity_precision.py`, `test_copy_status_gui.py` | 실제 Excel 앱, 실제 잠김 파일·전원 차단 미실시 |

## 재실행 및 시각 증거

Git HEAD로 만든 임시 스냅샷에 현재 `src`와 `tests`만 덮어쓴 뒤 그 디렉터리에서 `QT_QPA_PLATFORM=offscreen`으로 `.venv`의 Python에 `-m unittest discover -s tests -q`를 실행한다. 업무 `myteam`은 사용하지 않고 HEAD 스냅샷의 fixture만 사용한다. Windows 렌더링은 같은 임시 스냅샷에서 `QT_QPA_PLATFORM=windows`, `PYTHONPATH=tests;src`로 `python -m unittest test_theme_rendering -q`를 실행했다. Qt 미설치 모의는 별도 프로세스에서 `find_spec('PySide6')`만 차단해 새 모듈의 세 시험이 skip되는 것을 확인했다.

PNG는 `%TEMP%/opencode/prouman-theme-{offscreen,windows}-{Fusion,Windows}-{light,dark}.png`에 있다. 색상 조합 대비는 sRGB 계산으로 일반 텍스트 4.5:1 및 선택 영역의 압정/현재 셀 3:1 이상을 검사한다. 안티앨리어싱 픽셀만으로 대비를 판단하지 않는다. 시험용 위젯은 숨긴 별도 창이며 실제 물리 DPI, 운영체제 고대비, 사용자 키보드·마우스 및 실장치 장애는 검증하지 않았다.

## 실행 결과와 노트북 인계

- 최종 소스의 표준 offscreen 전체 회귀: 297개 중 291개 통과, 6개 skip, 실패·오류 0개. 이 환경에서는 이전 기록의 PowerShell 출력 디코딩 오류가 재현되지 않았다. UTF-8 환경을 지정한 별도 전체 회귀 결과는 최신 SESSION을 따른다.
- 새 테마 시험: Windows 플랫폼의 `Fusion`, `Windows`, `windowsvista`, `windows11` 스타일 모두 시험했으며 3개 시험이 통과했다. 스크린샷은 각 스타일·명암별로 `%TEMP%/opencode/prouman-theme-windows-<스타일>-<light|dark>.png`에 있다. 숨긴 창 시험이므로 실제 사용자의 초점·실마우스 조작으로 해석하지 않는다.
- 노트북에서 Git 게시와 코디네이터 검토가 끝난 뒤 저장소를 체크아웃하고, Windows PowerShell에서 `python -m venv .venv`, `.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt`, `.\.venv\Scripts\python.exe src\gui.py` 순서로 소스를 실행한다. GUI 의존성 파일은 CLI 의존성도 포함한다. 테스트는 임시 fixture에서 `QT_QPA_PLATFORM=offscreen`과 `python -m unittest discover -s tests -q`를 쓴다. 사용자 업무 YAML은 개발 fixture로 옮기지 않는다. 실제 데이터 사용 여부와 저장 위치는 노트북 사용자에게 맡긴다.
- 코디네이터는 이 표의 부분 검증 범위, Windows 스타일 PNG, 최종 diff와 실패 보호를 독립 검토한 뒤 게시 여부를 결정한다. EXE는 이 작업에서 빌드하지 않았다.

## 코디네이터 최종 독립 확인

2026-11-28 최종 수정본에서 위 기능의 소스 독립 검토를 통과했다. 실제 장치/플랫폼 경계는 각 행에 남겨 둔다.

- Git HEAD fixture 기반 최종 스냅샷의 표준 전체 unittest **297개 중 291개 통과·6개 skip·오류/실패 0개**.
- Windows 플랫폼에서 수정된 숨김/비활성 시험 창의 테마 시험 **3개 통과**. 코디네이터가 Windows11 밝은/어두운 PNG를 직접 확인했다.
- 핵심 자동 저장/별표·종료/metadata/메뉴 수용 점검 **6개 통과**, 미확정 Qt 셀 편집의 종료·모드·연도 경계 추가 점검 **3개 통과**.
- CLI 6종 각각 종료 0, 새 GUI 시험 6개 모듈의 Qt 미설치 모의 발견 **73개 skip·오류 없음**.
- 시험 스냅샷과 실제 소스/시험 Python 42개 파일의 지문이 일치했다.
- 기존 PowerShell 시험은 helper에서 자식 출력 UTF-8과 decoder 계약을 맞춰 표준 명령의 환경 의존 오류를 보완했다. 제품 `venv.ps1`은 바꾸지 않았다.

재개 절차·검토 이미지·다음 현장 확인은 [노트북 인계 문서](handoff-20261128.md)에 있다. GitHub 게시·병합의 실제 결과는 최신 SESSION을 따른다.
