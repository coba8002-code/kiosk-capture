"""키오스크 현장 접근성 진단 엔진.

별표5 40항목을 촬영물·점주 확인·전문 실측 세 트랙으로 나눠 판정한다.
진입점은 rules.load() 와 verdict.apply_ai_scope() 두 개다.
"""
from .rules import load, load_protocol, RuleSet, RuleError  # noqa: F401
from .verdict import (  # noqa: F401
    Assessment, Evidence, Grade, Undetermined, Verdict,
    apply_ai_scope, confirm, record_external,
)

__version__ = "0.1.0"
