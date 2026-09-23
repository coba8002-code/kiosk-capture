"""판정 모델.

이 모듈의 존재 이유는 하나다 — **AI가 자기 사정거리를 넘는 결론을 내지 못하게 막는 것**.

별표5 판정은 과태료가 걸린 영역이다. 촬영물로 확정할 수 없는 항목에 AI가 '적합'을
찍어버리면, 그 리포트를 믿고 아무 조치도 하지 않은 점주가 처분을 받는다.
따라서 사정거리(ai_verdict_scope)를 데이터가 아니라 코드로 강제한다.

핵심 규칙 (annex5.yaml enums.ai_verdict_scope 와 1:1 대응)
    both      : AI가 적합·부적합 양방향 제시 가능
    fail_only : AI의 PASS 는 무조건 UNDETERMINED 로 강등된다
    none      : AI 판정 자체가 기각된다

그리고 어떤 경우에도 AI 단독으로 FAIL 이 확정되지 않는다.
AI 의 FAIL 은 SUSPECTED_FAIL 이고, 검토자 승인을 거쳐야 FAIL 이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """항목 하나의 판정 상태."""

    PASS = "적합"
    FAIL = "부적합"
    SUSPECTED_FAIL = "위반 의심"      # AI가 낼 수 있는 최대 강도
    UNDETERMINED = "미판정"
    NOT_APPLICABLE = "해당 없음"
    EXEMPT = "면제"


class Grade(str, Enum):
    """우수/보통 등급. 별표5 8개 항목에만 부여된다."""

    EXCELLENT = "우수"
    NORMAL = "보통"


class Undetermined(str, Enum):
    """미판정 사유. 리포트에 이 문장이 그대로 인쇄된다."""

    NO_INPUT = "입력 부족"
    MARKER_MISSING = "마커 불량"
    LOW_CONFIDENCE = "확신도 미달"
    OUT_OF_AI_SCOPE = "AI 판정 범위 밖"
    NEEDS_MEASURE = "전문 실측 필요"
    NEEDS_OWNER = "점주 확인 필요"
    NEEDS_USER_TEST = "사용자 검증 필요"


# 미판정 사유별로 "다음에 누가 이어받는가". 리포트 B의 핵심 열.
NEXT_OWNER: dict[Undetermined, str] = {
    Undetermined.NO_INPUT: "현장 재촬영",
    Undetermined.MARKER_MISSING: "현장 재촬영",
    Undetermined.LOW_CONFIDENCE: "검토자",
    Undetermined.OUT_OF_AI_SCOPE: "검토자",
    Undetermined.NEEDS_MEASURE: "전문 실측",
    Undetermined.NEEDS_OWNER: "점주",
    Undetermined.NEEDS_USER_TEST: "사용자 검증",
}


@dataclass
class Evidence:
    """판정 하나에 반드시 따라붙는 근거 4종.

    BUILD_SPEC 불변 규칙: 이 네 가지가 없는 판정은 리포트에 실리지 않는다.
    """

    crop_ref: str | None = None            # 근거 이미지 크롭 경로/ID
    measured: dict[str, Any] = field(default_factory=dict)  # 측정값 {ratio: 3.1, ...}
    criterion_id: str = ""                 # 별표5 항목 ID (예: "3.i")
    confidence: float | None = None        # 0.0~1.0. 결정론적 계측은 None 이 아니라 1.0
    note: str = ""

    def is_complete(self) -> bool:
        return bool(self.criterion_id) and (
            bool(self.measured) or bool(self.crop_ref) or bool(self.note)
        )


@dataclass
class Assessment:
    """항목 하나의 최종 판정 레코드."""

    rule_id: str
    verdict: Verdict
    grade: Grade | None = None
    reason: Undetermined | None = None
    next_owner: str | None = None
    evidence: Evidence | None = None
    source: str = "AI"                     # AI | REVIEWER | OWNER | MEASURE | SYSTEM
    downgraded_from: Verdict | None = None  # 사정거리 강제로 강등된 경우 원래 값

    def to_row(self) -> dict[str, Any]:
        """리포트 매트릭스 한 행."""
        return {
            "항목": self.rule_id,
            "판정": self.verdict.value,
            "등급": self.grade.value if self.grade else "",
            "사유": self.reason.value if self.reason else "",
            "다음 담당": self.next_owner or "",
            "출처": self.source,
            "측정값": (self.evidence.measured if self.evidence else {}),
            "확신도": (self.evidence.confidence if self.evidence else None),
        }


class ScopeViolation(RuntimeError):
    """엔진이 룰 DB에 없는 판정을 시도한 경우. 버그이므로 죽인다."""


def apply_ai_scope(
    rule: dict[str, Any],
    raw: Verdict,
    *,
    evidence: Evidence | None = None,
    grade: Grade | None = None,
    confidence_floor: float | None = None,
) -> Assessment:
    """AI 원시 판정에 사정거리를 적용해 최종 Assessment 를 만든다.

    엔진의 모든 AI 판정은 반드시 이 함수를 통과해야 한다.
    직접 Assessment 를 생성하는 코드 경로는 없어야 한다.

    Args:
        rule: annex5.yaml 의 항목 dict
        raw:  모델/계측기가 낸 원시 판정
        evidence: 근거 4종
        grade: 우수/보통 (해당 항목만)
        confidence_floor: 이 값 미만이면 확신도 미달로 강등. 기본 0.85
    """
    rule_id = rule["id"]
    scope = rule.get("ai_verdict_scope", "none")
    floor = 0.85 if confidence_floor is None else confidence_floor

    def undet(reason: Undetermined, downgraded: Verdict | None = None) -> Assessment:
        return Assessment(
            rule_id=rule_id,
            verdict=Verdict.UNDETERMINED,
            reason=reason,
            next_owner=NEXT_OWNER[reason],
            evidence=evidence,
            source="AI",
            downgraded_from=downgraded,
        )

    # --- 0) 통과·면제·비해당은 사정거리와 무관하게 그대로 둔다 ------------------
    if raw in (Verdict.NOT_APPLICABLE, Verdict.EXEMPT):
        return Assessment(rule_id=rule_id, verdict=raw, evidence=evidence, source="SYSTEM")

    # --- 1) AI가 관여하지 않는 항목 -------------------------------------------
    if scope == "none":
        track = rule.get("track", "")
        reason = {
            "C_MEASURE": Undetermined.NEEDS_MEASURE,
            "B_OWNER": Undetermined.NEEDS_OWNER,
            "D_USER": Undetermined.NEEDS_USER_TEST,
        }.get(track, Undetermined.OUT_OF_AI_SCOPE)
        return undet(reason, downgraded=raw)

    # --- 2) 근거 4종이 없으면 판정 자체를 버린다 -------------------------------
    if evidence is None or not evidence.is_complete():
        return undet(Undetermined.NO_INPUT, downgraded=raw)

    # --- 3) 확신도 게이트 (결정론적 계측은 confidence=1.0 로 들어온다) ---------
    conf = evidence.confidence
    if conf is not None and conf < floor:
        return undet(Undetermined.LOW_CONFIDENCE, downgraded=raw)

    # --- 4) 사정거리 강제 — 이 파일의 핵심 -------------------------------------
    if scope == "fail_only" and raw is Verdict.PASS:
        # AI는 위반을 잡아낼 수는 있어도 적합을 증명하지는 못한다.
        # 누가 이어받는지는 track 이 아니라 final_by 가 정한다 —
        # 1.b 처럼 track=A_AI 이면서 적합 확정은 실측이 하는 항목이 있기 때문이다.
        final = rule.get("final_by", "")
        reason = {
            "MEASURE": Undetermined.NEEDS_MEASURE,
            "OWNER": Undetermined.NEEDS_OWNER,
            "REVIEWER": Undetermined.OUT_OF_AI_SCOPE,
        }.get(final, Undetermined.OUT_OF_AI_SCOPE)
        return undet(reason, downgraded=Verdict.PASS)

    # --- 5) AI의 FAIL 은 언제나 '위반 의심'까지다 ------------------------------
    if raw is Verdict.FAIL:
        return Assessment(
            rule_id=rule_id,
            verdict=Verdict.SUSPECTED_FAIL,
            reason=None,
            next_owner="검토자",
            evidence=evidence,
            source="AI",
            downgraded_from=Verdict.FAIL,
        )

    # --- 6) 여기까지 온 PASS 만 AI 적합으로 인정 -------------------------------
    if raw is Verdict.PASS:
        return Assessment(
            rule_id=rule_id,
            verdict=Verdict.PASS,
            grade=_validate_grade(rule, grade),
            next_owner="검토자",
            evidence=evidence,
            source="AI",
        )

    if raw is Verdict.SUSPECTED_FAIL:
        return Assessment(
            rule_id=rule_id,
            verdict=Verdict.SUSPECTED_FAIL,
            next_owner="검토자",
            evidence=evidence,
            source="AI",
        )

    raise ScopeViolation(f"{rule_id}: 처리되지 않은 원시 판정 {raw!r}")


def _validate_grade(rule: dict[str, Any], grade: Grade | None) -> Grade | None:
    """우수/보통이 없는 항목에 등급이 붙는 것을 막는다."""
    has_grade = bool((rule.get("verdict") or {}).get("grade"))
    if grade is not None and not has_grade:
        raise ScopeViolation(f"{rule['id']}: 우수/보통 등급이 정의되지 않은 항목에 등급 부여")
    return grade if has_grade else None


def confirm(assessment: Assessment, *, approved: bool, reviewer: str) -> Assessment:
    """검토자 승인. '위반 의심'을 '부적합'으로 확정하는 유일한 경로.

    반려하면 미판정으로 되돌아간다 — 검토자가 반려했다고 적합이 되지는 않는다.
    적합 확정은 별도로 `record_pass` 를 쓴다.
    """
    if assessment.verdict is not Verdict.SUSPECTED_FAIL:
        return assessment

    if approved:
        return Assessment(
            rule_id=assessment.rule_id,
            verdict=Verdict.FAIL,
            evidence=assessment.evidence,
            source=f"REVIEWER:{reviewer}",
            next_owner="개선 담당",
            downgraded_from=assessment.downgraded_from,
        )

    return Assessment(
        rule_id=assessment.rule_id,
        verdict=Verdict.UNDETERMINED,
        reason=Undetermined.LOW_CONFIDENCE,
        next_owner=NEXT_OWNER[Undetermined.LOW_CONFIDENCE],
        evidence=assessment.evidence,
        source=f"REVIEWER:{reviewer}",
    )


def record_external(
    rule: dict[str, Any],
    verdict: Verdict,
    *,
    source: str,
    evidence: Evidence,
    grade: Grade | None = None,
) -> Assessment:
    """실측·점주 확인 등 AI 밖의 판정을 기록한다. 사정거리 제한을 받지 않는다."""
    return Assessment(
        rule_id=rule["id"],
        verdict=verdict,
        grade=_validate_grade(rule, grade),
        evidence=evidence,
        source=source,
    )
