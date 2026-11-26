"""시스템 테마(라이트/다크)에 맞는 색상.

밝은 색을 하드코딩하면 다크 테마에서 흰 글자와 겹쳐 안 보인다(좌측 메뉴,
선택 셀, 계산 칸, 범례). 모든 의미 색상은 이 모듈의 함수로 구하고, 그릴
때마다 현재 팔레트를 기준으로 고른다. `is_dark()`을 바꾸면 라이트/다크
짝을 테스트에서 확인할 수 있다.
"""
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPalette, QPen

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
    'pin_fixed': '#1a73e8',
    'pin_partial': '#d97706',
    'pin_unfixed': '#8c96a0',
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
    'pin_fixed': '#7aa7ff',
    'pin_partial': '#ffb74d',
    'pin_unfixed': '#8b949e',
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


def pin_fixed_color():
    return _pair('pin_fixed')


def pin_partial_color():
    return _pair('pin_partial')


def pin_unfixed_color():
    return _pair('pin_unfixed')


def pin_icon_size(rect_height):
    """셀 높이에 맞춘 압정 아이콘 한 변 크기."""
    return max(14, min(rect_height - 6, 20))


def pin_hit_rect(cell_rect):
    """셀 왼쪽 끝의 압정 클릭 영역."""
    side = pin_icon_size(cell_rect.height())
    return QRect(cell_rect.left() + 2, cell_rect.top(), side + 6, cell_rect.height())


def pin_icon_rect(cell_rect):
    """셀 왼쪽 끝에 압정 아이콘을 그릴 정사각형 영역."""
    side = pin_icon_size(cell_rect.height())
    hit = pin_hit_rect(cell_rect)
    return QRect(hit.left() + (hit.width() - side) // 2,
                 hit.top() + (hit.height() - side) // 2,
                 side, side)


def pin_text_offset(cell_rect):
    """압정 오른쪽에서 텍스트가 시작할 가로 여백."""
    return pin_icon_size(cell_rect.height()) + 10


def draw_pin_icon(painter, rect, state, selected=False):
    """압정 벡터 아이콘을 그린다. state는 'fixed' | 'partial' | 'unfixed'."""
    if state not in ('fixed', 'partial', 'unfixed'):
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    side = min(rect.width(), rect.height())
    cx = rect.left() + rect.width() / 2.0
    cy = rect.top() + rect.height() / 2.0
    painter.translate(cx, cy)
    scale = side / 16.0
    painter.scale(scale, scale)
    if state == 'unfixed':
        # 미고정은 비스듬히 기울어진 빈 윤곽선 압정으로 표시한다.
        painter.rotate(30.0)
    painter.translate(-8.0, -8.0)

    body = QPainterPath()
    body.moveTo(5.0, 2.2)
    body.lineTo(11.0, 2.2)
    body.lineTo(11.0, 4.0)
    body.lineTo(9.8, 4.4)
    body.lineTo(10.5, 8.2)
    body.lineTo(12.6, 8.8)
    body.lineTo(12.6, 10.2)
    body.lineTo(3.4, 10.2)
    body.lineTo(3.4, 8.8)
    body.lineTo(5.5, 8.2)
    body.lineTo(6.2, 4.4)
    body.lineTo(5.0, 4.0)
    body.closeSubpath()

    if selected:
        base = selected_text_color()
        if state == 'fixed':
            stroke = base
            fill = base
        elif state == 'partial':
            stroke = base
            fill = pin_partial_color()
        else:
            stroke = QColor(base)
            stroke.setAlpha(160)
            fill = None
    else:
        if state == 'fixed':
            stroke = pin_fixed_color()
            fill = stroke
        elif state == 'partial':
            stroke = pin_partial_color()
            fill = stroke
        else:
            stroke = pin_unfixed_color()
            fill = None

    if state == 'fixed':
        painter.setPen(QPen(stroke, 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(fill)
        painter.drawPath(body)
        painter.setPen(QPen(stroke, 1.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(8.0, 10.2), QPointF(8.0, 14.4))
    elif state == 'partial':
        # 부분 고정은 수직 압정의 왼쪽 절반을 채워 미고정·전체 고정과 모양·색 모두로 구분한다.
        painter.save()
        painter.setClipRect(QRectF(0.0, 0.0, 8.0, 16.0))
        painter.fillPath(body, fill)
        painter.restore()
        painter.setPen(QPen(stroke, 1.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(body)
        painter.setPen(QPen(stroke, 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(8.0, 10.2), QPointF(8.0, 14.4))
    else:
        painter.setPen(QPen(stroke, 1.15, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(body)
        painter.setPen(QPen(stroke, 1.2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(8.0, 10.2), QPointF(8.0, 14.0))

    painter.restore()


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


def legend_stylesheet(color):
    """범례 칩. 칩 밝기에 맞는 글자색을 함께 지정한다."""
    ink = '#000000' if color.lightness() >= 128 else '#ffffff'
    return f'background-color: {color.name()}; color: {ink}; border: 1px solid #c8c8c8; padding: 2px;'
