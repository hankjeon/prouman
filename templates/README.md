# YAML 템플릿 모음

각 사례는 독립된 작업 폴더다. 정상·경고 사례는 복사해서 입력 작성의 출발점으로 사용할 수 있다. `errors/`는 오류 메시지를 확인하기 위한 잘못된 입력이므로 그대로 실무 입력에 사용하지 않는다.

## 권장 폴더 구조

```text
작업폴더/
  projects.yaml            # 모든 연도에 공통으로 사용하는 사업 목록
  people/
    people_2026.yaml       # 2026년 사람·휴직·연간 사업 목표
    people_2027.yaml       # 2027년 입력 (한 번 실행할 때 하나만 선택)
    people_2028.yaml       # 2028년 입력
```

단일 연도만 필요하면 해당 연도의 사람 파일 하나만 둔다. 실제 예제의 전체 구조:

```text
templates/
  README.md
  01_basic/
    projects.yaml
    people/people_2027.yaml
  02_multiple_projects/
  03_excluded_months/
  04_partial_period/
  05_absences/             # people_2026.yaml
  06_leap_year/            # people_2028.yaml
  07_multi_year/           # people_2026.yaml, people_2027.yaml, people_2028.yaml
  08_whitespace/
  09_warnings/
  errors/
    10_target_total/
    11_project_limit/
    12_joint_capacity/
    13_unknown_alias/
    14_duplicate_alias/
    15_duplicate_yaml_key/
    16_invalid_fields/
    17_zero_budget/
```

생략해서 표시한 각 사례 폴더에도 `projects.yaml`과 `people/people_연도.yaml`이 들어 있다. 다른 사례의 사람·사업 파일은 약칭과 상한이 다르므로 섞어서 사용하지 않는다.

## 실행 방법

