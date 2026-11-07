"""사업·사람 파일 묶음의 준비 후 교체와 실패 시 원본 복구."""
import os
import shutil
import tempfile
from pathlib import Path


def write_bundle(jobs, backed_up):
    """모든 내용을 먼저 준비한다. 교체 실패 시 이미 바꾼 파일을 역순 복구한다.

    프로세스 강제 종료·정전까지 보장하는 디스크 트랜잭션은 아니다.
    복구 자체가 실패하면 그 경로를 오류로 알리고 복구용 임시 파일을 남긴다.
    """
    staged, originals, replaced = {}, {}, []
    preserve = set()
    try:
        for raw_path, text in jobs:
            path = Path(raw_path).resolve()
            if path in staged:
                raise OSError(f'묶음에 중복된 저장 경로: {path}')
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.prouman-', delete=False) as stream:
                staged[path] = Path(stream.name)
                stream.write(text.encode('utf-8'))
            originals[path] = None
            if path.exists():
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.prouman-restore-', delete=False) as stream:
                    originals[path] = Path(stream.name)
                shutil.copy2(path, originals[path])
        for path in staged:
            if originals[path] is not None and str(path) not in backed_up:
                shutil.copy2(path, path.with_name(path.name + '.bak'))
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
        backed_up.update(str(path) for path in staged if originals[path] is not None)
    except OSError as exc:
        failures = []
        for path in reversed(replaced):
            try:
                if originals[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(originals[path], path)
            except OSError:
                failures.append(str(path))
                if originals[path] is not None:
                    preserve.add(originals[path])
        if failures:
            raise OSError(f'{exc}; 원본 복구 실패: {", ".join(failures)}; 복구 파일: '
                          + ', '.join(map(str, preserve))) from exc
        raise
    finally:
        for temporary in list(staged.values()) + [p for p in originals.values() if p is not None]:
            if temporary not in preserve:
                temporary.unlink(missing_ok=True)
