"""저장된 업무 스냅샷의 세션 내 이력.

호출자는 적용 함수에서 모든 대상 파일과 화면 상태를 하나의 작업으로 교체한다.
적용이 실패하면 이력 커서는 이동하지 않는다.
"""
from copy import deepcopy
from dataclasses import dataclass


@dataclass
class Entry:
    label: str
    before: object
    after: object


class History:
    def __init__(self):
        self.entries = []
        self.cursor = 0

    def record(self, label, before, after, *, force=False):
        if not force and before == after:
            return False
        entry = Entry(label, deepcopy(before), deepcopy(after))
        del self.entries[self.cursor:]
        self.entries.append(entry)
        self.cursor += 1
        return True

    def undo_labels(self):
        return [entry.label for entry in reversed(self.entries[:self.cursor])]

    def redo_labels(self):
        return [entry.label for entry in self.entries[self.cursor:]]

    def move(self, delta, apply):
        target = self.cursor + delta
        if not delta or not 0 <= target <= len(self.entries):
            return False
        state = self.entries[target].before if delta < 0 else self.entries[target - 1].after
        if not apply(deepcopy(state)):
            return False
        self.cursor = target
        return True

    def clear(self):
        self.entries.clear()
        self.cursor = 0
