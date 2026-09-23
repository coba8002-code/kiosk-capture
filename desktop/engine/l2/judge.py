"""L2 판정 실행 — 공급자의 의견을 Assessment 로 바꾸는 유일한 통로.

대상은 ai_role 이 judge(판단) 또는 screen(선별)인 항목 25개다.

여기서 강제하는 두 가지가 L2 계층 전체의 안전장치다.

① 모델의 '적합'은 적합이 아니다 (L2_SCOPE_CEILING)
    별표5 judge 항목 상당수는 ai_verdict_scope 가 both 다.
    계측기라면 both 가 맞다 — 명도 대비 7.2:1 은 적합이 확실하다.
    그러나 모델이 화면을 **보고** "이해하기 쉬워 보인다"고 말한 것을
    법정 적합으로 인쇄하면, 그 리포트를 믿고 아무 조치도 하지 않은 점주가
    처분을 받는다.

    그래서 L2 를 통과하는 판정은 룰이 both 라도 fail_only 로 눌린다.
    위반 후보를 골라 주는 것까지가 모델의 몫이고, 적합 선언은 사람의 몫이다.
    이 강등은 리포트에 그대로 표시된다(사유: AI 판정 범위 밖 → 검토자).

② 실패를 판정으로 바꾸지 않는다
    네트워크 오류, 파싱 실패, 촬영물 없음 — 전부 미판정이고, 그 이유가 적힌다.
    '판단 못 했음'을 '위반 없음'으로 바꾸는 순간 자동화가 거짓말이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..rules import RuleSet
from ..verdict import Assessment, Evidence, Undetermined, Verdict, apply_ai_scope
from .provider import Judgment, Provider
from .prompt import request_from_rule

# 모델 판정에 적용하는 사정거리 상한. both 여도 여기까지만 인정한다.
L2_SCOPE_CEILING = "fail_only"

# 모델의 자기 확신도에 적용할 하한. 이보다 낮으면 판정을 버린다.
MIN_USABLE_CONFIDENCE = 0.55


@dataclass
class L2Run:
    provider: str
    reason: str = ""                                  # 못 돌린 경우 그 이유
    assessments: list[Assessment] = field(default_factory=list)
    judged: int = 0
    declined: int = 0                                 # 모델이 판단불가라고 물러선 수
    failed: list[str] = field(default_factory=list)   # 호출 자체가 실패한 항목

    @property
    def ran(self) -> bool:
        return bool(self.assessments) or self.judged > 0


def _effective_rule(rule: dict) -> dict:
    """L2 판정에 적용할 룰 사본. 사정거리만 눌러 내린다.

    원본 룰을 고치지 않는다 — 같은 항목을 계측기가 판정할 때는 both 가 맞다.
    눌리는 것은 '이번 판정이 모델에서 나왔다'는 사실 때문이다.
    """
    if rule.get("ai_verdict_scope") == "both":
        return {**rule, "ai_verdict_scope": L2_SCOPE_CEILING}
    return rule


def to_assessment(rule: dict, j: Judgment) -> Assessment:
    """공급자 의견 하나를 판정으로 바꾼다. apply_ai_scope 를 반드시 통과한다."""
    evidence = Evidence(
        crop_ref=(j.cited[0] if j.cited else None),
        criterion_id=rule["id"],
        confidence=j.confidence,
        measured={
            "판단 근거": j.rationale,
            "참조 촬영물": ", ".join(j.cited) or "없음",
            "공급자": j.provider,
            "주의": (
                "촬영물을 보고 낸 모델 판단입니다. 계측값이 아니며, "
                "모델의 '적합'은 적합으로 인정하지 않습니다."
            ),
        },
    )
    return apply_ai_scope(_effective_rule(rule), j.verdict, evidence=evidence)


def _media_for(rule: dict, bundle) -> list[Path]:
    """이 항목의 근거가 되는 촬영물. 룰의 evidence 세트를 그대로 따른다."""
    out: list[Path] = []
    for set_id in rule.get("evidence") or []:
        out += [s.path for s in bundle.by_set(set_id)]
    return out


def run(rs: RuleSet, bundle, provider: Provider, *,
        rule_ids: list[str] | None = None, progress=None) -> L2Run:
    """judge 역할 항목들을 공급자에 태운다."""
    ok, why = provider.available()
    if not ok:
        return L2Run(provider=provider.name, reason=why)

    # judge(판단)뿐 아니라 screen(선별)도 태운다.
    # 1.a '한 손으로 조작할 수 있는가', 1.h '지문 인식 장치가 손가락을 올리기 쉬운
    # 형태인가' 는 자로 잴 수 없고 촬영물을 보고 가려내는 일이다 — L2 가 할 일 그대로다.
    # screen 항목은 이미 전부 fail_only 라 위반 후보만 올라간다.
    L2_ROLES = ("judge", "screen")
    targets = [rs[r] for r in rule_ids] if rule_ids else [
        rs[rid] for rid in rs.ids() if rs[rid].get("ai_role") in L2_ROLES
    ]
    area_of = {
        item["id"]: cat["name"]
        for cat in rs.categories for item in cat["items"]
    }

    out = L2Run(provider=provider.name, reason=why)
    for rule in targets:
        media = _media_for(rule, bundle)
        if progress:
            progress(f"  {rule['id']:<5} 촬영물 {len(media)}건")
        if not media:
            continue

        req = request_from_rule(rule, area_of.get(rule["id"], ""), media)
        try:
            j = provider.judge(req)
        except Exception as exc:                                 # noqa: BLE001
            # 호출 실패를 판정으로 바꾸지 않는다
            out.failed.append(f"{rule['id']} — {exc}")
            continue

        if j is None:
            out.declined += 1
            continue
        # 공급자는 남이 만들 수 있다. 규약을 어긴 반환값이 판정으로 새어들지 않게 막는다 —
        # 예전에는 문자열을 돌려주면 AttributeError 로 진단 전체가 죽었다.
        if not isinstance(j, Judgment):
            out.failed.append(
                f"{rule['id']} — 공급자가 Judgment 가 아닌 {type(j).__name__} 을 반환했습니다")
            continue
        if j.verdict not in (Verdict.PASS, Verdict.FAIL, Verdict.NOT_APPLICABLE):
            out.failed.append(
                f"{rule['id']} — 공급자가 낼 수 없는 판정입니다: {j.verdict}")
            continue
        if j.confidence < MIN_USABLE_CONFIDENCE:
            out.declined += 1
            continue

        out.assessments.append(to_assessment(rule, j))
        out.judged += 1

    return out


def merge(base: list[Assessment], l2: list[Assessment]) -> list[Assessment]:
    """기존 판정에 L2 결과를 얹는다.

    이미 계측·점주·실측으로 값이 있는 항목은 **덮지 않는다.**
    계측값이 모델 의견보다 언제나 강하다.
    """
    have = {a.rule_id for a in base}
    return base + [a for a in l2 if a.rule_id not in have]
