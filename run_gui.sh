#!/usr/bin/env bash
# 저장소의 src/gui.py로 데스크톱 화면을 실행합니다.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    venv_dir="$VIRTUAL_ENV"
else
    venv_dir="$script_dir/.venv"
    if [[ ! -f "$venv_dir/bin/activate" || ! -x "$venv_dir/bin/python" ]]; then
        printf '%s\n' '오류: 저장소의 .venv 가상환경이 없습니다. 먼저 저장소 루트에서 설치하세요:' \
            '  python3 -m venv .venv' \
            '  .venv/bin/python -m pip install -r requirements-gui.txt' >&2
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

exec "$venv_dir/bin/python" "$script_dir/src/gui.py"
