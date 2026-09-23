"""L2 판정 공급자 — 촬영물을 보고 판단하는 계층의 경계.

별표5 40항목 중 23항목은 자로 잴 수 없다. '이해하기 쉬운 말로 안내하는가',
'초점이 눈에 띄게 표시되는가' 같은 것들이다. 이 항목들은 사람이나
멀티모달 모델이 촬영물을 **보고** 판단해야 한다.

이 파일이 정하는 것은 판단 자체가 아니라 **판단의 경계**다.

    ① 공급자는 어떤 것도 확정하지 못한다.
       Judgment 는 원시 의견일 뿐이고, 반드시 verdict.apply_ai_scope 를 통과한다.

    ② 모델의 '적합'은 적합이 아니다.
       룰이 both 여도 L2 판정은 fail_only 로 눌린다(judge.L2_SCOPE_CEILING).
       모델이 화면을 보고 "괜찮아 보인다"고 말한 것을 법정 적합으로 인쇄하면,
       그 리포트를 믿은 점주가 처분을 받는다. 위반 후보를 골라 주는 것까지가
       모델의 몫이고, 적합 선언은 사람의 몫이다.

    ③ 무거운 의존성을 exe 에 넣지 않는다.
       공급자는 실행 시점에 있는 것만 쓴다. 없으면 '미구성'이라고 정직하게 말하고
       해당 항목을 검토자에게 넘긴다. 조용히 빈 판정을 만들지 않는다.

    ④ 외부 전송은 기본 꺼짐이다.
       현장 사진에는 개인정보가 남아 있을 수 있다. 앱이 기기에서 마스킹하지만
       그것으로 충분하다고 가정하지 않는다. 외부 API 공급자는 사용자가
       명시적으로 켤 때만 동작한다(ingest --l2).
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..verdict import Verdict

# 공급자가 낼 수 있는 원시 판정. 여기 없는 값은 파싱 단계에서 버린다.
ALLOWED = {
    "적합": Verdict.PASS,
    "위반": Verdict.FAIL,
    "해당없음": Verdict.NOT_APPLICABLE,
    "판단불가": None,
}

# 모델이 스스로 매긴 확신도는 계측값이 아니다. 상한을 둔다.
CONFIDENCE_CEILING = 0.92


@dataclass
class JudgeRequest:
    """항목 하나를 판단해 달라는 요청."""

    rule_id: str
    criterion: str                       # 별표5 원문 (공공저작물)
    area: str                            # 영역 이름
    media: list[Path] = field(default_factory=list)
    ks_refs: list[str] = field(default_factory=list)   # 절 번호만. 본문은 절대 싣지 않는다
    params: dict = field(default_factory=dict)
    note: str = ""

    def media_note(self) -> str:
        return ", ".join(p.name for p in self.media) or "없음"


@dataclass
class Judgment:
    """공급자가 낸 원시 의견. 이 자체로는 아무것도 확정하지 못한다."""

    verdict: Verdict
    confidence: float
    rationale: str
    cited: list[str] = field(default_factory=list)
    provider: str = ""

    def __post_init__(self) -> None:
        value = float(self.confidence)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be a finite number between 0 and 1")
        self.confidence = min(CONFIDENCE_CEILING, value)


class Provider:
    """L2 공급자 인터페이스."""

    name = "base"
    sends_media_externally = False

    def available(self) -> tuple[bool, str]:
        """(쓸 수 있는가, 못 쓰면 그 이유). 이유는 리포트에 그대로 인쇄된다."""
        return False, "구현되지 않은 공급자입니다"

    def judge(self, req: JudgeRequest) -> Judgment | None:
        """판단하거나, 못 하겠으면 None. 추측으로 채우지 않는다."""
        raise NotImplementedError


class NullProvider(Provider):
    """공급자가 없을 때의 기본값 — 아무것도 판단하지 않는다.

    '설정되지 않음'을 '적합'이나 '위반 없음'으로 바꾸지 않는 것이 이 클래스의 일이다.
    """

    name = "없음"

    def available(self) -> tuple[bool, str]:
        return False, (
            "L2 공급자가 설정되지 않았습니다. "
            "환경변수 KFA_L2_PROVIDER 와 자격증명을 지정하면 이 항목들이 자동 선별됩니다."
        )

    def judge(self, req: JudgeRequest) -> Judgment | None:
        return None


def resolve(name: str | None = None) -> Provider:
    """쓸 공급자를 고른다.

    KFA_L2_PROVIDER 환경변수 또는 인자로 지정한다. 지정이 없으면 NullProvider —
    **자동으로 외부 API 를 찾아 붙지 않는다.** 현장 사진을 밖으로 보내는 일은
    사용자가 명시적으로 켜야 한다.
    """
    key = (name or os.environ.get("KFA_L2_PROVIDER") or "").strip().lower()
    if not key:
        return NullProvider()
    if key in ("anthropic", "claude"):
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if key in ("echo", "stub", "test"):
        from .stub import EchoProvider
        return EchoProvider()
    raise ValueError(f"모르는 L2 공급자입니다: {key!r} (anthropic | echo)")
