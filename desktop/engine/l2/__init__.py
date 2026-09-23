"""L2 판정 계층 — 촬영물을 보고 판단해야 하는 23항목을 다룬다.

경계는 provider.py 머리말에, 안전장치는 judge.py 머리말에 적혀 있다.
"""

from .judge import L2Run, run, merge, L2_SCOPE_CEILING
from .provider import Judgment, JudgeRequest, Provider, resolve

__all__ = [
    "L2Run", "run", "merge", "L2_SCOPE_CEILING",
    "Judgment", "JudgeRequest", "Provider", "resolve",
]
