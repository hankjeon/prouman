#!/usr/bin/env bash
# 실행한 작업 디렉터리는 유지하고, 저장소의 src/main.py를 호출합니다.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1

if [[ $# -eq 0 ]]; then
    cat <<'USAGE'
사용법: ./run.sh <사람 YAML 파일> [--person 이름 | --project 사업약칭 | --all-people | --all-projects | --all]

예시 (저장소 루트에서 실행):
  ./run.sh people/people_2027.yaml
  ./run.sh people/people_2027.yaml --person "홍길동"
  ./run.sh people/people_2027.yaml --project "사업1"
  ./run.sh people/people_2027.yaml --all-people    # 사람 전체 상세
  ./run.sh people/people_2027.yaml --all-projects  # 사업 전체 상세
  ./run.sh people/people_2027.yaml --all

템플릿 실행:
  cd templates/01_basic
  ../../run.sh people/people_2027.yaml --all

현재 작업 디렉터리의 projects.yaml을 읽습니다.
활성 가상환경을 사용하며, 없으면 저장소의 .venv를 활성화합니다.
가상환경 설치 방법은 README.md를 참고하세요. 전체 옵션: ./run.sh --help
USAGE
    exit 0
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    venv_dir="$VIRTUAL_ENV"
else
    venv_dir="$script_dir/.venv"
    if [[ ! -f "$venv_dir/bin/activate" || ! -x "$venv_dir/bin/python" ]]; then
        printf '%s\n' '오류: 저장소의 .venv 가상환경이 없습니다. 먼저 저장소 루트에서 설치하세요:' \
            '  python3 -m venv .venv' \
            '  .venv/bin/python -m pip install -r requirements.txt' >&2
        exit 1
    fi
    # shellcheck disable=SC1091
    source "$venv_dir/bin/activate" || exit 1
fi

if [[ ! -x "$venv_dir/bin/python" ]]; then
    printf '%s\n' "오류: 활성 가상환경의 Python을 찾을 수 없습니다: $venv_dir/bin/python" \
        '가상환경을 다시 활성화하거나 비활성화한 후 실행하세요.' >&2
    exit 1
fi

# 배열 인자를 그대로 전달하고 Python의 종료 코드를 보존합니다.
exec "$venv_dir/bin/python" "$script_dir/src/main.py" "$@"
