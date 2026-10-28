"""시스템 테마(라이트/다크)에 맞는 색상.

밝은 색을 하드코딩하면 다크 테마에서 흰 글자와 겹쳐 안 보인다(좌측 메뉴,
선택 셀, 계산 칸, 범례). 모든 의미 색상은 이 모듈의 함수로 구하고, 그릴
때마다 현재 팔레트를 기준으로 고른다. `is_dark()`을 바꾸면 라이트/다크
짝을 테스트에서 확인할 수 있다.
"""
from PySide6.QtGui import QColor, QGuiApplication, QPalette

# 라이트(예전 Windows 회색 계열에서 쓰던 값)와 다크 짝. 다크 짝은 흰 글자가
# 보이는 어두운 틴트로 고른다.
LIGHT = {
    'current_row': '#eef5ff',
    'current_cell': '#1a73e8',
    'problem': '#ffd6d6',
    'computed': '#f0f0f0',
    'excluded': '#e6dcff',
    'outside': '#e4e4e4',
    'error_text': '#c62828',
    'warning_text': '#b26a00',
}
DARK = {
    'current_row': '#333c47',
    'current_cell': '#7aa7ff',
    'problem': '#5c2b2e',
    'computed': '#3d3d3d',
    'excluded': '#3d3261',
    'outside': '#333333',
    'error_text': '#ff8a80',
    'warning_text': '#ffb74d',
}


def is_dark():
    """시스템 팔레트가 다크 테마인지. 앱이 없으면 라이트로 간주한다."""
    app = QGuiApplication.instance()
    if app is None:
        return False
    return app.palette().color(QPalette.Window).lightness() < 128


def _pair(key):
    return QColor(DARK[key] if is_dark() else LIGHT[key])


def palette():
    """현재 앱 팔레트. 없으면 None."""
    app = QGuiApplication.instance()
    return app.palette() if app is not None else None


def selection_color():
    """선택 셀 배경. 시스템 강조색을 그대로 써서 테마와 맞춘다."""
    pal = palette()
    return pal.color(QPalette.Highlight) if pal is not None else QColor(LIGHT['current_cell'])


def selected_text_color():
    """선택 셀 글자. 시스템 강조 글자색이라 배경과 항상 대비된다."""
    pal = palette()
    return pal.color(QPalette.HighlightedText) if pal is not None else QColor('#ffffff')


def normal_text_color():
    pal = palette()
    return pal.color(QPalette.WindowText) if pal is not None else QColor('#000000')


def current_row_color():
    return _pair('current_row')


def current_cell_color():
    return _pair('current_cell')


def problem_color():
    return _pair('problem')


def computed_color():
    return _pair('computed')


def excluded_color():
    return _pair('excluded')


def outside_color():
    return _pair('outside')


def error_text():
    return _pair('error_text')


def warning_text():
    return _pair('warning_text')


def error_stylesheet():
    return f'color: {error_text().name()};'


def mix(first, second, ratio):
    """두 QColor를 섞는다. ratio만큼 first를 섞는다."""
    return QColor(
        round(first.red() * ratio + second.red() * (1 - ratio)),
        round(first.green() * ratio + second.green() * (1 - ratio)),
        round(first.blue() * ratio + second.blue() * (1 - ratio)),
    )


def nav_stylesheet():
    """좌측 메뉴. 시스템 팔레트에서 뽑아 라이트/다크 어디서도 어울리게 한다.

    예전에는 밝은 회색 배경(#e8edf3)에 흰 글자가 올라가 다크 테마에서
    메뉴 글자가 안 보였다. 이제 배경·글자·선택을 모두 팔레트에서 가져온다.
    """
    pal = palette()
    if pal is None:
        return ''
    window = pal.color(QPalette.Window).name()
    text = pal.color(QPalette.WindowText).name()
    highlight = pal.color(QPalette.Highlight).name()
    highlighted = pal.color(QPalette.HighlightedText).name()
    hover = mix(pal.color(QPalette.Highlight), pal.color(QPalette.Window), 0.25).name()
    return (f'#nav {{ background-color: {window}; }}'
            f'QToolButton {{ color: {text}; border: none; padding: 10px 6px; text-align: left; }}'
            f'QToolButton:checked {{ background-color: {highlight}; color: {highlighted}; font-weight: bold; }}'
            f'QToolButton:hover:!checked {{ background-color: {hover}; }}')


def stale_stylesheet():
    """'입력이 바뀌었습니다' 알림 줄. 글자색을 함께 지정해야 다크에서도 보인다."""
    if is_dark():
        return ('QFrame { background-color: #4a3f1e; border: 1px solid #8a7638; }'
                'QLabel { border: none; color: #ffe9a8; }')
    return ('QFrame { background-color: #fff4c2; border: 1px solid #e0c060; }'
            'QLabel { border: none; color: #5a4a00; }')


def legend_stylesheet(color):
    """범례 칩. 칩 밝기에 맞는 글자색을 함께 지정한다."""
    ink = '#000000' if color.lightness() >= 128 else '#ffffff'
    return f'background-color: {color.name()}; color: {ink}; border: 1px solid #c8c8c8; padding: 2px;'
