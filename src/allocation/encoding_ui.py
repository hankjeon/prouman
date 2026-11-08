"""파일 선택 창에 인코딩 옵션을 제공한다. YAML 편집기는 아니다."""
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QPlainTextEdit

from .text_encoding import ENCODINGS, decode_yaml


class EncodingFileDialog(QFileDialog):
    def __init__(self, parent, title, directory, saving=False, single=False):
        super().__init__(parent, title, directory)
        self.setOption(QFileDialog.DontUseNativeDialog)
        self.setFileMode(QFileDialog.Directory if saving else
                         QFileDialog.ExistingFile if single else QFileDialog.ExistingFiles)
        if saving:
            self.setOption(QFileDialog.ShowDirsOnly)
        else:
            self.setNameFilter('YAML 파일 (*.yaml *.yml);;모든 파일 (*)')
        self.encoding_combo = QComboBox(self)
        self.encoding_combo.addItem('기존 파일/원본 인코딩 유지' if saving else '자동 판별',
                                    'preserve' if saving else 'auto')
        for codec, label in ENCODINGS.items():
            self.encoding_combo.addItem(label, codec)
        grid = self.layout()
        row = grid.rowCount()
        grid.addWidget(QLabel('저장 인코딩' if saving else '읽기 인코딩', self), row, 0)
        grid.addWidget(self.encoding_combo, row, 1, 1, grid.columnCount() - 1)
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(160)
        self.preview.setVisible(not saving)
        if not saving:
            grid.addWidget(self.preview, row + 1, 0, 1, grid.columnCount())
        self.currentChanged.connect(self.update_preview)
        self.encoding_combo.currentIndexChanged.connect(lambda _: self.update_preview(self._preview_path))
        self._preview_path = ''
        self.resize(850, 650 if not saving else 480)

    def update_preview(self, path):
        self._preview_path = path
        if not path or not Path(path).is_file():
            self.preview.clear()
            return
        try:
            # 미리보기만 크기를 제한한다. 실제 가져오기는 전체 파일을 엄격하게 읽는다.
            with Path(path).open('rb') as stream:
                data = stream.read(65536)
                truncated = bool(stream.read(1))
            if truncated:
                # 끝 문자가 잘렸을 수 있어 마지막 줄은 미리보기에서 제외한다.
                data = data.rsplit(b'\n', 1)[0]
            decoded = decode_yaml(data, self.encoding_combo.currentData())
            info = ENCODINGS[decoded.encoding] + (f' · {decoded.note}' if decoded.note else '')
            self.preview.setPlainText(info + '\n' + decoded.text[:10000]
                                      + ('\n… 미리보기 생략' if truncated else ''))
        except (OSError, UnicodeError, ValueError) as exc:
            self.preview.setPlainText(f'읽기 실패: {exc}\n인코딩을 직접 지정해 보세요.')


def choose_files(parent, directory, single=False):
    dialog = EncodingFileDialog(parent, '불러오기 — 인코딩 확인', directory, single=single)
    try:
        if dialog.exec() != QFileDialog.Accepted:
            return [], 'auto'
        return dialog.selectedFiles(), dialog.encoding_combo.currentData()
    finally:
        dialog.deleteLater()


def choose_folder(parent, directory):
    dialog = EncodingFileDialog(parent, '저장/내보내기 폴더 — 인코딩', directory, saving=True)
    try:
        if dialog.exec() != QFileDialog.Accepted:
            return '', 'preserve'
        return dialog.selectedFiles()[0], dialog.encoding_combo.currentData()
    finally:
        dialog.deleteLater()
