"""표시 순서만 이동하고, 복사는 같은 화면 순서를 따르게 한다."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def selection_text(view, text_at):
    selected = {(index.row(), index.column()) for index in view.selectedIndexes()}
    if not selected:
        return ''
    vertical, horizontal = view.verticalHeader(), view.horizontalHeader()
    visual = {(vertical.visualIndex(row), horizontal.visualIndex(column)) for row, column in selected}
    rows = range(min(row for row, _ in visual), max(row for row, _ in visual) + 1)
    columns = range(min(column for _, column in visual), max(column for _, column in visual) + 1)
    return '\n'.join('\t'.join(
        text_at(vertical.logicalIndex(row), horizontal.logicalIndex(column))
        if (row, column) in visual else '' for column in columns) for row in rows)


class TableOrderMixin:
    """이름 셀 드래그로 행 이동, 사업 머리글 드래그로 열 이동을 지원한다.

    모델의 논리 인덱스는 유지한다. 고정 영역은 이동하거나 넘어갈 수 없다.
    화면별 순서는 현재 실행 중에만 기억하며 입력 문서를 변경하지 않는다.
    """

    def init_table_order(self):
        self._order_cache = {}
        self._order_context = None
        self._ordering_update = False
        self._row_keys = {}
        self._column_keys = {}
        self._drag_origin = None
        self._drag_row = None
        self._row_dragging = False
        self.verticalHeader().sectionMoved.connect(lambda logical, old, new: self._section_moved(True, logical, old, new))
        self.horizontalHeader().sectionMoved.connect(lambda logical, old, new: self._section_moved(False, logical, old, new))

    def configure_order(self, context=None, rows=None, columns=None):
        """rows/columns: 이동 가능한 논리 인덱스 → 안정적인 식별 이름."""
        self._ordering_update = True
        self._order_context = context
        self._row_keys = dict(rows or {})
        self._column_keys = dict(columns or {})
        self._drag_row = None
        self._row_dragging = False
        self.viewport().unsetCursor()
        self.setMouseTracking(bool(self._row_keys))
        self.horizontalHeader().setSectionsMovable(bool(self._column_keys))
        self.horizontalHeader().setFirstSectionMovable(False)
        saved_rows, saved_columns = self._order_cache.get(context, ([], []))
        try:
            for header, keys, saved in ((self.verticalHeader(), self._row_keys, saved_rows),
                                        (self.horizontalHeader(), self._column_keys, saved_columns)):
                # 모델을 바꿨을 때 남아 있는 이전 섹션 배치를 먼저 초기화한다.
                for logical in range(header.count()):
                    header.moveSection(header.visualIndex(logical), logical)
                previous = [key for key in saved if key in keys.values()]
                ordered = previous + [key for key in keys.values() if key not in previous]
                lookup = {key: logical for logical, key in keys.items()}
                for visual, key in zip(sorted(keys), ordered):
                    header.moveSection(header.visualIndex(lookup[key]), visual)
        finally:
            self._ordering_update = False
        self._remember_order()

    def _remember_order(self):
        if self._order_context is None:
            return
        orders = []
        for header, keys in ((self.verticalHeader(), self._row_keys),
                             (self.horizontalHeader(), self._column_keys)):
            orders.append([keys[logical] for logical in sorted(keys, key=header.visualIndex)])
        self._order_cache[self._order_context] = tuple(orders)

    def _section_moved(self, vertical, logical, old, new):
        if self._ordering_update:
            return
        header = self.verticalHeader() if vertical else self.horizontalHeader()
        keys = self._row_keys if vertical else self._column_keys
        if logical not in keys or new not in keys:
            self._ordering_update = True
            try:
                header.moveSection(new, old)
            finally:
                self._ordering_update = False
        else:
            self._remember_order()

    def mousePressEvent(self, event):
        self._drag_row = None
        self._row_dragging = False
        super().mousePressEvent(event)
        index = self.indexAt(event.position().toPoint())
        if (event.button() == Qt.LeftButton and event.modifiers() == Qt.NoModifier
                and index.isValid() and index.column() == 0 and index.row() in self._row_keys):
            self._drag_row = index.row()
            self._drag_origin = event.position().toPoint()

    def mouseMoveEvent(self, event):
        position = event.position().toPoint()
        if self._drag_row is not None and event.buttons() & Qt.LeftButton:
            if not self._row_dragging and (position - self._drag_origin).manhattanLength() >= QApplication.startDragDistance():
                self._row_dragging = True
                self.selectRow(self._drag_row)
                self.viewport().setCursor(Qt.ClosedHandCursor)
            if self._row_dragging:
                # 포인터가 위·아래 끝에 가면 화면 밖 행도 이동 대상으로 접근할 수 있다.
                bar = self.verticalScrollBar()
                if position.y() < 16:
                    bar.setValue(bar.value() - bar.singleStep())
                elif position.y() > self.viewport().height() - 16:
                    bar.setValue(bar.value() + bar.singleStep())
                header = self.verticalHeader()
                target = header.visualIndex(self.rowAt(position.y()))
                if target >= 0:
                    target = max(min(self._row_keys), min(target, max(self._row_keys)))
                    source = header.visualIndex(self._drag_row)
                    if source != target:
                        header.moveSection(source, target)
                event.accept()
                return
        if not event.buttons():
            index = self.indexAt(position)
            if index.isValid() and index.column() == 0 and index.row() in self._row_keys:
                self.viewport().setCursor(Qt.OpenHandCursor)
            else:
                self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        dragging = self._row_dragging
        self._drag_row = None
        self._drag_origin = None
        self._row_dragging = False
        self.viewport().unsetCursor()
        if dragging:
            event.accept()
        else:
            super().mouseReleaseEvent(event)
