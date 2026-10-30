#!/usr/bin/env bash
# source하면 현재 셸을 활성화하고 직접 실행하면 활성화된 새 Bash를 연다.
_prouman_venv_main() {
    local root venv
    root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return 1
    venv="$root/.venv"
    if [[ ! -f "$venv/bin/activate" || ! -x "$venv/bin/python" ]]; then
        printf '%s\n' "오류: 가상환경이 없습니다: $venv" \
            "생성: python3 -m venv \"$venv\"" \
            "설치: \"$venv/bin/python\" -m pip install -r \"$root/requirements.txt\"" >&2
        return 1
    fi
    if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
        printf '%s\n' '가상환경이 활성화된 새 Bash를 엽니다. 종료하려면 exit를 입력하세요.'
        exec bash --rcfile <(printf 'if [[ -f ~/.bashrc ]]; then source ~/.bashrc; fi\nsource %q\n' "$venv/bin/activate") -i
    fi
    if [[ "${VIRTUAL_ENV:-}" == "$venv" && "$(command -v python)" == "$venv/bin/python" ]]; then
        printf '%s\n' "이미 활성화됨: $venv"
        return 0
    fi
    # shellcheck disable=SC1091
    source "$venv/bin/activate" || return 1
    printf '%s\n' "가상환경 활성화: $venv"
}
_prouman_venv_main
_prouman_venv_status=$?
unset -f _prouman_venv_main
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    exit "$_prouman_venv_status"
else
    return "$_prouman_venv_status"
fi
