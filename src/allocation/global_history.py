"""여러 업무 세션의 시간순 이력과 단일 묶음 이동."""
from collections import Counter
from dataclasses import dataclass

from . import storage


@dataclass
class GlobalEntry:
    workspace: object
    session: object
    label: str
    context: object


class GlobalHistory:
    def __init__(self):
        self.entries = []
        self.cursor = 0
        self.revision = 0

    def clear(self):
        self.entries.clear()
        self.cursor = 0
        self.revision += 1

    def record(self, workspace, session, label, context, workspaces):
        if self.cursor < len(self.entries):
            for item in workspaces:
                if item.autosave_session is not None and item.autosave_session is not session:
                    item.autosave_session.discard_redo()
            del self.entries[self.cursor:]
        self.entries.append(GlobalEntry(workspace, session, label, context))
        self.cursor += 1
        self.revision += 1

    def labels(self, redo=False):
        entries = self.entries[self.cursor:] if redo else reversed(self.entries[:self.cursor])
        return [entry.label for entry in entries]

    def move(self, delta):
        target = self.cursor + delta
        if not delta or not 0 <= target <= len(self.entries):
            return None
        group = self.entries[target:self.cursor] if delta < 0 else self.entries[self.cursor:target]
        counts = Counter(entry.workspace for entry in group)
        sessions = {}
        for entry in group:
            if entry.workspace.autosave_session is not entry.session or not entry.workspace.history_ready():
                raise ValueError('열린 문서의 원본이 바뀌어 이력을 적용할 수 없습니다.')
            sessions[entry.workspace] = entry.session
        prepared = {ws: session.prepare_move((-1 if delta < 0 else 1) * counts[ws])
                    for ws, session in sessions.items()}
        if any(item is None for item in prepared.values()):
            raise ValueError('작업공간 이력과 전역 이력이 일치하지 않습니다.')
        for item in prepared.values():
            item.session.check_fingerprints()
        jobs, expected, outputs, backups = {}, {}, {}, set()
        for ws, item in prepared.items():
            backups.update(item.session.backed_up)
            for path, data in item.rendered.items():
                if path in outputs and (outputs[path] != data or expected[path] != item.fingerprints[path]):
                    raise ValueError(f'같은 원본에 서로 다른 이력 상태가 있습니다: {path}')
                outputs[path] = data
                expected[path] = item.fingerprints[path]
            for path, data in item.jobs:
                jobs[path] = data
        if jobs:
            storage.write_bundle(list(jobs.items()), backups, expected=expected, guards=expected)
        for item in prepared.values():
            # 같은 파일을 두 세션이 공유하면 함께 새 지문을 채택한다.
            item.jobs = [(path, jobs[path]) for path in item.rendered if path in jobs]
            item.session.accept_move(item, backups)
        self.cursor = target
        self.revision += 1
        return group[0] if delta < 0 else group[-1]