먼저 저장소 루트의 [설치 안내](../README.md#설치)에 따라 `.venv`를 준비한다. 아래 명령은 저장소 루트에서 시작한다.

```bash
cd templates/01_basic
../../.venv/bin/python ../../main.py people/people_2027.yaml --all
```

프로그램은 **현재 작업 폴더의 `projects.yaml`**을 읽는다. 저장소 루트에서 사람 파일 경로만 템플릿으로 지정하면 루트의 다른 사업 파일을 읽으므로, 실행 전에 원하는 사례 폴더로 이동한다.

동일한 사례 폴더 안에서 개인·사업을 조회할 수도 있다:

```bash
../../.venv/bin/python ../../main.py people/people_2027.yaml --person 홍길동
../../.venv/bin/python ../../main.py people/people_2027.yaml --project 연구지원
```

다년 사업은 `templates/07_multi_year` 폴더에서 사람 파일만 바꿔 실행한다:

```bash
../../.venv/bin/python ../../main.py people/people_2026.yaml --all
../../.venv/bin/python ../../main.py people/people_2027.yaml --all
../../.venv/bin/python ../../main.py people/people_2028.yaml --all
```

오류 사례는 폴더 깊이가 한 단계 더 깊다. 저장소 루트에서 시작하는 예:

```bash
cd templates/errors/12_joint_capacity
../../../.venv/bin/python ../../../main.py people/people_2027.yaml --all
```

Windows에서는 `python`으로 실행하거나 `.venv/Scripts/python.exe` 경로를 사용한다. 예를 들어 `01_basic` 폴더에서는 `../../.venv/Scripts/python.exe ../../main.py people/people_2027.yaml --all`로 실행한다.

## 사례별 예상 결과

| 폴더 | 참고할 내용 | 예상 결과 |
| --- | --- | --- |
| [01_basic](01_basic/projects.yaml) | 필수 필드, 사람 1명·사업 1개, 주석 작성 | 오류·경고 없음, 종료 0 |
| [02_multiple_projects](02_multiple_projects/people/people_2027.yaml) | 사람 3명·사업 3개, 배정 상한과 잔여 | 오류·경고 없음, 종료 0 |
| [03_excluded_months](03_excluded_months/projects.yaml) | 4~6월 제외, 0 자리 채움, 다른 사업으로 월 가용량 충족 | 오류·경고 없음, 종료 0 |
| [04_partial_period](04_partial_period/projects.yaml) | 월 중간 시작·종료도 해당 월 전체 허용 | 오류·경고 없음, 종료 0 |
| [05_absences](05_absences/people/people_2026.yaml) | 부분월·분기·복수 기간·연중 전체 휴직, 선택 사유, 빈 할당 | 오류·경고 없음, 종료 0 |
| [06_leap_year](06_leap_year/people/people_2028.yaml) | 2월 29일 하루 휴직, 반올림과 연간 목표 | 오류·경고 없음, 종료 0 |
| [07_multi_year](07_multi_year/projects.yaml) | 하나의 사업 파일과 3개 연도 입력, 연도별 상한·제외 월 | 세 연도 모두 오류·경고 없음, 종료 0 |
| [08_whitespace](08_whitespace/projects.yaml) | 사업명 중간 공백 유지, 사업명·약칭·참조의 앞뒤 공백 제거 | 오류·경고 없음, 종료 0 |
| [09_warnings](09_warnings/people/people_2027.yaml) | 중복 제외 월, 기간 밖 연도, 연도 경계·중복 휴직, 0·미등록 배정 | W001~W005, 경고 6건, 오류 0건, 종료 0 |
| [errors/10_target_total](errors/10_target_total/people/people_2027.yaml) | 개인 연간 목표 부족 | E101, 종료 1 |
| [errors/11_project_limit](errors/11_project_limit/projects.yaml) | 사람별 합계는 맞지만 사업 전체 상한 초과 | E103, 종료 1 |
| [errors/12_joint_capacity](errors/12_joint_capacity/projects.yaml) | 개별 사업은 가능해도 공동 허용 월 부족 | E201, 종료 1 |
| [errors/13_unknown_alias](errors/13_unknown_alias/people/people_2027.yaml) | 등록되지 않은 사업약칭 참조 | E007, 종료 1 |
| [errors/14_duplicate_alias](errors/14_duplicate_alias/projects.yaml) | 앞뒤 공백 제거 후 약칭 중복 | E004, 종료 1 |
| [errors/15_duplicate_yaml_key](errors/15_duplicate_yaml_key/people/people_2027.yaml) | 같은 YAML mapping 키 반복 | E002, 종료 1 |
| [errors/16_invalid_fields](errors/16_invalid_fields/projects.yaml) | 미지원 필드, 제외 월 13, 잘못된 날짜, 소수 6자리 | E003·E005·E006, 종료 1 |
| [errors/17_zero_budget](errors/17_zero_budget/projects.yaml) | 배정 0인 사업에 양수 목표 | E102, 종료 1 |

경고만 있으면 최종 표를 출력한다. 오류 사례는 실패하는 것이 정상이며 최종 표를 출력하지 않는다. 상세 규칙은 [데이터 모델](../docs/data-model.md)과 [오류 코드](../docs/validation.md#구현-오류-코드)를 참고한다.

## YAML 주석과 입력 작성 요령

YAML 표준은 `#` 주석을 지원한다. 독립된 줄이나 값 뒤에 쓸 수 있으며 파서는 주석을 무시한다.

```yaml
# 독립된 주석입니다.
year: 2027  # 값 뒤의 주석입니다.
```

- 들여쓰기는 탭 대신 스페이스를 사용한다. 예제는 단계마다 2칸을 사용한다.
- 날짜·기간과 수량은 따옴표로 감싼다. 연도와 월 번호는 따옴표 없는 정수로 쓴다.
- 사업명은 중간 공백을 허용하지만 사업약칭 내부 공백은 허용하지 않는다. 약칭은 파일 내에서 고유해야 한다.
- `allocations`에는 사업의 `name`이 아닌 `alias`를 적는다. 연간 목표 합계와 반올림한 공백 합계를 더해 정확히 `1.00000`이 되어야 한다.
- 휴직을 추가해도 사업 목표는 자동으로 줄어들지 않는다. `05_absences`처럼 공백을 반영해 목표를 입력한다.
- `excluded_months`는 필수다. 제외 월이 없으면 `[]`로 적는다. 사업 배정인력은 상한이며 미소진은 정상이다.
- 필드의 문법과 오타는 조용히 무시되지 않는다. 같은 mapping 키를 반복하거나 설명용 임의 필드를 추가하지 말고 주석을 사용한다.
- 값 자체에 ` #`가 들어가야 하면 `name: "연구 #1"`처럼 따옴표로 감싼다. 따옴표 안의 `#`는 주석이 아니다.
