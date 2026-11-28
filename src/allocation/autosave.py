"""Qt와 독립적인 업무 스냅샷 및 다중 파일 저장 트랜잭션.

경로 집합은 생성 시 직렬화 결과로 고정한다. 새 파일이나 다시 불러온
외부 파일은 호출자가 경로와 내용을 확인한 뒤 새 세션을 만들어 바인딩한다.
오래된 세션을 자동으로 재기준화하면 외부 변경을 덮어쓸 수 있다.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path

from .history import History
from . import storage


@dataclass
class PreparedMove:
    session: object
    cursor: int
    history: object
    state: object
    rendered: dict
    jobs: list
    fingerprints: dict


class TransactionalSession:
    """직렬화 가능한 스냅샷의 성공한 변경만 파일과 세션 이력에 반영한다.

    serialize는 스냅샷을 받아 경로와 bytes의 mapping을 돌려준다. 경로의
    추가나 제거는 거부한다. 논리 검증과 인코딩은 호출자의 책임이다.
    예외는 호출자에게 전달되며 실패한 작업은 이력에 기록되지 않는다.
    """

    def __init__(self, snapshot, serialize, *, backed_up=None):
        self._serialize = serialize
        self._state = deepcopy(snapshot)
        self._serialized = self._render(self._state)
        self._fingerprints = {path: self._fingerprint(path) for path in self._serialized}
        self._history = History()
        self._backed_up = set(backed_up or ())

    @staticmethod
    def _fingerprint(path):
        try:
            return hashlib.sha256(path.read_bytes()).digest()
        except FileNotFoundError:
            return None

    def _render(self, snapshot):
        result = {}
        for raw_path, data in self._serialize(deepcopy(snapshot)).items():
            path = Path(raw_path).resolve()
            if path in result:
                raise ValueError(f'중복 저장 경로: {path}')
            if not isinstance(data, bytes):
                raise TypeError(f'직렬화 결과는 bytes여야 합니다: {path}')
            result[path] = data
        return result

    @property
    def state(self):
        return deepcopy(self._state)

    @property
    def history(self):
        """외부 수정으로 내부 이력이 바뀌지 않는 읽기용 사본."""
        return deepcopy(self._history)

    @property
    def fingerprints(self):
        return self._fingerprints.copy()

    @property
    def backed_up(self):
        return self._backed_up.copy()

    def _prepare(self, snapshot):
        state = deepcopy(snapshot)
        rendered = self._render(state)
        if rendered.keys() != self._serialized.keys():
            raise ValueError('등록되지 않았거나 누락된 저장 경로: '
                             + ', '.join(map(str, rendered.keys() ^ self._serialized.keys())))
        jobs = [(path, data) for path, data in rendered.items()
                if data != self._serialized[path]]
        return state, rendered, jobs

    def _write(self, jobs):
        if not jobs:
            return
        # 성공 후에만 백업 상태를 채택한다. 복구 실패를 포함한 예외는 전파한다.
        backups = self._backed_up.copy()
        # 변경하지 않는 사업·사람 원본도 현재 결과와 저장 작업의 의존성이다.
        storage.write_bundle(jobs, backups, expected=self._fingerprints, guards=self._fingerprints)
        self._backed_up = backups

    def _accept(self, state, rendered, jobs):
        self._state = state
        self._serialized = rendered
        for path, data in jobs:
            self._fingerprints[path] = hashlib.sha256(data).digest()

    def commit(self, label, snapshot):
        state, rendered, jobs = self._prepare(snapshot)
        if not jobs:
            return False
        # deepcopy나 이력 기록이 실패하면 디스크에 쓰기 전에 종료한다.
        history = deepcopy(self._history)
        # list 하위 클래스의 부가 metadata처럼 __eq__가 놓치는 자료도
        # 직렬화된 바이트가 다르면 업무 변경이다.
        history.record(label, self._state, state, force=True)
        self._write(jobs)
        self._accept(state, rendered, jobs)
        self._history = history
        return True

    def create_missing(self):
        """명시적으로 선택된 새 바인딩의 초기 파일을 생성한다.

        생성 당시 부재로 확인된 경로만 쓰며, 그 사이에 파일이 생겼으면
        지문 검사로 거부한다. 사용자가 경로를 승인한 뒤에만 호출해야 한다.
        """
        jobs = [(path, self._serialized[path]) for path, fingerprint in self._fingerprints.items()
                if fingerprint is None]
        if not jobs:
            return False
        self._write(jobs)
        for path, data in jobs:
            self._fingerprints[path] = hashlib.sha256(data).digest()
        return True

    def move(self, delta):
        prepared = self.prepare_move(delta)
        if prepared is None:
            return False
        self._write(prepared.jobs)
        self.accept_move(prepared, self._backed_up)
        return True

    def prepare_move(self, delta):
        """이력 이동의 최종 문서·바이트를 파일 쓰기 없이 준비한다."""
        history = deepcopy(self._history)
        prepared = []

        def apply(snapshot):
            prepared.append(self._prepare(snapshot))
            return True

        if not history.move(delta, apply):
            return None
        state, rendered, jobs = prepared[0]
        return PreparedMove(self, self._history.cursor, history, state, rendered, jobs,
                            self._fingerprints.copy())

    def accept_move(self, prepared, backed_up):
        """호출자가 단일 묶음 저장에 성공한 이후 준비된 상태를 채택한다."""
        if prepared.session is not self or prepared.cursor != self._history.cursor:
            raise ValueError('오래된 이력 이동입니다.')
        self._accept(prepared.state, prepared.rendered, prepared.jobs)
        self._history = prepared.history
        self._backed_up = set(backed_up)

    def discard_redo(self):
        del self._history.entries[self._history.cursor:]

    def check_fingerprints(self):
        """준비된 이력이 의존하는 모든 원본이 현재 상태인지 확인한다."""
        for path, expected in self._fingerprints.items():
            if self._fingerprint(path) != expected:
                raise OSError(f'외부 변경 충돌: {path}')
