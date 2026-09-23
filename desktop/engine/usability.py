"""사용성 주의 — '기준은 통과했지만 쓰기 어렵다'를 말하는 자리.

별표5는 적합/부적합만 정한다. 그런데 합동 평가에서 정형외과 전문의가 지적했다 —
9.a 의 400~1,220mm 는 도달 **가능** 범위이고, 상단 1,220mm 는 어깨 굴곡 제한이 있는
고령자에게 도달은 되지만 통증을 동반한다. 1.b 의 2.5mm 는 본태성 진전 환자의
손 떨림 진폭보다 작다. **기준을 지켜도 실사용에서 포기가 생긴다.**

이 모듈이 하는 일과 하지 않는 일
────────────────────────────────────────────────────────────────────
    한다     기준을 통과한 측정값이 경계 근처인지 보고, 참고 메모를 붙인다
    안 한다  **판정을 바꾸지 않는다.** 적합은 적합으로 남는다.
             새로운 법적 등급을 만들지 않는다 — 별표5에 그런 것은 없다.

    임계값은 코드가 아니라 rules/usability-advisory.yaml 에 있다.
    별표5가 아닌 숫자를 별표5 파일에 섞지 않으려는 것이고,
    임상 근거로 확립된 값이 아니라 실무 기준이므로 **드러내 놓고 고칠 수 있어야**
    하기 때문이다. 현장에서 반증되면 그 파일만 고치면 된다.

왜 이렇게 해도 안전한가
    판정을 바꾸지 않으므로, 이 숫자가 틀려도 법적 판정은 틀어지지 않는다.
    과하게 표시되면 검토자가 넘기면 되고, 적게 표시되면 놓칠 뿐이다.
    그 비대칭이 이 기능을 안전하게 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .verdict import Assessment, Verdict


@dataclass
class Caution:
    """참고 메모 한 건. 판정이 아니다."""

    rule_id: str
    label: str
    measured: float
    unit: str
    band: str              # 어느 쪽 경계에 걸렸는가 (사람이 읽는 문장)
    why: str
    advice: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "항목": self.rule_id,
            "무엇": self.label,
            "측정값": self.measured,
            "구간": self.band,
            "왜": self.why,
            "제안": self.advice,
        }


def _number(value: Any) -> float | None:
    """측정값에서 숫자만 꺼낸다. 문자열이 섞여 있어도 죽지 않는다."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        for cut in ("mm", "dBA", "px", " "):
            cleaned = cleaned.replace(cut, "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def find(assessments: Sequence[Assessment], advisory: dict) -> list[Caution]:
    """적합 판정 중 경계 근처인 것을 골라 참고 메모를 만든다.

    **적합(PASS)만 본다.** 부적합·위반 의심은 이미 실행 카드로 나가므로
    여기서 또 말하면 같은 항목이 두 번 실린다.
    """
    by_id = {a.rule_id: a for a in assessments}
    out: list[Caution] = []

    for item in advisory.get("items", []):
        a = by_id.get(item["id"])
        if a is None or a.verdict is not Verdict.PASS:
            continue
        if not a.evidence or not a.evidence.measured:
            continue

        value = _number(a.evidence.measured.get(item["measured_key"]))
        if value is None:
            continue

        c = item.get("caution") or {}
        above, below = c.get("above_mm"), c.get("below_mm")
        band = ""
        if above is not None and value > above:
            band = f"{above}mm 초과 — 기준 상한에 가깝습니다"
        elif below is not None and value < below:
            band = f"{below}mm 미만 — 기준 하한에 가깝습니다"
        if not band:
            continue

        out.append(Caution(
            rule_id=item["id"],
            label=item.get("label", item["id"]),
            measured=round(value, 2),
            unit="mm",
            band=band,
            why=" ".join((item.get("why") or "").split()),
            advice=" ".join((item.get("advice") or "").split()),
        ))

    out.sort(key=lambda c: c.rule_id)
    return out


NOTE = (
    "아래는 **별표5 판정이 아닙니다.** 기준은 통과했지만 고령·근골격 제한이 있는 "
    "사용자에게는 부담이 될 수 있는 값입니다. 조치 의무는 없으며, 다음 발주나 "
    "교체 때 참고하시라고 적습니다."
)
