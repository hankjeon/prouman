# 인력할당 1차 개발 문서

이 문서 세트는 인력할당 프로그램의 1차 요구사항을 Codex 구현 기준으로 정리한 것이다. 특히 월별 인력 직접 입력 및 사업 기간의 일수 비례 계산 제안은 최종 규칙이 아니다.

## 목적과 범위

사용자가 사람별·사업별 **연간 할당인력**을 결정한다. Python 오프라인 CLI가 이를 할당 가능한 월에 기본적으로 균등하게 나눈다. 입력한 연간 목표량은 바꾸지 않고, 월별 가용량, 휴직, 사업 기간, 제외 월, 사업별 연간 배정인력 상한을 모두 만족하도록 필요한 만큼 월별 값을 조정한다. 제곱편차의 전역 최솟값은 요구하지 않는다. 불가능하거나 논리적으로 모순되면 `ERROR`를 내고 해당 연도 최종 할당표 생성을 중단한다.

1차 범위: YAML 로드·무결성 검사, 한 연도 계산, 개인/사업별 월·분기·연간 집계, 텍스트 CLI 출력. 인터넷 연결 없이 실행하며 GUI, 그래프, Excel/CSV 내보내기, 사업 우선순위에 따른 연간 참여율 자동 결정은 후속 범위다.

## 문서 읽는 순서

1. [requirements.md](docs/requirements.md): 기능과 수용 기준
2. [data-model.md](docs/data-model.md): 두 YAML 파일의 최종 입력 형식
3. [allocation-algorithm.md](docs/allocation-algorithm.md): 계산식과 균등 배분 절차
4. [validation.md](docs/validation.md): `ERROR`/`WARNING` 판정
5. [test-cases.md](docs/test-cases.md): 구현 검증 사례
6. [AGENTS.md](AGENTS.md): Codex 작업 지침

## 예정 디렉터리와 실행 방식

```text
manpower-allocation/
  AGENTS.md
  README.md
  docs/
    requirements.md
    data-model.md
    allocation-algorithm.md
    validation.md
    test-cases.md
  projects.yaml                 # 구현·입력 준비 시 생성
  people/
    people_2027.yaml            # 구현·입력 준비 시 생성
  main.py                       # Codex 구현 대상
```

예정 CLI 계약은 다음과 같다. 현재 폴더에는 **구현 코드와 실제 YAML 데이터가 아직 없으므로** 아래 명령은 문서상의 목표 사용법이다.

```powershell
python main.py people/people_2027.yaml
python main.py people/people_2027.yaml --person 홍길동
python main.py people/people_2027.yaml --project 사업1
python main.py people/people_2027.yaml --all
```

기본 실행은 검증 결과와 개인별·사업별 연간 요약을 출력한다. 옵션은 개인 월별표, 사업 월별표, 전체 개인 월별표를 조회한다. 대상 연도는 파일명이 아니라 people 파일 최상위 `year`로 판단한다. `projects.yaml`은 기본적으로 작업 폴더 루트에서 읽고, 계산 엔진과 출력 코드는 분리한다.

## Codex에서 작업 시작하기

이 저장소를 Codex의 로컬 작업 폴더로 열거나 Codex Cloud 환경에 연결한다. 루트의 `AGENTS.md`와 `README.md`, `docs/`의 상세 문서를 함께 읽으면 된다. 이 문서들은 구현 전 요구사항 검토용이며, 실제 YAML 입력과 Python 코드는 아직 없다.

Codex에 보낼 **첫 작업 요청** 예시:

> 루트의 AGENTS.md와 README.md부터 읽고 docs/requirements.md, docs/data-model.md, docs/allocation-algorithm.md, docs/validation.md, docs/test-cases.md를 검토해줘. 문서 간 모순이나 구현상 불명확한 점을 보고하고, 1차 Python 오프라인 CLI의 구현 계획과 테스트 계획을 제시해줘. 이번 단계에서는 아직 코드를 작성하지 마.

계획을 검토한 다음 구현을 요청한다. 이 순서는 원 대화에서 제안한 요구사항 검토와 구현의 분리를 반영한다.

ChatGPT 프로젝트 대화 자체를 Codex가 자동으로 읽는다고 가정하지 않는다. 구현 판단은 저장소에 커밋된 문서를 기준으로 한다.
