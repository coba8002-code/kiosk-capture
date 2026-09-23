"""검출기 — 계측 이전 단계. '무엇을 잴 것인가'를 찾아낸다.

계측(calc)과 분리한 이유: 검출기는 교체될 수 있지만 계측은 고정돼야 한다.
OCR 을 붙이면 text_regions 는 대체되지만 contrast 계산은 그대로다.
"""
from . import text_regions  # noqa: F401
