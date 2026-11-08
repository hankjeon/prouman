"""YAML 바이트와 내부 Unicode 문자열 사이의 명시적·손실 없는 변환."""
from dataclasses import dataclass


ENCODINGS = {'utf-8': 'UTF-8', 'utf-8-sig': 'UTF-8 BOM',
             'cp949': 'CP949', 'euc-kr': 'EUC-KR'}


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    note: str = ''


def decode_yaml(data, encoding='auto'):
    """자동 판별의 모호함은 안내하고, 명시한 인코딩 실패에는 대체 해석하지 않는다."""
    if encoding != 'auto':
        if encoding not in ENCODINGS:
            raise ValueError(f'지원하지 않는 인코딩: {encoding}')
        # BOM은 YAML 내용이 아니므로 UTF-8 지정에서도 읽을 때는 제거한다.
        codec = 'utf-8-sig' if encoding == 'utf-8' else encoding
        return DecodedText(data.decode(codec), encoding, '직접 지정')
    if data.startswith(b'\xef\xbb\xbf'):
        return DecodedText(data.decode('utf-8-sig'), 'utf-8-sig')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        text = data.decode('cp949')
        try:
            shared = data.decode('euc-kr') == text
        except UnicodeDecodeError:
            shared = False
        return DecodedText(text, 'cp949', 'CP949/EUC-KR 공통 바이트: CP949로 처리' if shared else '')
    return DecodedText(text, 'utf-8', 'ASCII 공통 바이트: UTF-8로 처리' if data.isascii() else '')


def encode_yaml(text, encoding):
    if encoding not in ENCODINGS:
        raise ValueError(f'지원하지 않는 인코딩: {encoding}')
    return text.encode(encoding, errors='strict')
