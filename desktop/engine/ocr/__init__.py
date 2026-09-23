"""OCR 계층 — 별표5 3.g(문자 높이) 판정에 필요한 '무슨 글자인가'를 담당한다.

경계와 이유는 provider.py 머리말에 적혀 있다.
"""

from .provider import (
    MAX_LINES, MIN_TEXT_CONFIDENCE, NullProvider, OcrResult, Provider,
    TextLine, resolve,
)

__all__ = [
    "MAX_LINES", "MIN_TEXT_CONFIDENCE", "NullProvider", "OcrResult",
    "Provider", "TextLine", "resolve",
]
