import sys

try:
    from allocation.gui import main
except ModuleNotFoundError as exc:
    if not (exc.name or '').startswith(('PySide6', 'shiboken6')):
        raise
    print('오류: GUI 실행에 필요한 PySide6가 없습니다. 저장소 루트에서 설치하세요:\n'
          '  python -m pip install -r requirements-gui.txt', file=sys.stderr)
    raise SystemExit(1)

if __name__ == '__main__':
    raise SystemExit(main())
