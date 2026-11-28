"""YAML 원본을 VS Code 스타일로 보고 편집·검증·저장하는 PySide6 편집기 위젯."""
import difflib
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QRect, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPalette, QPen,
                             QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument)
from PySide6.QtWidgets import (QCheckBox, QCompleter, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextDocumentLayout,
                               QPlainTextEdit, QPushButton, QSplitter, QTabBar, QTextEdit, QToolButton,
                               QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
import yaml

from . import theme
from .document import (DocumentError, PeopleDoc, PlanDoc, ProjectList, dump_people, dump_plan,
                       dump_projects, read_people, read_plan, read_projects)
from .engine import calculate
from .loading import load_inputs, load_plan
from .planning import review_plan

DARK_COLORS = {
    'bg': '#1E1E1E',
    'fg': '#D4D4D4',
    'gutter_bg': '#1E1E1E',
    'gutter_fg': '#858585',
    'gutter_border': '#2D2D2D',
    'current_line': '#2A2D2E',
    'selection': '#264F78',
    'indent_guide': '#404040',
    'key': '#9CDCFE',
    'string': '#CE9178',
    'date': '#4EC9B0',
    'number': '#B5CEA8',
    'keyword': '#569CD6',
    'marker': '#C586C0',
    'comment': '#6A9955',
    'tab_bg': '#6E1B1B',
    'tab_fg': '#FF8080',
    'error_line': '#5A1D1D',
    'error_wave': '#F44747',
    'warning_line': '#4D3B14',
    'warning_wave': '#CCA700',
    'search_bg': '#613214',
    'search_current_bg': '#9E5623',
    'fold_icon': '#C5C5C5',
}

LIGHT_COLORS = {
    'bg': '#FFFFFF',
    'fg': '#1F1F1F',
    'gutter_bg': '#F3F3F3',
    'gutter_fg': '#6E7681',
    'gutter_border': '#D0D7DE',
    'current_line': '#F0F6FC',
    'selection': '#CFE4FA',
    'indent_guide': '#D8DEE4',
    'key': '#0550AE',
    'string': '#A31515',
    'date': '#0A7E66',
    'number': '#098658',
    'keyword': '#0000FF',
    'marker': '#AF00DB',
    'comment': '#008000',
    'tab_bg': '#FDE7E9',
    'tab_fg': '#A4262C',
    'error_line': '#FDE7E9',
    'error_wave': '#D1242F',
    'warning_line': '#FFF8C5',
    'warning_wave': '#9A6700',
    'search_bg': '#F8E3A1',
    'search_current_bg': '#F2C14E',
    'fold_icon': '#57606A',
}

_DATE_RE = re.compile(r'["\']?\d{4}[.-]\d{1,2}[.-]\d{1,2}\.?(?:~\d{4}[.-]\d{1,2}[.-]\d{1,2}\.?)?["\']?')
_KEY_RE = re.compile(r'^(\s*(?:-\s+)?)([^#\s][^:\n]*?)(:)(?=\s|$)')
_LIST_MARKER_RE = re.compile(r'^(\s*)(-)(?=\s|$)')
_NUMBER_RE = re.compile(r'(?<![\w.-])(-?\d+(?:\.\d+)?)(?![\w.-])')
_KEYWORD_RE = re.compile(r'(?<![\w.-])(true|false|null|True|False|TRUE|FALSE|NULL|~)(?![\w.-])')
_LINE_NO_RE = re.compile(r'(?:line\s+|줄\s*)(\d+)|(\d+)\s*번째\s*줄', re.IGNORECASE)

DOMAIN_KEYS = {
    'projects': ['name:', 'alias:', 'period:', 'yearly:', 'allocated_manpower:', 'excluded_months:', 'metadata:'],
    'people': ['year:', 'stage:', 'people:', 'name:', 'allocations:', 'fixed:', 'absences:', 'period:', 'reason:',
               'metadata:'],
    'plan': ['year:', 'stage:', 'people:', 'name:', 'draft:', 'priorities:', 'allocations:', 'absences:', 'period:',
             'reason:', 'metadata:'],
}

ALIAS_SECTIONS = {'allocations', 'fixed', 'draft', 'priorities'}

SNIPPETS = {
    'projects': [
        ('새 사업 블록',
         '  - name: 신규 사업명\n'
         '    alias: 신규약칭\n'
         '    period: "2026-01-01~2026-12-31"\n'
         '    yearly:\n'
         '      2026:\n'
         '        allocated_manpower: "1.00000"\n'
         '        excluded_months: []\n'),
        ('연도 배정 블록',
         '      2026:\n'
         '        allocated_manpower: "1.00000"\n'
         '        excluded_months: []\n'),
    ],
    'people': [
        ('새 사람 블록',
         '  - name: 새 사람\n'
         '    allocations:\n'
         '      사업약칭: "1.00000"\n'),
        ('휴직 기간 블록',
         '    absences:\n'
         '      - period: "2026-01-01~2026-01-31"\n'
         '        reason: 휴직\n'),
        ('월 고정 12개월 블록',
         '    fixed:\n'
         '      사업약칭: ["0.00000", "0.00000", "0.00000", "0.00000", "0.00000", "0.00000",\n'
         '                 "0.00000", "0.00000", "0.00000", "0.00000", "0.00000", "0.00000"]\n'),
    ],
    'plan': [
        ('새 사람 계획 블록',
         '  - name: 새 사람\n'
         '    draft:\n'
         '      사업약칭: "0.00000"\n'),
        ('명시 순위(priorities) 블록',
         '    priorities:\n'
         '      사업약칭: 1\n'),
        ('휴직 기간 블록',
         '    absences:\n'
         '      - period: "2026-01-01~2026-01-31"\n'
         '        reason: 휴직\n'),
    ],
}


def editor_colors(use_dark=True):
    """편집기 색상 사전. 고대비 모드에서는 앱 팔레트를 존중한다."""
    if theme.high_contrast_active():
        pal = theme.palette()
        if pal is not None:
            base = dict(DARK_COLORS if pal.color(QPalette.Base).lightness() < 128 else LIGHT_COLORS)
            base['bg'] = pal.color(QPalette.Base).name()
            base['fg'] = pal.color(QPalette.Text).name()
            base['gutter_bg'] = pal.color(QPalette.Window).name()
            base['gutter_fg'] = pal.color(QPalette.Text).name()
            base['selection'] = pal.color(QPalette.Highlight).name()
            return base
    return dict(DARK_COLORS if use_dark else LIGHT_COLORS)


YAML_FONT_OFFSET = 2


def coding_font(point_size=12):
    """한글/영문 2:1 정렬에 유리한 코딩 전용 고정폭 폰트를 고른다."""
    preferred = ('D2Coding', 'Sarasa Mono K', 'Noto Sans Mono CJK KR',
                 'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', 'Courier New')
    available = set(QFontDatabase.families())
    chosen = next((name for name in preferred if name in available), None)
    if chosen is None:
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    else:
        font = QFont(chosen)
    font.setFixedPitch(True)
    font.setPointSize(max(7, min(28, int(point_size))))
    return font


def _strip_inline_comment(line):
    in_single = False
    in_double = False
    escaped = False
    for index, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == '\\' and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '#' and not in_single and not in_double:
            if index == 0 or line[index - 1].isspace():
                return line[:index]
    return line


def _leading_spaces(line):
    return len(line) - len(line.lstrip(' '))


def yaml_fold_ranges(lines):
    """들여쓰기와 리스트 계층을 기준으로 접기 범위 {start_line: end_line}를 구한다."""
    parsed = []
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        is_blank = not stripped or stripped.startswith('#')
        indent = _leading_spaces(raw) if not is_blank else None
        is_list = stripped.startswith('- ') or stripped == '-'
        code = _strip_inline_comment(raw).rstrip() if not is_blank else ''
        is_mapping_header = bool(code.endswith(':') and not is_list)
        parsed.append((indent, is_blank, is_list, is_mapping_header))

    ranges = {}
    n = len(lines)
    for start in range(n):
        indent, is_blank, is_list, is_header = parsed[start]
        if is_blank:
            continue
        next_idx = next((k for k in range(start + 1, n) if not parsed[k][1]), None)
        if next_idx is None:
            continue
        next_indent, _, next_list, _ = parsed[next_idx]
        # `projects:` 바로 아래 같은 들여쓰기의 `- name:`도 자식으로 묶는다.
        child_by_indent = next_indent > indent
        child_by_list = is_header and next_indent == indent and next_list
        if not (child_by_indent or child_by_list):
            continue

        end = start
        for k in range(next_idx, n):
            k_indent, k_blank, k_list, _ = parsed[k]
            if k_blank:
                end = k
                continue
            if k_indent > indent:
                end = k
                continue
            if child_by_list and k_indent == indent and k_list:
                end = k
                continue
            break
        while end > start and parsed[end][1]:
            end -= 1
        if end > start:
            ranges[start] = end
    return ranges


@dataclass
class OutlineItem:
    line: int
    label: str
    level: int
    key: str


def yaml_outline_items(lines, kind):
    """문서 종류에 맞는 사업/사람·섹션 목차 목록을 추출한다."""
    items = []
    in_root_list = False
    current_indent = None
    in_yearly = False
    yearly_indent = None
    in_project_item = False
    pending_project = None

    def flush_project():
        nonlocal pending_project
        if pending_project is None:
            return
        line, name, alias = pending_project
        label = f'{alias} — {name}' if alias and name and alias != name else (alias or name or '(사업)')
        items.append(OutlineItem(line, label, 0, alias or name))
        pending_project = None

    for idx, raw in enumerate(lines):
        code = _strip_inline_comment(raw).rstrip()
        if not code.strip():
            continue
        indent = _leading_spaces(code)
        stripped = code.strip()
        if indent == 0 and stripped in ('projects:', 'people:'):
            in_root_list = (stripped == ('projects:' if kind == 'projects' else 'people:'))
            current_indent = None
            in_yearly = False
            in_project_item = False
            continue
        if indent == 0 and not stripped.startswith('-'):
            flush_project()
            in_root_list = False
            in_yearly = False
            in_project_item = False
            continue
        if not in_root_list:
            continue
        if stripped.startswith('- '):
            if current_indent is None or indent <= current_indent:
                flush_project()
                current_indent = indent
                in_yearly = False
                in_project_item = (kind == 'projects')
                rest = stripped[2:].strip()
                if rest.startswith('name:'):
                    val = rest[5:].strip().strip('"\'')
                    if kind == 'projects':
                        pending_project = [idx, val, '']
                    else:
                        items.append(OutlineItem(idx, val or '(이름 없음)', 0, val))
                elif kind == 'projects':
                    pending_project = [idx, '', '']
                continue
        if kind == 'projects' and in_project_item:
            if stripped.startswith('name:') and pending_project is not None:
                pending_project[1] = stripped[5:].strip().strip('"\'')
            elif stripped.startswith('alias:') and pending_project is not None:
                pending_project[2] = stripped[6:].strip().strip('"\'')
            elif stripped == 'yearly:':
                flush_project()
                in_yearly = True
                yearly_indent = indent
            elif in_yearly and yearly_indent is not None and indent > yearly_indent and stripped.endswith(':'):
                year_label = stripped[:-1].strip().strip('"\'')
                if year_label not in ('allocated_manpower', 'excluded_months'):
                    items.append(OutlineItem(idx, f'{year_label}년', 1, year_label))
            elif in_yearly and yearly_indent is not None and indent <= yearly_indent:
                in_yearly = False
        elif kind in ('people', 'plan') and current_indent is not None and indent > current_indent:
            key = stripped[:-1].strip() if stripped.endswith(':') else ''
            if key in ('allocations', 'fixed', 'draft', 'priorities', 'absences'):
                items.append(OutlineItem(idx, key, 1, key))
    flush_project()
    return items


def yaml_section_at(lines, line_index):
    """커서/호버 줄이 속한 가장 가까운 상위 매핑 키 이름을 반환한다."""
    if not (0 <= line_index < len(lines)):
        return ''
    cur_raw = lines[line_index]
    cur_code = _strip_inline_comment(cur_raw)
    cur_indent = _leading_spaces(cur_raw) if cur_code.strip() else _leading_spaces(cur_raw)
    for idx in range(line_index, -1, -1):
        code = _strip_inline_comment(lines[idx]).rstrip()
        if not code.strip():
            continue
        indent = _leading_spaces(code)
        stripped = code.strip()
        if idx == line_index and not cur_code.strip():
            pass
        elif indent >= cur_indent and idx < line_index:
            continue
        if stripped.startswith('- '):
            rest = stripped[2:].strip()
            if rest.endswith(':'):
                return rest[:-1].strip()
            if indent < cur_indent:
                return 'item'
        elif ':' in stripped:
            key = stripped.split(':', 1)[0].strip().strip('"\'')
            if idx < line_index and indent < cur_indent:
                return key
    return ''


def token_at_position(line_text, col):
    """줄 내 지정 열 위치의 식별자(사업 약칭 등)를 추출한다."""
    if not line_text or col < 0 or col > len(line_text):
        return ''
    sep = set(' \t:,\'"{}[]#')
    pos = min(col, len(line_text) - 1) if line_text else 0
    if pos < 0 or line_text[pos] in sep:
        if pos > 0 and line_text[pos - 1] not in sep:
            pos -= 1
        else:
            return ''
    left = pos
    while left > 0 and line_text[left - 1] not in sep:
        left -= 1
    right = pos
    while right < len(line_text) and line_text[right] not in sep:
        right += 1
    return line_text[left:right].strip()


def clean_yaml_text(text):
    """하드 탭을 공백 2칸으로 바꾸고 줄 끝 공백을 정리한다."""
    if not text:
        return ''
    cleaned = [line.replace('\t', '  ').rstrip() for line in text.splitlines()]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return '\n'.join(cleaned) + ('\n' if cleaned else '')


def yaml_diff_summary(old_text, new_text, label='YAML'):
    """변경된 줄 수 요약과 unified diff 문자열을 반환한다."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=f'{label} (저장됨)', tofile=f'{label} (편집 중)', n=3))
    added = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
    removed = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
    return added, removed, ''.join(diff)


def extract_error_line(message):
    """오류 메시지에서 1-based 줄 번호를 추출한다. 없으면 None."""
    match = _LINE_NO_RE.search(message or '')
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def find_item_line(lines, kind, target_name):
    """표 편집기에서 지정한 사업 약칭/이름 또는 사람 이름의 줄(1-based)을 찾는다."""
    target = (target_name or '').strip()
    if not target:
        return 1
    if kind == 'projects':
        candidate = None
        for idx, raw in enumerate(lines, 1):
            stripped = _strip_inline_comment(raw).strip()
            if stripped.startswith('- name:'):
                candidate = idx
                if stripped[7:].strip().strip('"\'') == target:
                    return idx
            elif stripped.startswith('name:') and candidate is not None:
                if stripped[5:].strip().strip('"\'') == target:
                    return candidate
            elif stripped.startswith('alias:'):
                if stripped[6:].strip().strip('"\'') == target:
                    return candidate or idx
    else:
        for idx, raw in enumerate(lines, 1):
            stripped = _strip_inline_comment(raw).strip()
            if stripped.startswith('- name:') and stripped[7:].strip().strip('"\'') == target:
                return idx
    return 1


class YamlSyntaxHighlighter(QSyntaxHighlighter):
    """YAML 키·문자열·날짜·숫자·주석·금지 탭 문자를 강조한다."""

    def __init__(self, document, colors=None, parent=None):
        super().__init__(parent if parent is not None else document)
        self.colors = colors or editor_colors(True)
        self._build_formats()
        if parent is not None and document is not None:
            self.setDocument(document)

    def set_colors(self, colors):
        self.colors = dict(colors)
        self._build_formats()
        self.rehighlight()

    def _fmt(self, color_key, *, bold=False, italic=False, bg_key=None):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors[color_key]))
        if bg_key:
            fmt.setBackground(QColor(self.colors[bg_key]))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def _build_formats(self):
        self.fmt_key = self._fmt('key', bold=True)
        self.fmt_string = self._fmt('string')
        self.fmt_date = self._fmt('date')
        self.fmt_number = self._fmt('number')
        self.fmt_keyword = self._fmt('keyword', bold=True)
        self.fmt_marker = self._fmt('marker', bold=True)
        self.fmt_comment = self._fmt('comment', italic=True)
        self.fmt_tab = self._fmt('tab_fg', bg_key='tab_bg', bold=True)

    def highlightBlock(self, text):
        # 1. 주석 경계 찾기 (따옴표 밖의 #)
        comment_start = None
        in_single = False
        in_double = False
        escaped = False
        for i, ch in enumerate(text):
            if escaped:
                escaped = False
                continue
            if ch == '\\' and in_double:
                escaped = True
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == '#' and not in_single and not in_double and (i == 0 or text[i - 1].isspace()):
                comment_start = i
                break

        code = text if comment_start is None else text[:comment_start]

        # 2. 리스트 마커
        m_list = _LIST_MARKER_RE.match(code)
        if m_list:
            self.setFormat(m_list.start(2), 1, self.fmt_marker)

        # 3. 매핑 키
        m_key = _KEY_RE.match(code)
        value_start = 0
        if m_key:
            self.setFormat(m_key.start(2), len(m_key.group(2)), self.fmt_key)
            self.setFormat(m_key.start(3), 1, self.fmt_marker)
            value_start = m_key.end(3)

        # 4. 값 영역 내 키워드/숫자/문자열/날짜
        val = code[value_start:]
        for m in _KEYWORD_RE.finditer(val):
            self.setFormat(value_start + m.start(1), len(m.group(1)), self.fmt_keyword)
        for m in _NUMBER_RE.finditer(val):
            self.setFormat(value_start + m.start(1), len(m.group(1)), self.fmt_number)

        # 따옴표 문자열 (내부가 날짜/기간이면 날짜 색 적용)
        idx = value_start
        while idx < len(code):
            ch = code[idx]
            if ch in ('"', "'"):
                quote = ch
                end = idx + 1
                esc = False
                while end < len(code):
                    c = code[end]
                    if esc:
                        esc = False
                    elif c == '\\' and quote == '"':
                        esc = True
                    elif c == quote:
                        end += 1
                        break
                    end += 1
                token = code[idx:end]
                fmt = self.fmt_date if _DATE_RE.fullmatch(token) else self.fmt_string
                self.setFormat(idx, end - idx, fmt)
                idx = end
            else:
                idx += 1

        for m in _DATE_RE.finditer(val):
            if not (val[m.start()] in ('"', "'")):
                self.setFormat(value_start + m.start(), m.end() - m.start(), self.fmt_date)

        # 5. 주석
        if comment_start is not None:
            self.setFormat(comment_start, len(text) - comment_start, self.fmt_comment)

        # 6. 금지된 하드 탭(\t) 최우선 강조
        for i, ch in enumerate(text):
            if ch == '\t':
                self.setFormat(i, 1, self.fmt_tab)


class _EditorGutter(QWidget):
    """줄 번호, 폴딩 마커(▾/▸), 오류/경고 도트를 그리는 좌측 여백."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.editor.handle_gutter_click(event.position().toPoint())
            return
        super().mousePressEvent(event)


class YamlCodeEditor(QPlainTextEdit):
    """YAML 전용 들여쓰기·폴딩·가이드선·자동완성·호버 툴팁·실시간 오류 표시 편집기."""

    zoom_requested = Signal(int)
    save_requested = Signal()
    find_requested = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = editor_colors(True)
        self._fold_ranges = {}
        self._folded_starts = set()
        self._line_diagnostics = {}  # line(1-based) -> (severity, message)
        self._search_matches = []    # list[(start_pos, length)]
        self._current_match_index = -1
        self._alias_provider = lambda: []
        self._tooltip_provider = lambda alias, section: ''
        self._kind = 'projects'

        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setCenterOnScroll(True)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance('  '))
        self.setMouseTracking(True)

        self._gutter = _EditorGutter(self)
        self._highlighter = YamlSyntaxHighlighter(self.document(), self._colors, parent=self)
        self._completer = QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._refresh_extra_selections)
        self.textChanged.connect(self._on_text_changed)

        self.apply_editor_style(point_size=12, use_dark=True)
        self._update_gutter_width(0)

    def set_kind(self, kind):
        self._kind = kind

    def set_providers(self, alias_provider, tooltip_provider):
        self._alias_provider = alias_provider
        self._tooltip_provider = tooltip_provider

    def apply_editor_style(self, *, point_size=None, use_dark=True):
        if point_size is not None:
            self.setFont(coding_font(point_size))
        self._colors = editor_colors(use_dark)
        c = self._colors
        self.setStyleSheet(
            f'QPlainTextEdit {{ background-color: {c["bg"]}; color: {c["fg"]}; '
            f'selection-background-color: {c["selection"]}; border: none; }}'
        )
        self._highlighter.set_colors(c)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance('  '))
        self._update_gutter_width(0)
        self._refresh_extra_selections()
        self.viewport().update()

    def set_document_buffer(self, document, *, kind='projects', folded_starts=None, cursor_pos=0):
        """다른 파일 탭의 QTextDocument로 전환하며 강조기·폴딩·커서를 복원한다."""
        self._kind = kind
        self.setDocument(document)
        self._highlighter.setDocument(document)
        self._highlighter.rehighlight()
        self._folded_starts = set(folded_starts or ())
        self._recompute_folds()
        self._apply_folding()
        cursor = self.textCursor()
        cursor.setPosition(max(0, min(cursor_pos, max(0, document.characterCount() - 1))))
        self.setTextCursor(cursor)
        self._update_gutter_width(0)
        self._refresh_extra_selections()

    def folded_starts(self):
        return set(self._folded_starts)

    # --- 거터(줄 번호 + 폴딩 + 진단 마커) ---

    def gutter_width(self):
        digits = max(3, len(str(max(1, self.blockCount()))))
        char_w = self.fontMetrics().horizontalAdvance('9')
        return 14 + digits * char_w + 18

    def _update_gutter_width(self, _):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        painter = QPainter(self._gutter)
        c = self._colors
        painter.fillRect(event.rect(), QColor(c['gutter_bg']))
        painter.setPen(QColor(c['gutter_border']))
        painter.drawLine(self._gutter.width() - 1, event.rect().top(),
                         self._gutter.width() - 1, event.rect().bottom())

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        cur_line = self.textCursor().blockNumber()
        fold_w = 16
        num_right = self._gutter.width() - fold_w - 4

        while block.isValid():
            if block.isVisible():
                rect = self.blockBoundingGeometry(block).translated(self.contentOffset())
                top = round(rect.top())
                h = round(rect.height())
                if top > event.rect().bottom():
                    break
                if top + h >= event.rect().top():
                    # 오류/경고 도트
                    diag = self._line_diagnostics.get(block_number + 1)
                    if diag:
                        sev, _ = diag
                        dot_color = QColor(c['error_wave'] if sev == 'ERROR' else c['warning_wave'])
                        painter.setBrush(dot_color)
                        painter.setPen(Qt.NoPen)
                        painter.drawEllipse(3, top + max(2, (h - 6) // 2), 6, 6)

                    # 줄 번호
                    painter.setPen(QColor(c['fg'] if block_number == cur_line else c['gutter_fg']))
                    painter.drawText(10, top, num_right - 10, h, Qt.AlignRight | Qt.AlignVCenter,
                                     str(block_number + 1))

                    # 폴딩 아이콘
                    if block_number in self._fold_ranges:
                        painter.setPen(QColor(c['fold_icon']))
                        marker = '▸' if block_number in self._folded_starts else '▾'
                        painter.drawText(self._gutter.width() - fold_w - 1, top, fold_w, h,
                                         Qt.AlignCenter, marker)
            block = block.next()
            block_number += 1

    def handle_gutter_click(self, pos):
        block = self.firstVisibleBlock()
        while block.isValid():
            if block.isVisible():
                rect = self.blockBoundingGeometry(block).translated(self.contentOffset())
                if rect.top() > pos.y():
                    break
                if rect.top() <= pos.y() < rect.bottom():
                    line = block.blockNumber()
                    if line in self._fold_ranges and pos.x() >= self._gutter.width() - 22:
                        self.toggle_fold(line)
                    else:
                        cursor = self.textCursor()
                        cursor.setPosition(block.position())
                        self.setTextCursor(cursor)
                    return
            block = block.next()

    # --- 들여쓰기 가이드 선 그리기 ---

    def paintEvent(self, event):
        super().paintEvent(event)
        step = self.fontMetrics().horizontalAdvance('  ')
        if step <= 0:
            return
        painter = QPainter(self.viewport())
        guide = QColor(self._colors['indent_guide'])
        guide.setAlpha(110)
        painter.setPen(QPen(guide, 1))
        offset_x = self.contentOffset().x() + self.document().documentMargin()
        block = self.firstVisibleBlock()
        while block.isValid():
            if block.isVisible():
                rect = self.blockBoundingGeometry(block).translated(self.contentOffset())
                top = round(rect.top())
                h = round(rect.height())
                if top > event.rect().bottom():
                    break
                if top + h >= event.rect().top():
                    text = block.text()
                    indent = _leading_spaces(text) if text.strip() else 0
                    for level in range(2, indent + 1, 2):
                        x = round(offset_x + level * step)
                        painter.drawLine(x, top, x, top + h)
            block = block.next()

    # --- 폴딩 / 언폴딩 ---

    def _on_text_changed(self):
        self._recompute_folds()
        self._gutter.update()

    def _recompute_folds(self):
        lines = self.toPlainText().splitlines()
        self._fold_ranges = yaml_fold_ranges(lines)
        self._folded_starts = {line for line in self._folded_starts if line in self._fold_ranges}

    def _apply_folding(self, anchor_line=None):
        doc = self.document()
        before_top = None
        if anchor_line is not None:
            anchor_block = doc.findBlockByNumber(anchor_line)
            if anchor_block.isValid() and anchor_block.isVisible():
                before_top = self.blockBoundingGeometry(anchor_block).translated(self.contentOffset()).top()
        saved_scroll = self.verticalScrollBar().value()

        hidden = set()
        for start in self._folded_starts:
            end = self._fold_ranges.get(start)
            if end is not None:
                hidden.update(range(start + 1, end + 1))
        block = doc.begin()
        idx = 0
        while block.isValid():
            block.setVisible(idx not in hidden)
            block = block.next()
            idx += 1
        # 커서가 접힌 줄 안에 있으면 시작 줄로 올린다.
        cur = self.textCursor()
        if cur.blockNumber() in hidden:
            parent = max((s for s in self._folded_starts if s < cur.blockNumber() <= self._fold_ranges[s]),
                         default=0)
            target = doc.findBlockByNumber(parent)
            if target.isValid():
                cur.setPosition(target.position())
                self.setTextCursor(cur)
        doc.markContentsDirty(0, max(1, doc.characterCount()))

        scroll = self.verticalScrollBar()
        scroll.setValue(min(saved_scroll, scroll.maximum()))
        if before_top is not None and anchor_line is not None:
            anchor_block = doc.findBlockByNumber(anchor_line)
            if anchor_block.isValid() and anchor_block.isVisible():
                for _ in range(3):
                    after_top = self.blockBoundingGeometry(anchor_block).translated(self.contentOffset()).top()
                    diff = after_top - before_top
                    line_h = max(1.0, self.blockBoundingRect(anchor_block).height())
                    if abs(diff) < line_h * 0.5:
                        break
                    step = round(diff / line_h) or (1 if diff > 0 else -1)
                    new_val = max(scroll.minimum(), min(scroll.maximum(), scroll.value() + step))
                    if new_val == scroll.value():
                        break
                    scroll.setValue(new_val)
        self.viewport().update()
        self._gutter.update()

    def toggle_fold(self, start_line):
        if start_line not in self._fold_ranges:
            return
        if start_line in self._folded_starts:
            self._folded_starts.remove(start_line)
        else:
            self._folded_starts.add(start_line)
        self._apply_folding(anchor_line=start_line)

    def fold_at_cursor(self, fold=True):
        cur_line = self.textCursor().blockNumber()
        candidates = [s for s, e in self._fold_ranges.items() if s <= cur_line <= e]
        if not candidates:
            return
        target = max(candidates)
        if fold:
            self._folded_starts.add(target)
        else:
            self._folded_starts.discard(target)
        self._apply_folding(anchor_line=target)

    def fold_all(self):
        self._folded_starts = set(self._fold_ranges)
        self._apply_folding()

    def unfold_all(self):
        self._folded_starts.clear()
        self._apply_folding()

    def fold_level_one(self):
        """사업/사람 목록(`- name:` 등) 항목만 남기고 내부 하위 블록은 접는다."""
        self._recompute_folds()
        lines = self.toPlainText().splitlines()
        self._folded_starts = {
            start for start in self._fold_ranges
            if _leading_spaces(lines[start]) >= 2 and not lines[start].lstrip().startswith('- name:')
        }
        # `- name:` 항목 자체는 펼치고, 그 바로 아래(`allocations:`, `yearly:`, `draft:` 등)를 접는다.
        for start in self._fold_ranges:
            stripped = lines[start].lstrip()
            if _leading_spaces(lines[start]) >= 4 or (stripped.endswith(':') and _leading_spaces(lines[start]) >= 2):
                self._folded_starts.add(start)
        self._apply_folding()

    # --- 강조(현재 줄 + 진단 밑줄 + 찾기 일치) ---

    def set_diagnostics(self, line_diagnostics):
        """line_diagnostics: {1-based line: ('ERROR'|'WARNING', message)}."""
        self._line_diagnostics = dict(line_diagnostics)
        self._refresh_extra_selections()
        self._gutter.update()

    def set_search_highlights(self, matches, current_index):
        self._search_matches = list(matches)
        self._current_match_index = current_index
        self._refresh_extra_selections()

    def _refresh_extra_selections(self):
        selections = []
        c = self._colors
        # 현재 줄
        cur_sel = QTextEdit.ExtraSelection()
        cur_sel.format.setBackground(QColor(c['current_line']))
        cur_sel.format.setProperty(QTextCharFormat.FullWidthSelection, True)
        cur_sel.cursor = self.textCursor()
        cur_sel.cursor.clearSelection()
        selections.append(cur_sel)

        # 찾기 결과
        for i, (pos, length) in enumerate(self._search_matches):
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(c['search_current_bg'] if i == self._current_match_index else c['search_bg']))
            cursor = self.textCursor()
            cursor.setPosition(pos)
            cursor.setPosition(pos + length, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)

        # 오류/경고 줄 밑줄
        doc = self.document()
        for line_no, (sev, _) in self._line_diagnostics.items():
            block = doc.findBlockByNumber(line_no - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(c['error_line'] if sev == 'ERROR' else c['warning_line']))
            sel.format.setProperty(QTextCharFormat.FullWidthSelection, True)
            sel.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            sel.format.setUnderlineColor(QColor(c['error_wave'] if sev == 'ERROR' else c['warning_wave']))
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)

        self.setExtraSelections(selections)

    def jump_to_line(self, line_1based):
        doc = self.document()
        target_idx = max(0, min(line_1based - 1, doc.blockCount() - 1))
        # 접혀 있으면 펼친다.
        to_unfold = [s for s, e in self._fold_ranges.items() if s in self._folded_starts and s < target_idx <= e]
        if to_unfold:
            for s in to_unfold:
                self._folded_starts.discard(s)
            self._apply_folding()
        block = doc.findBlockByNumber(target_idx)
        if block.isValid():
            cursor = QTextCursor(block)
            self.setTextCursor(cursor)
            self.centerCursor()
            self.setFocus()

    # --- YAML 전용 키보드 편집 ---

    def _indent_selection(self, outdent=False):
        cursor = self.textCursor()
        doc = self.document()
        if not cursor.hasSelection() and not outdent:
            cursor.insertText('  ')
            return
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        start_block = doc.findBlock(start).blockNumber()
        end_block = doc.findBlock(end).blockNumber()
        if cursor.hasSelection() and end == doc.findBlockByNumber(end_block).position() and end_block > start_block:
            end_block -= 1
        cursor.beginEditBlock()
        for b_num in range(start_block, end_block + 1):
            block = doc.findBlockByNumber(b_num)
            if not block.isValid():
                continue
            line_cursor = QTextCursor(block)
            text = block.text()
            if outdent:
                remove = min(2, _leading_spaces(text))
                if remove:
                    line_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, remove)
                    line_cursor.removeSelectedText()
            elif text.strip() or start_block == end_block:
                line_cursor.insertText('  ')
        cursor.endEditBlock()

    def _insert_newline_indented(self):
        cursor = self.textCursor()
        block = cursor.block()
        line_before = block.text()[:cursor.positionInBlock()]
        indent = ' ' * _leading_spaces(block.text())
        code = _strip_inline_comment(line_before).rstrip()
        if code.endswith(':'):
            indent += '  '
        elif code.lstrip().startswith('- ') and ':' in code:
            indent += '  '
        cursor.insertText('\n' + indent)

    def _toggle_comment(self):
        cursor = self.textCursor()
        doc = self.document()
        start_block = doc.findBlock(cursor.selectionStart()).blockNumber()
        end_block = doc.findBlock(cursor.selectionEnd()).blockNumber()
        if cursor.hasSelection() and cursor.selectionEnd() == doc.findBlockByNumber(end_block).position() and end_block > start_block:
            end_block -= 1
        blocks = [doc.findBlockByNumber(i) for i in range(start_block, end_block + 1)]
        non_empty = [b for b in blocks if b.isValid() and b.text().strip()]
        uncomment = bool(non_empty) and all(b.text().lstrip().startswith('#') for b in non_empty)
        cursor.beginEditBlock()
        for b in blocks:
            if not b.isValid() or not b.text().strip():
                continue
            spaces = _leading_spaces(b.text())
            c = QTextCursor(b)
            c.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, spaces)
            if uncomment:
                remove = 2 if b.text()[spaces:spaces + 2] == '# ' else 1
                c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, remove)
                c.removeSelectedText()
            else:
                c.insertText('# ')
        cursor.endEditBlock()

    def _move_lines(self, direction):
        cursor = self.textCursor()
        doc = self.document()
        start_block = doc.findBlock(cursor.selectionStart()).blockNumber()
        end_block = doc.findBlock(cursor.selectionEnd()).blockNumber()
        if cursor.hasSelection() and cursor.selectionEnd() == doc.findBlockByNumber(end_block).position() and end_block > start_block:
            end_block -= 1
        if direction < 0 and start_block == 0:
            return
        if direction > 0 and end_block >= doc.blockCount() - 1:
            return
        lines = self.toPlainText().split('\n')
        if direction < 0:
            moved = lines.pop(start_block - 1)
            lines.insert(end_block, moved)
            new_start, new_end = start_block - 1, end_block - 1
        else:
            moved = lines.pop(end_block + 1)
            lines.insert(start_block, moved)
            new_start, new_end = start_block + 1, end_block + 1
        cursor.beginEditBlock()
        cursor.select(QTextCursor.Document)
        cursor.insertText('\n'.join(lines))
        cursor.endEditBlock()
        b_start = doc.findBlockByNumber(new_start)
        b_end = doc.findBlockByNumber(new_end)
        sel = QTextCursor(b_start)
        if new_start != new_end:
            sel.setPosition(b_end.position() + len(b_end.text()), QTextCursor.KeepAnchor)
        self.setTextCursor(sel)

    # --- 자동완성 ---

    def _word_prefix(self):
        cursor = self.textCursor()
        text = cursor.block().text()[:cursor.positionInBlock()]
        match = re.search(r'([^\s:\[\],\'"]+)$', text)
        return match.group(1) if match else ''

    def _completion_candidates(self):
        lines = self.toPlainText().splitlines()
        section = yaml_section_at(lines, self.textCursor().blockNumber())
        candidates = []
        if section in ALIAS_SECTIONS and self._kind in ('people', 'plan'):
            candidates.extend(f'{alias}: ' for alias in self._alias_provider())
        candidates.extend(DOMAIN_KEYS.get(self._kind, ()))
        # 중복 제거 순서 유지
        seen, result = set(), []
        for item in candidates:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    def trigger_completion(self, force=False):
        candidates = self._completion_candidates()
        if not candidates:
            self._completer.popup().hide()
            return
        prefix = self._word_prefix()
        if not force and len(prefix) < 1:
            self._completer.popup().hide()
            return
        self._completer.setModel(QStringListModel(candidates, self._completer))
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(max(240, self._completer.popup().sizeHintForColumn(0) + 24))
        self._completer.complete(rect)

    def _insert_completion(self, completion):
        cursor = self.textCursor()
        prefix = self._word_prefix()
        if prefix:
            cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
            cursor.removeSelectedText()
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        if self._completer.popup().isVisible():
            if event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
                event.ignore()
                return

        key, mods = event.key(), event.modifiers()
        if mods == Qt.ControlModifier and key == Qt.Key_S:
            self.save_requested.emit()
            return
        if mods == Qt.ControlModifier and key == Qt.Key_F:
            self.find_requested.emit(False)
            return
        if mods == Qt.ControlModifier and key == Qt.Key_H:
            self.find_requested.emit(True)
            return
        if mods == Qt.ControlModifier and key == Qt.Key_Space:
            self.trigger_completion(force=True)
            return
        if mods == Qt.ControlModifier and key == Qt.Key_Slash:
            self._toggle_comment()
            return
        if mods == Qt.AltModifier and key in (Qt.Key_Up, Qt.Key_Down):
            self._move_lines(-1 if key == Qt.Key_Up else 1)
            return
        if (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier) and key in (Qt.Key_BracketLeft, Qt.Key_BraceLeft):
            self.fold_at_cursor(True)
            return
        if (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier) and key in (Qt.Key_BracketRight, Qt.Key_BraceRight):
            self.fold_at_cursor(False)
            return
        if key == Qt.Key_Tab and not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            self._indent_selection(outdent=False)
            return
        if key == Qt.Key_Backtab or (key == Qt.Key_Tab and (mods & Qt.ShiftModifier)):
            self._indent_selection(outdent=True)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            self._insert_newline_indented()
            return

        super().keyPressEvent(event)
        if event.text() and (event.text().isalnum() or event.text() in ('_', '-')):
            self.trigger_completion(force=False)
        elif not mods and key in (Qt.Key_Backspace, Qt.Key_Delete):
            if self._completer.popup().isVisible():
                self.trigger_completion(force=False)
        else:
            self._completer.popup().hide()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_requested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event):
        if event.type() == QEvent_ToolTip():
            cursor = self.cursorForPosition(event.pos())
            line_no = cursor.blockNumber() + 1
            tip_parts = []
            diag = self._line_diagnostics.get(line_no)
            if diag:
                tip_parts.append(f'[{diag[0]}] {diag[1]}')
            lines = self.toPlainText().splitlines()
            if 0 <= line_no - 1 < len(lines):
                token = token_at_position(lines[line_no - 1], cursor.positionInBlock())
                section = yaml_section_at(lines, line_no - 1)
                if token:
                    project_tip = self._tooltip_provider(token, section)
                    if project_tip:
                        tip_parts.append(project_tip)
            if tip_parts:
                QToolTip.showText(event.globalPos(), '\n\n'.join(tip_parts), self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)


