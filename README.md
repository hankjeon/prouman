# 인력할당 오프라인 CLI

사람별·사업별 연간 할당 목표를 월별로 배분하는 Python CLI다. 입력한 연간 목표를 유지하면서 월별 가용량, 휴직, 사업 기간, 제외 월, 사업별 연간 배정 상한을 검증한다. 오류가 있으면 최종 할당표를 출력하지 않는다.

Python 3.10 이상과 PyYAML 6.0.3을 사용한다. 이번 구현은 Python 3.12에서 검증했다. 실행 시 인터넷이나 서버는 필요하지 않다. GUI, 그래프, Excel/CSV 출력, 연간 목표 자동 결정은 후속 범위다.

## 설치

저장소 루트에서 실행한다. 온라인 설치:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

오프라인 설치용 파일은 대상 환경과 같은 Python 버전·운영체제·CPU 환경에서 먼저 준비한다:

```bash
python3 -m pip download --only-binary=:all: --no-deps -r requirements.txt -d .wheels
```

저장소와 `.wheels` 디렉터리를 대상 환경으로 옮긴 뒤, 외부 패키지 인덱스 없이 설치한다:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links=.wheels -r requirements.txt
```

Windows에서는 `python3` 대신 `python`, `.venv/bin/python` 대신 `.venv\Scripts\python.exe`를 사용한다. wheel은 환경에 맞게 다시 준비해야 하며 `.wheels`와 `.venv`는 Git에 포함하지 않는다.

## 실행

저장소 루트에 동작 가능한 예제 `projects.yaml`과 `people/people_2027.yaml`이 있다. 실제 업무 데이터로 교체하기 전에 예제로 실행할 수 있다.

```bash
.venv/bin/python src/main.py people/people_2027.yaml
.venv/bin/python src/main.py people/people_2027.yaml --person 홍길동
.venv/bin/python src/main.py people/people_2027.yaml --project 사업1
.venv/bin/python src/main.py people/people_2027.yaml --all
```

사업 파일은 **현재 실행 작업 디렉터리의 `projects.yaml`**이다. 사람 파일 경로는 위치 인자이며, 관리 연도는 파일명이 아니라 파일 안의 `year`에서 읽는다. 조회 옵션 세 개는 함께 사용할 수 없다.

### 실행 스크립트

설치 후 Linux Bash에서는 `run.sh`, Windows PowerShell에서는 `run.ps1`을 사용하면 가상환경을 직접 활성화하지 않아도 된다.

```bash
./run.sh people/people_2027.yaml --all
```

```powershell
.\run.ps1 people\people_2027.yaml --all
```

인자가 없으면 사용법을 표시한다. 활성 가상환경(`VIRTUAL_ENV`)이 있으면 그 환경의 Python을 사용하고, 없으면 스크립트가 위치한 저장소의 `.venv`를 활성화한다. 가상환경 생성과 패키지 설치는 위 설치 절차로 먼저 진행한다. 전달한 인자와 프로그램 종료 코드를 그대로 보존하며, 실행 작업 디렉터리도 유지한다. 예를 들어 `templates/01_basic`에서 `../../run.sh people/people_2027.yaml --all` 또는 `..\..\run.ps1 people\people_2027.yaml --all`로 해당 템플릿을 실행할 수 있다.

옵션 없는 기본 출력은 검증 결과와 개인별·사업별 연간 요약이다. `--person`·`--project`를 지정하면 전체 요약은 생략하고 선택한 대상의 상세만 출력한다. 입력 파일·관리 연도와 검증 결과는 계속 표시한다. `--all`은 전체 요약과 모든 개인 상세를 출력한다. 개인 상세는 1~12월·연간 집계와 공백을 표시한다. 분기별 열은 표시하지 않으며 분기 합계의 내부 검증은 유지한다. 사업 조회는 월별 할당 합계와 연간 배정/할당/잔여를 표시한다. 수량은 모두 소수점 5자리다. 성공·경고만 있는 경우 종료 코드 0, 입력·계산 오류는 1, CLI 사용법 오류는 2다.

예제의 연간 요약:

```text
김철수: 0.75000 / 0.25000 / 1.00000
홍길동: 1.00000 / 0.00000 / 1.00000
사업1 (연구 지원 사업): 1.50000 / 0.60000 / 0.90000
사업2 (운영 사업): 1.50000 / 1.15000 / 0.35000
```

사업명은 앞뒤 공백을 제거하고 중간 공백은 유지한다. 사업약칭은 앞뒤 공백을 제거한 뒤 내부 공백을 허용하지 않는다. 약칭의 중복 검사·참조·조회에도 이 규칙을 적용한다.

## 입력 템플릿

[templates/README.md](templates/README.md)에 권장 폴더 구조와 실행 방법을 정리했다. `templates/`에는 기본 배분, 여러 사람·사업, 제외 월, 부분 사업 기간, 휴직, 윤년, 다년 사업, 공백 처리, 경고·오류 등 17개 사례의 주석 달린 YAML 예제가 있다. 각 사례 폴더를 작업 디렉터리로 사용하며, `errors/`의 입력은 의도적으로 실패하는 참고 자료다.

## 구성

- `src/main.py`, `src/allocation/cli.py`: CLI 진입점과 조회 인자
- `src/allocation/loading.py`: 안전한 YAML 로드, 원문 숫자·중복 키·스키마 검증
- `src/allocation/models.py`: 입력·결과·진단 모델과 고정 소수 출력
- `src/allocation/flow.py`: 정수 최대 흐름 및 균등 초안을 참고한 재배치
- `src/allocation/engine.py`: 공백·허용 월 계산, 사전·사후 검증
- `src/allocation/rendering.py`: 진단·요약·상세표 출력
- `tests/test_allocation.py`: T01~T26, 경계 입력, 결정성, 완전 탐색 비교

## 테스트

```bash
.venv/bin/python -m unittest discover -s tests -v
```

표준 라이브러리 `unittest`를 사용한다. 모든 성공 fixture는 반복 실행과 YAML 항목 순서 변경 결과를 비교한다. 작은 정수 흐름 문제 200개는 완전 탐색과 비교하며, 외부 소켓을 금지한 상태에서 CLI도 검증한다. 테스트는 임시 YAML을 사용하고 예제 파일을 수정하지 않는다.

## 요구사항 문서

1. [기능과 수용 기준](docs/requirements.md)
2. [YAML 데이터 모델](docs/data-model.md)
3. [계산식과 배분 절차](docs/allocation-algorithm.md)
4. [검증과 오류 코드](docs/validation.md)
5. [수용 테스트 사례](docs/test-cases.md)
6. [Codex 작업 지침](AGENTS.md)

월별 직접 입력, 사업 기간의 일수 비례 상한, 사업 배정인력 완전 소진은 요구하지 않는다. 구현 기준은 이 저장소의 문서이며 외부 대화 내용을 자동으로 읽는다고 가정하지 않는다.
