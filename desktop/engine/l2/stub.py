"""결정론적 시험용 공급자 — 배선을 검사하기 위한 것.

API 키 없이도 L2 경로 전체(요청 조립 → 판단 → 사정거리 강제 → 리포트)를
끝까지 돌려 볼 수 있어야 한다. 그러지 않으면 '키를 넣으면 되겠지' 상태로
배선이 썩는다.

**이 공급자는 촬영물을 보지 않는다.** 파일명 규칙만 보고 정해진 답을 낸다.
진단에 쓰면 안 되고, 그래서 리포트에 공급자 이름이 그대로 찍힌다.
"""

from __future__ import annotations

from ..verdict import Verdict
from .provider import Judgment, JudgeRequest, Provider


class EchoProvider(Provider):
    """정해진 규칙대로만 답하는 공급자.

    규칙
        · 촬영물이 없으면 판단하지 않는다(None) — 근거 없는 판정을 만들지 않는다.
        · 파일명에 'viol' 이 있으면 위반, 아니면 적합.
    """

    name = "echo(시험용)"
    sends_media_externally = False

    def available(self) -> tuple[bool, str]:
        return True, "시험용 공급자입니다. 촬영물을 보지 않으므로 진단에 쓰지 마십시오."

    def judge(self, req: JudgeRequest) -> Judgment | None:
        if not req.media:
            return None
        names = [p.name for p in req.media]
        violated = any("viol" in n.lower() for n in names)
        return Judgment(
            verdict=Verdict.FAIL if violated else Verdict.PASS,
            confidence=0.9,
            rationale=f"시험용 공급자입니다. 파일명 규칙으로만 판단했습니다: {', '.join(names)}",
            cited=names,
            provider=self.name,
        )