def QEvent_ToolTip():
    from PySide6.QtCore import QEvent
    return QEvent.ToolTip


@dataclass
class _TabBuffer:
    key: str
    title: str
    kind: str
    year: str | None
    encoding_label: str
    saved_text: str
    document: QTextDocument
    dirty: bool = False
    folded_starts: set = field(default_factory=set)
    cursor_pos: int = 0


class DiffPreviewDialog(QDialog):
    """저장 전후 YAML 변경 사항을 줄 단위로 보여 주는 대화상자."""

    def __init__(self, parent, title, added, removed, diff_text, font_size=10):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(780, 520)
        layout = QVBoxLayout(self)
        summary = QLabel(f'추가 {added}줄 · 삭제 {removed}줄' if diff_text else '변경된 내용이 없습니다.')
        layout.addWidget(summary)
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setFont(coding_font(font_size))
        viewer.setPlainText(diff_text or '(변경 없음)')
        c = editor_colors(True)
        viewer.setStyleSheet(f'QPlainTextEdit {{ background-color: {c["bg"]}; color: {c["fg"]}; }}')
        self._apply_diff_highlights(viewer, diff_text)
        layout.addWidget(viewer, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _apply_diff_highlights(viewer, diff_text):
        if not diff_text:
            return
        selections = []
        block = viewer.document().begin()
        while block.isValid():
            text = block.text()
            color = None
            if text.startswith('+') and not text.startswith('+++'):
                color = QColor('#1E3A24')
            elif text.startswith('-') and not text.startswith('---'):
                color = QColor('#4A2226')
            elif text.startswith('@@'):
                color = QColor('#243B55')
            if color is not None:
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(color)
                sel.format.setProperty(QTextCharFormat.FullWidthSelection, True)
                sel.cursor = QTextCursor(block)
                selections.append(sel)
            block = block.next()
        viewer.setExtraSelections(selections)


class YamlEditorPanel(QWidget):
    """파일별 YAML 탭, 아웃라인, 찾기/바꾸기, 실시간 검증, 저장/비교 툴바를 묶은 패널."""

    dirty_changed = Signal(bool)
    zoom_requested = Signal(int)

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self._buffers = {}        # key -> _TabBuffer
        self._tab_keys = []       # tab index -> key
        self._current_key = None
        self._switching_tab = False
        self._syncing_text = False
        self._font_size = 10 + YAML_FONT_OFFSET

        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)

        # 툴바
        self.save_button = QPushButton('저장 (Ctrl+S)')
        self.save_button.clicked.connect(self.save_current)
        self.revert_button = QPushButton('되돌리기')
        self.revert_button.clicked.connect(self.revert_current)
        self.diff_button = QPushButton('변경 비교')
        self.diff_button.clicked.connect(self.show_diff)
        self.clean_button = QPushButton('YAML 정리')
        self.clean_button.clicked.connect(self.clean_current)
        self.snippet_button = QToolButton()
        self.snippet_button.setText('스니펫')
        self.snippet_button.setPopupMode(QToolButton.InstantPopup)
        self.snippet_menu = QMenu(self.snippet_button)
        self.snippet_button.setMenu(self.snippet_menu)
        self.level1_button = QPushButton('1단계 보기')
        self.level1_button.clicked.connect(lambda: self.editor.fold_level_one())
        self.fold_all_button = QPushButton('모두 접기')
        self.fold_all_button.clicked.connect(lambda: self.editor.fold_all())
        self.unfold_all_button = QPushButton('모두 펼치기')
        self.unfold_all_button.clicked.connect(lambda: self.editor.unfold_all())
        self.outline_toggle = QPushButton('목차')
        self.outline_toggle.setCheckable(True)
        self.outline_toggle.setChecked(True)
        self.outline_toggle.toggled.connect(self._toggle_outline)
        self.find_button = QPushButton('찾기 (Ctrl+F)')
        self.find_button.clicked.connect(lambda: self.open_find_bar(False))

        toolbar = QHBoxLayout()
        for widget in (self.save_button, self.revert_button, self.diff_button, self.clean_button,
                       self.snippet_button, self.level1_button, self.fold_all_button,
                       self.unfold_all_button, self.outline_toggle, self.find_button):
            toolbar.addWidget(widget)
        toolbar.addStretch()

        # 찾기 / 바꾸기 바
        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(0, 2, 0, 2)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText('찾을 내용...')
        self.find_input.textChanged.connect(self._refresh_search)
        self.find_input.returnPressed.connect(lambda: self.find_next(True))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText('바꿀 내용...')
        self.case_check = QCheckBox('대소문자')
        self.case_check.toggled.connect(self._refresh_search)
        self.regex_check = QCheckBox('정규식')
        self.regex_check.toggled.connect(self._refresh_search)
        self.prev_match_btn = QPushButton('이전')
        self.prev_match_btn.clicked.connect(lambda: self.find_next(False))
        self.next_match_btn = QPushButton('다음')
        self.next_match_btn.clicked.connect(lambda: self.find_next(True))
        self.replace_btn = QPushButton('바꾸기')
        self.replace_btn.clicked.connect(self.replace_one)
        self.replace_all_btn = QPushButton('모두 바꾸기')
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.match_count_label = QLabel('0건')
        close_find_btn = QPushButton('닫기')
        close_find_btn.clicked.connect(self.close_find_bar)
        for w in (self.find_input, self.replace_input, self.case_check, self.regex_check,
                  self.prev_match_btn, self.next_match_btn, self.replace_btn,
                  self.replace_all_btn, self.match_count_label, close_find_btn):
            find_layout.addWidget(w)
        self.find_bar.hide()

        # 아웃라인 사이드바
        self.outline_panel = QWidget()
        out_layout = QVBoxLayout(self.outline_panel)
        out_layout.setContentsMargins(0, 0, 0, 0)
        self.outline_filter = QLineEdit()
        self.outline_filter.setPlaceholderText('목차 필터...')
        self.outline_filter.textChanged.connect(self._refresh_outline)
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderHidden(True)
        self.outline_tree.itemActivated.connect(self._jump_outline_item)
        self.outline_tree.itemClicked.connect(self._jump_outline_item)
        out_layout.addWidget(self.outline_filter)
        out_layout.addWidget(self.outline_tree, 1)

        # 에디터
        self.editor = YamlCodeEditor(self)
        self.editor.set_providers(self._project_aliases, self._project_tooltip)
        self.editor.save_requested.connect(self.save_current)
        self.editor.find_requested.connect(self.open_find_bar)
        self.editor.zoom_requested.connect(self.zoom_requested.emit)
        self.editor.textChanged.connect(self._on_editor_text_changed)

        main_split = QSplitter(Qt.Horizontal)
        main_split.addWidget(self.outline_panel)
        main_split.addWidget(self.editor)
        main_split.setSizes([210, 950])

        # 하단 검증 패널
        self.status_label = QLabel('정상')
        self.diagnostics_list = QListWidget()
        self.diagnostics_list.setMaximumHeight(130)
        self.diagnostics_list.itemClicked.connect(self._jump_diagnostic)
        self.diagnostics_list.itemActivated.connect(self._jump_diagnostic)

        v_split = QSplitter(Qt.Vertical)
        v_split.addWidget(main_split)
        diag_box = QWidget()
        diag_layout = QVBoxLayout(diag_box)
        diag_layout.setContentsMargins(0, 0, 0, 0)
        diag_layout.addWidget(self.status_label)
        diag_layout.addWidget(self.diagnostics_list)
        v_split.addWidget(diag_box)
        v_split.setSizes([600, 140])

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.addWidget(self.tab_bar)
        root.addLayout(toolbar)
        root.addWidget(self.find_bar)
        root.addWidget(v_split, 1)

        # 패널 내부 로컬 단축키 (메뉴나 표 편집기와 충돌하지 않는다)
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar)
        esc.setContext(Qt.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_find_bar)
        f3 = QShortcut(QKeySequence(Qt.Key_F3), self)
        f3.setContext(Qt.WidgetWithChildrenShortcut)
        f3.activated.connect(lambda: self.find_next(True))
        sf3 = QShortcut(QKeySequence('Shift+F3'), self)
        sf3.setContext(Qt.WidgetWithChildrenShortcut)
        sf3.activated.connect(lambda: self.find_next(False))

        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(300)
        self._validate_timer.timeout.connect(self.run_validation)

    # --- 동기화 및 탭 관리 ---

    def set_font_size(self, size):
        self._font_size = size + YAML_FONT_OFFSET
        self.editor.apply_editor_style(point_size=self._font_size, use_dark=True)

    def apply_theme(self):
        self.editor.apply_editor_style(point_size=self._font_size, use_dark=True)

    def has_dirty(self):
        return any(buf.dirty for buf in self._buffers.values())

    def dirty_keys(self):
        return [key for key in self._tab_keys if key in self._buffers and self._buffers[key].dirty]

    def current_key(self):
        return self._current_key

    def sync_from_workspace(self, *, preferred_key=None, preserve_dirty=True):
        """작업공간의 사업·사람/계획 문서를 YAML 탭으로 동기화한다."""
        w = self.workspace
        specs = []
        p_label = (w.projects_sources[0] if len(w.projects_sources) == 1
                   else f'{len(w.projects_sources)}개 합침' if w.projects_sources else '새 사업')
        p_enc = self._encoding_badge('projects')
        specs.append(('projects', f'사업 ({self._short_name(p_label)})', 'projects', None,
                      p_enc, dump_projects(w.project_editor.projects)))

        docs = w.plan_docs if w.planning else w.people_docs
        dump = dump_plan if w.planning else dump_people
        prefix = 'plan:' if w.planning else 'people:'
        for year in sorted(docs):
            key = prefix + year
            if w.planning:
                src = w.plan_sources.get(year, '')
                short = self._short_name(src) if src else f'새 {year} 계획'
            else:
                srcs = w.people_sources.get(year, [])
                short = (self._short_name(srcs[0]) if len(srcs) == 1
                         else f'{len(srcs)}개 합침' if srcs else f'새 {year} 사람')
            specs.append((key, f'{year} ({short})', 'plan' if w.planning else 'people',
                          year, self._encoding_badge(key), dump(docs[year])))

        old_buffers = self._buffers
        new_buffers = {}
        self._tab_keys = []
        self._switching_tab = True
        try:
            while self.tab_bar.count():
                self.tab_bar.removeTab(0)
            for key, title, kind, year, enc, text in specs:
                prev = old_buffers.get(key)
                if prev is not None and preserve_dirty and prev.dirty:
                    buf = prev
                    buf.title = title
                    buf.encoding_label = enc
                else:
                    if prev is not None:
                        doc = prev.document
                    else:
                        doc = QTextDocument(self)
                        doc.setDocumentLayout(QPlainTextDocumentLayout(doc))
                    self._syncing_text = True
                    try:
                        if doc.toPlainText() != text:
                            doc.setPlainText(text)
                    finally:
                        self._syncing_text = False
                    buf = _TabBuffer(
                        key=key, title=title, kind=kind, year=year,
                        encoding_label=enc, saved_text=text, document=doc,
                        dirty=False,
                        folded_starts=prev.folded_starts if prev is not None else set(),
                        cursor_pos=prev.cursor_pos if prev is not None else 0,
                    )
                new_buffers[key] = buf
                self._tab_keys.append(key)
                self.tab_bar.addTab(self._format_tab_title(buf))
            self._buffers = new_buffers
            target_key = (preferred_key if preferred_key in new_buffers
                          else self._current_key if self._current_key in new_buffers
                          else (prefix + str(w.current_year) if (prefix + str(w.current_year)) in new_buffers
                                else self._tab_keys[0]))
            idx = self._tab_keys.index(target_key)
            self.tab_bar.setCurrentIndex(idx)
            self._activate_buffer(target_key)
        finally:
            self._switching_tab = False
        self.dirty_changed.emit(self.has_dirty())

    def _short_name(self, path_or_label):
        from pathlib import Path
        return Path(path_or_label).name if ('/' in path_or_label or '\\' in path_or_label or path_or_label.endswith('.yaml')) else path_or_label

    def _encoding_badge(self, key):
        from .text_encoding import ENCODINGS
        codes = self.workspace.document_encodings.get(key, set())
        return '/'.join(ENCODINGS[c] for c in sorted(codes)) if codes else 'UTF-8'

    @staticmethod
    def _format_tab_title(buf):
        star = ' *' if buf.dirty else ''
        return f'{buf.title} [{buf.encoding_label}]{star}'

    def _refresh_tab_titles(self):
        for idx, key in enumerate(self._tab_keys):
            buf = self._buffers[key]
            self.tab_bar.setTabText(idx, self._format_tab_title(buf))
        self.dirty_changed.emit(self.has_dirty())

    def _save_current_view_state(self):
        if self._current_key and self._current_key in self._buffers:
            buf = self._buffers[self._current_key]
            buf.folded_starts = self.editor.folded_starts()
            buf.cursor_pos = self.editor.textCursor().position()

    def _activate_buffer(self, key):
        self._save_current_view_state()
        self._current_key = key
        buf = self._buffers[key]
        self._syncing_text = True
        try:
            self.editor.set_document_buffer(buf.document, kind=buf.kind,
                                            folded_starts=buf.folded_starts,
                                            cursor_pos=buf.cursor_pos)
        finally:
            self._syncing_text = False
        self._rebuild_snippet_menu(buf.kind)
        self._refresh_outline()
        self._refresh_search()
        self.run_validation()

    def select_key(self, key, *, prompt_if_dirty=True):
        if key not in self._buffers:
            return False
        idx = self._tab_keys.index(key)
        if self.tab_bar.currentIndex() == idx:
            return True
        if prompt_if_dirty and not self.confirm_leave_tab():
            return False
        self._switching_tab = True
        try:
            self.tab_bar.setCurrentIndex(idx)
            self._activate_buffer(key)
        finally:
            self._switching_tab = False
        return True

    def _on_tab_changed(self, new_idx):
        if self._switching_tab or not (0 <= new_idx < len(self._tab_keys)):
            return
        new_key = self._tab_keys[new_idx]
        if new_key == self._current_key:
            return
        if not self.confirm_leave_tab():
            old_idx = self._tab_keys.index(self._current_key)
            self._switching_tab = True
            try:
                self.tab_bar.setCurrentIndex(old_idx)
            finally:
                self._switching_tab = False
            return
        self._activate_buffer(new_key)

    def confirm_leave_tab(self, key=None):
        """지정한 탭(기본 현재 탭)에 미저장 변경이 있으면 저장·버리기·취소를 묻는다."""
        target_key = key or self._current_key
        buf = self._buffers.get(target_key)
        if buf is None or not buf.dirty:
            return True
        answer = self.workspace.ask_export(f'YAML 편집 ({buf.title})')
        if answer == 'discard':
            self.revert_key(target_key)
            return True
        if answer == 'export':
            return self.save_key(target_key)
        return False

    def confirm_leave_all(self):
        """미저장 YAML 탭이 남아 있으면 모두 처리할 때까지 묻는다."""
        order = ([self._current_key] if self._current_key else []) + [
            k for k in self.dirty_keys() if k != self._current_key
        ]
        for key in order:
            if not self.confirm_leave_tab(key):
                return False
        return True

    # --- 편집 이벤트 및 저장/되돌리기 ---

    def _on_editor_text_changed(self):
        if self._syncing_text or not self._current_key or self._current_key not in self._buffers:
            return
        buf = self._buffers[self._current_key]
        dirty = buf.document.toPlainText() != buf.saved_text
        if dirty != buf.dirty:
            buf.dirty = dirty
            self._refresh_tab_titles()
        self._refresh_outline()
        self._refresh_search()
        self._validate_timer.start()

    def save_current(self):
        if self._current_key:
            return self.save_key(self._current_key)
        return False

    def save_key(self, key):
        if key not in self._buffers:
            return False
        buf = self._buffers[key]
        text = buf.document.toPlainText()
        return self.workspace.apply_and_save_yaml(key, text)

    def mark_saved(self, key, canonical_text):
        if key not in self._buffers:
            return
        buf = self._buffers[key]
        self._syncing_text = True
        try:
            pos = self.editor.textCursor().position() if key == self._current_key else buf.cursor_pos
            if buf.document.toPlainText() != canonical_text:
                buf.document.setPlainText(canonical_text)
            buf.saved_text = canonical_text
            buf.dirty = False
            buf.cursor_pos = min(pos, max(0, buf.document.characterCount() - 1))
            if key == self._current_key:
                cursor = self.editor.textCursor()
                cursor.setPosition(buf.cursor_pos)
                self.editor.setTextCursor(cursor)
        finally:
            self._syncing_text = False
        self._refresh_tab_titles()
        self._refresh_outline()
        self.run_validation()

    def revert_current(self):
        if self._current_key:
            self.revert_key(self._current_key)

    def revert_key(self, key):
        if key not in self._buffers:
            return
        buf = self._buffers[key]
        self._syncing_text = True
        try:
            buf.document.setPlainText(buf.saved_text)
            buf.dirty = False
        finally:
            self._syncing_text = False
        self._refresh_tab_titles()
        self._refresh_outline()
        self._refresh_search()
        self.run_validation()

    def clean_current(self):
        if not self._current_key:
            return
        cleaned = clean_yaml_text(self.editor.toPlainText())
        if cleaned != self.editor.toPlainText():
            cursor = self.editor.textCursor()
            cursor.beginEditBlock()
            cursor.select(QTextCursor.Document)
            cursor.insertText(cleaned)
            cursor.endEditBlock()
            self.workspace.statusBar().showMessage('YAML 탭·공백을 정리했습니다.')

    def show_diff(self):
        if not self._current_key or self._current_key not in self._buffers:
            return
        buf = self._buffers[self._current_key]
        added, removed, diff_text = yaml_diff_summary(
            buf.saved_text, buf.document.toPlainText(), buf.title
        )
        DiffPreviewDialog(self, f'변경 비교 — {buf.title}', added, removed, diff_text,
                          font_size=self._font_size).exec()

    # --- 스니펫 / 자동완성 / 호버 ---

    def _rebuild_snippet_menu(self, kind):
        self.snippet_menu.clear()
        for label, body in SNIPPETS.get(kind, ()):
            action = self.snippet_menu.addAction(label)
            action.triggered.connect(lambda checked=False, text=body: self._insert_snippet(text))

    def _insert_snippet(self, body):
        cursor = self.editor.textCursor()
        if not cursor.atBlockStart():
            cursor.movePosition(QTextCursor.EndOfBlock)
            cursor.insertText('\n')
        cursor.insertText(body)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def _project_aliases(self):
        text = self._buffers['projects'].document.toPlainText() if 'projects' in self._buffers else ''
        try:
            projects, _ = read_projects(text)
            return [p.alias for p in projects if p.alias]
        except Exception:
            return [p.alias for p in self.workspace.project_editor.projects if p.alias]

    def _project_tooltip(self, token, section):
        if self._current_key == 'projects':
            return ''
        if section not in ALIAS_SECTIONS:
            return ''
        text = self._buffers['projects'].document.toPlainText() if 'projects' in self._buffers else ''
        try:
            projects, _ = read_projects(text)
        except Exception:
            projects = self.workspace.project_editor.projects
        match = next((p for p in projects if p.alias == token), None)
        if match is None:
            return ''
        year = self._buffers[self._current_key].year if self._current_key in self._buffers else None
        year_entry = next((y for y in match.years if y.year == year), None)
        parts = [f'사업: {match.name or "(이름 없음)"} ({match.alias})',
                 f'기간: {match.start or "?"}~{match.end or "?"}']
        if year_entry is not None:
            parts.append(f'{year}년 배정: {year_entry.manpower or "(미입력)"}'
                         + (f' · 제외월 [{year_entry.excluded}]' if year_entry.excluded else ''))
        return '\n'.join(parts)

    # --- 목차(아웃라인) ---

    def _toggle_outline(self, visible):
        self.outline_panel.setVisible(visible)

    def _refresh_outline(self):
        if not self._current_key or self._current_key not in self._buffers:
            return
        buf = self._buffers[self._current_key]
        needle = self.outline_filter.text().strip().lower()
        items = yaml_outline_items(buf.document.toPlainText().splitlines(), buf.kind)
        self.outline_tree.clear()
        last_parent = None
        for item in items:
            if item.level == 0:
                node = QTreeWidgetItem([item.label])
                node.setData(0, Qt.UserRole, item.line + 1)
                last_parent = node
                if not needle or needle in item.label.lower():
                    self.outline_tree.addTopLevelItem(node)
                    node.setExpanded(True)
            else:
                if needle and needle not in item.label.lower() and (last_parent is None or needle not in last_parent.text(0).lower()):
                    continue
                child = QTreeWidgetItem([item.label])
                child.setData(0, Qt.UserRole, item.line + 1)
                if last_parent is not None:
                    if last_parent.treeWidget() is None:
                        self.outline_tree.addTopLevelItem(last_parent)
                        last_parent.setExpanded(True)
                    last_parent.addChild(child)
                else:
                    self.outline_tree.addTopLevelItem(child)

    def _jump_outline_item(self, item, _column=0):
        line = item.data(0, Qt.UserRole)
        if line:
            self.editor.jump_to_line(int(line))

    # --- 찾기 / 바꾸기 ---

    def open_find_bar(self, show_replace=False):
        self.find_bar.show()
        self.replace_input.setVisible(show_replace)
        self.replace_btn.setVisible(show_replace)
        self.replace_all_btn.setVisible(show_replace)
        selected = self.editor.textCursor().selectedText().replace('\u2029', '\n')
        if selected and '\n' not in selected:
            self.find_input.setText(selected)
        self.find_input.setFocus()
        self.find_input.selectAll()
        self._refresh_search()

    def close_find_bar(self):
        self.find_bar.hide()
        self.editor.set_search_highlights([], -1)
        self.editor.setFocus()

    def _find_matches(self):
        pattern = self.find_input.text()
        if not pattern or not self.find_bar.isVisible():
            return []
        text = self.editor.toPlainText()
        flags = 0 if self.case_check.isChecked() else re.IGNORECASE
        try:
            regex = re.compile(pattern if self.regex_check.isChecked() else re.escape(pattern), flags)
        except re.error:
            return []
        return [(m.start(), m.end() - m.start()) for m in regex.finditer(text) if m.end() > m.start()]

    def _refresh_search(self):
        matches = self._find_matches()
        idx = 0 if matches else -1
        pos = self.editor.textCursor().position()
        for i, (start, _) in enumerate(matches):
            if start >= pos:
                idx = i
                break
        self.match_count_label.setText(f'{idx + 1}/{len(matches)}건' if matches else '0건')
        self.editor.set_search_highlights(matches, idx)

    def find_next(self, forward=True):
        matches = self._find_matches()
        if not matches:
            self.match_count_label.setText('0건')
            self.editor.set_search_highlights([], -1)
            return False
        cur_pos = self.editor.textCursor().position()
        if forward:
            idx = next((i for i, (s, _) in enumerate(matches) if s >= cur_pos), 0)
        else:
            anchor = self.editor.textCursor().selectionStart()
            idx = next((i for i in range(len(matches) - 1, -1, -1) if matches[i][0] < anchor), len(matches) - 1)
        start, length = matches[idx]
        cursor = self.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()
        self.match_count_label.setText(f'{idx + 1}/{len(matches)}건')
        self.editor.set_search_highlights(matches, idx)
        return True

    def replace_one(self):
        matches = self._find_matches()
        if not matches:
            return
        cursor = self.editor.textCursor()
        sel_pair = (cursor.selectionStart(), cursor.selectionEnd() - cursor.selectionStart())
        if sel_pair in matches:
            cursor.insertText(self.replace_input.text())
        self.find_next(True)

    def replace_all(self):
        pattern = self.find_input.text()
        if not pattern:
            return
        flags = 0 if self.case_check.isChecked() else re.IGNORECASE
        try:
            regex = re.compile(pattern if self.regex_check.isChecked() else re.escape(pattern), flags)
        except re.error:
            return
        text = self.editor.toPlainText()
        replacement = self.replace_input.text()
        new_text, count = (regex.subn(replacement, text) if self.regex_check.isChecked()
                           else regex.subn(lambda _m: replacement, text))
        if count:
            cursor = self.editor.textCursor()
            cursor.beginEditBlock()
            cursor.select(QTextCursor.Document)
            cursor.insertText(new_text)
            cursor.endEditBlock()
        self._refresh_search()

    # --- 실시간 검증 ---

    def _target_people_key(self):
        if self._current_key and self._current_key != 'projects':
            return self._current_key
        prefix = 'plan:' if self.workspace.planning else 'people:'
        preferred = prefix + str(self.workspace.current_year)
        if preferred in self._buffers:
            return preferred
        return next((k for k in self._tab_keys if k != 'projects'), None)

    def run_validation(self):
        if not self._current_key or self._current_key not in self._buffers:
            return
        buf = self._buffers[self._current_key]
        current_text = buf.document.toPlainText()
        diag_items = []  # (severity, code, text, line_or_None)
        line_map = {}

        def add_line_diag(line, sev, msg):
            if line is not None and line >= 1 and line not in line_map:
                line_map[line] = (sev, msg)

        # 1. 현재 편집 중인 문서 구조 파싱 (원문 라인 오류 포착)
        try:
            if buf.kind == 'plan':
                read_plan(current_text)
                dropped = []
            elif buf.kind == 'projects':
                _, dropped = read_projects(current_text)
            else:
                _, dropped = read_people(current_text)
            for msg in dropped:
                line = extract_error_line(msg)
                diag_items.append(('WARNING', 'META', msg, line))
                add_line_diag(line, 'WARNING', msg)
        except (DocumentError, yaml.YAMLError, ValueError) as exc:
            msg = str(exc)
            line = extract_error_line(msg)
            if line is None and isinstance(exc, yaml.YAMLError) and getattr(exc, 'problem_mark', None):
                line = exc.problem_mark.line + 1
            diag_items.append(('ERROR', 'YAML', msg, line))
            add_line_diag(line, 'ERROR', msg)

        # 2. 전체 계산/스키마 규칙(load_inputs / load_plan) 실행
        projects_text = (current_text if self._current_key == 'projects'
                         else self._buffers['projects'].document.toPlainText()
                         if 'projects' in self._buffers else dump_projects(self.workspace.project_editor.projects))
        other_key = self._target_people_key()
        other_text = (current_text if other_key == self._current_key
                      else self._buffers[other_key].document.toPlainText()
                      if other_key and other_key in self._buffers else '')

        lines = current_text.splitlines()
        if other_text:
            if self.workspace.planning:
                plan_inputs, issues = load_plan(
                    'projects.yaml', 'plan.yaml',
                    texts={'projects': projects_text, 'plan': other_text},
                )
                if plan_inputs is not None and not any(i.severity == 'ERROR' for i in issues):
                    issues = list(issues) + list(review_plan(plan_inputs).issues)
            else:
                inputs, issues = load_inputs(
                    'projects.yaml', 'people.yaml',
                    texts={'projects': projects_text, 'people': other_text},
                )
                if inputs is not None and not any(i.severity == 'ERROR' for i in issues):
                    issues = calculate(inputs).issues
            for issue in issues:
                line = extract_error_line(issue.location) or extract_error_line(issue.hint)
                if line is None:
                    line = self._guess_issue_line(lines, buf.kind, issue.location)
                text = f'[{issue.severity} {issue.code}] {issue.location}: {issue.current} ({issue.hint})'
                diag_items.append((issue.severity, issue.code, text, line))
                add_line_diag(line, issue.severity, text)

        # 3. 하드 탭 경고
        for idx, raw in enumerate(lines, 1):
            if '\t' in raw:
                msg = f'{idx}번째 줄: YAML에서는 탭 문자(\\t) 대신 공백을 사용해야 합니다.'
                diag_items.append(('ERROR', 'TAB', msg, idx))
                add_line_diag(idx, 'ERROR', msg)

        self.diagnostics_list.clear()
        errors = sum(1 for sev, _, _, _ in diag_items if sev == 'ERROR')
        warnings = sum(1 for sev, _, _, _ in diag_items if sev == 'WARNING')
        if not diag_items:
            self.status_label.setText(f'검증 통과 · ERROR 0건 / WARNING 0건'
                                      + (' · 미저장 변경 있음 (Ctrl+S로 저장)' if buf.dirty else ''))
        else:
            self.status_label.setText(f'ERROR {errors}건 / WARNING {warnings}건'
                                      + (' · 미저장 변경 있음 (Ctrl+S로 저장)' if buf.dirty else ''))
            for sev, code, text, line in diag_items:
                prefix = f'[{line}줄] ' if line else ''
                item = QListWidgetItem(prefix + text)
                item.setData(Qt.UserRole, line)
                item.setForeground(QColor(DARK_COLORS['error_wave'] if sev == 'ERROR' else DARK_COLORS['warning_wave'])
                                   if theme.is_dark() else
                                   (theme.error_text() if sev == 'ERROR' else theme.warning_text()))
                self.diagnostics_list.addItem(item)
        self.editor.set_diagnostics(line_map)

    @staticmethod
    def _guess_issue_line(lines, kind, where):
        # 예: "people.yaml: 홍길동: 사업1", "projects.yaml: 사업1"
        parts = [p.strip() for p in (where or '').split(':') if p.strip()]
        for part in reversed(parts):
            if part.endswith('.yaml') or '/' in part:
                continue
            line = find_item_line(lines, kind, part)
            if line > 1:
                return line
        return None

    def _jump_diagnostic(self, item):
        line = item.data(Qt.UserRole)
        if line:
            self.editor.jump_to_line(int(line))

    def jump_to_item(self, kind_key, target_name):
        if not self.select_key(kind_key):
            return
        lines = self.editor.toPlainText().splitlines()
        line = find_item_line(lines, self._buffers[kind_key].kind, target_name)
        self.editor.jump_to_line(line)
