"""리포트 생성 — 같은 판정 데이터에서 두 벌을 만든다.

리포트 A (점주용 한 장)   : 실행 카드 3단. "오늘 / 이번 분기 / 교체 시 검토"
리포트 B (발주·지자체용) : 별표5 40항목 매트릭스 + 미판정 사유와 다음 담당

B 의 신뢰 장치는 **미판정을 빈칸이 아니라 명시 항목으로 인쇄하는 것**이다.
"이 항목은 사진으로 판정할 수 없어 전문 실측이 필요하며, 다음 담당은 계측 업체입니다"
라는 문장이 들어간 리포트가 40항목을 전부 채운 리포트보다 신뢰를 얻는다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import usability
from ..rules import RuleSet
from ..verdict import NEXT_OWNER, Assessment, Undetermined, Verdict

# 실행 카드 3단 배치 규칙.
#   즉시  : 부적합 + 비용 1등급 + 난이도 낮음 → 오늘 바로 고칠 수 있는 것
#   분기  : 부적합 나머지 중 비용 2등급 이하
#   검토  : 비용 3등급 또는 난이도 높음 → 교체 검토 대상
URGENCY_LABEL = {
    "now": "오늘 할 것",
    "quarter": "이번 분기",
    "review": "교체 시 검토",
}

REMEDY_OWNER = {
    "SW": "키오스크 개발사",
    "HW": "설치·유지보수 업체",
    "OPS": "점주",
    "NONE": "개별 검토",
}


@dataclass
class ActionCard:
    """리포트 A 의 실행 카드 한 장."""

    rule_id: str
    category: str
    urgency: str
    title: str
    owner: str
    remedy_type: str
    difficulty: str
    cost_band: int
    evidence_crop: str | None = None
    measured: dict[str, Any] = field(default_factory=dict)
    related: str = ""          # 다른 항목의 판정이 조치를 바꾸는 경우

    def to_dict(self) -> dict[str, Any]:
        return {
            "항목": self.rule_id,
            "영역": self.category,
            "시급도": URGENCY_LABEL[self.urgency],
            "내용": self.title,
            "담당": self.owner,
            "처방": self.remedy_type,
            "난이도": self.difficulty,
            "비용대역": self.cost_band,
            "근거": self.evidence_crop,
            **({"함께 볼 것": self.related} if self.related else {}),
        }


@dataclass
class ReportBundle:
    """진단 1건의 산출물 전체."""

    device_id: str
    product_type: str
    summary: dict[str, Any]
    matrix: list[dict[str, Any]]          # 리포트 B
    cards: list[ActionCard]               # 리포트 A
    undetermined: list[dict[str, Any]]    # 미판정 명세 — B 의 핵심
    scope_note: str
    cautions: list[Any] = field(default_factory=list)   # 사용성 주의 — 판정 아님


def _urgency(remedy: dict[str, Any]) -> str:
    cost = remedy.get("cost_band", 3)
    difficulty = remedy.get("difficulty", "높음")
    if cost >= 3 or difficulty == "높음":
        return "review"
    if cost == 1 and difficulty == "낮음":
        return "now"
    return "quarter"


def build(
    rs: RuleSet,
    assessments: Sequence[Assessment],
    *,
    device_id: str,
    product_type: str,
    available_scopes: set[str] | None = None,
    exemption_ids: set[str] | None = None,
    l2_provider: str | None = None,
    advisory: dict[str, Any] | None = None,
) -> ReportBundle:
    """판정 목록을 리포트 두 벌로 만든다."""
    applicable = rs.applicable(
        product_type=product_type,
        available_scopes=available_scopes,
        exemption_ids=exemption_ids,
    )
    applicable_ids = {r["id"] for r in applicable}
    exempted = rs.exempted_by(exemption_ids or set())

    by_id = {a.rule_id: a for a in assessments}

    matrix: list[dict[str, Any]] = []
    cards: list[ActionCard] = []
    undetermined: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_category: dict[str, Counter[str]] = defaultdict(Counter)

    for cat in rs.categories:
        for rule in cat["items"]:
            rid = rule["id"]
            cat_name = f"{cat['area']}. {cat['name']}"

            # 이번 진단에서 평가 대상이 아닌 항목도 매트릭스에는 남긴다.
            if rid in exempted:
                verdict, reason, owner = Verdict.EXEMPT, None, None
            elif rid not in applicable_ids:
                verdict, reason, owner = Verdict.NOT_APPLICABLE, None, None
            else:
                a = by_id.get(rid)
                if a is None:
                    # 판정이 아직 없는 항목. 사유는 트랙이 정한다 —
                    # 실측·점주 항목을 '촬영 부족'이라고 적으면 엉뚱한 사람을 부르게 된다.
                    verdict = Verdict.UNDETERMINED
                    reason = {
                        "C_MEASURE": Undetermined.NEEDS_MEASURE,
                        "B_OWNER": Undetermined.NEEDS_OWNER,
                        "D_USER": Undetermined.NEEDS_USER_TEST,
                    }.get(rule["track"], Undetermined.NO_INPUT)
                    owner = NEXT_OWNER[reason]
                else:
                    verdict, reason, owner = a.verdict, a.reason, a.next_owner

            a = by_id.get(rid)
            counts[verdict.value] += 1
            by_category[cat_name][verdict.value] += 1

            row: dict[str, Any] = {
                "영역": cat_name,
                "항목": rid,
                "구분": rule["scope"],
                "판정": verdict.value,
                "등급": (a.grade.value if a and a.grade else ""),
                "사유": (reason.value if reason else ""),
                "다음 담당": owner or "",
                "트랙": rule["track"],
                "AI 사정거리": rule["ai_verdict_scope"],
                "측정값": (a.evidence.measured if a and a.evidence else {}),
                "확신도": (a.evidence.confidence if a and a.evidence else None),
                "출처": (a.source if a else ""),
            }
            matrix.append(row)

            if verdict is Verdict.UNDETERMINED:
                undetermined.append({
                    "항목": rid,
                    "영역": cat_name,
                    "사유": reason.value if reason else "",
                    "다음 담당": owner or "",
                    "필요 입력": rule.get("evidence", []),
                    "마커 필요": rule.get("marker_required", False),
                    "안내": _undetermined_hint(rule, reason),
                })

            if verdict in (Verdict.FAIL, Verdict.SUSPECTED_FAIL):
                remedy = rule["remedy"]
                cards.append(ActionCard(
                    rule_id=rid,
                    category=cat_name,
                    urgency=_urgency(remedy),
                    title=_short_title(rule),
                    owner=REMEDY_OWNER.get(remedy["type"], "개별 검토"),
                    remedy_type=remedy["type"],
                    difficulty=remedy["difficulty"],
                    cost_band=remedy["cost_band"],
                    evidence_crop=(a.evidence.crop_ref if a and a.evidence else None),
                    measured=(a.evidence.measured if a and a.evidence else {}),
                    related=_related_note(rid, by_id),
                ))

    order = {"now": 0, "quarter": 1, "review": 2}
    cards.sort(key=lambda c: (order[c.urgency], c.cost_band, c.rule_id))

    evaluated = len(applicable_ids)
    decided = counts[Verdict.PASS.value] + counts[Verdict.FAIL.value]

    summary = {
        "기기": device_id,
        "제품 구분": product_type,
        "별표5 전체 항목": len(rs),
        "이번 진단 대상": evaluated,
        "면제": counts[Verdict.EXEMPT.value],
        "해당 없음": counts[Verdict.NOT_APPLICABLE.value],
        "적합": counts[Verdict.PASS.value],
        "부적합": counts[Verdict.FAIL.value],
        "위반 의심": counts[Verdict.SUSPECTED_FAIL.value],
        "미판정": counts[Verdict.UNDETERMINED.value],
        "확정률": round(decided / evaluated * 100, 1) if evaluated else 0.0,
        "영역별": {k: dict(v) for k, v in by_category.items()},
    }

    return ReportBundle(
        device_id=device_id,
        product_type=product_type,
        summary=summary,
        matrix=matrix,
        cards=cards,
        undetermined=undetermined,
        scope_note=SCOPE_NOTE + (l2_note(l2_provider) if l2_provider else ""),
        # 적합 판정 중 경계 근처인 것. 판정을 바꾸지 않고 참고로만 싣는다.
        cautions=(usability.find(assessments, advisory) if advisory else []),
    )


# 한 항목의 조치가 다른 항목의 판정에 따라 달라지는 짝.
# 합동 평가에서 나온 지적이다 — '작지만 키울 수 있음'과 '작고 키울 수도 없음'은
# 점주가 해야 할 일이 완전히 다른데 리포트가 그 차이를 말해 주지 않았다.
RELATED_RULES = {
    "3.g": {
        "other": "3.h", "label": "문자 확대",
        "pass": "문자 확대 기능은 사용할 수 있습니다. 기본 문자 높이의 미달 여부와 개선 필요성은 별도로 확인하세요.",
        "fail": "글씨가 작은데 **키울 수도 없는 상태**입니다. 우선순위가 올라갑니다.",
    },
    "3.i": {
        "other": "3.j", "label": "고대비 화면",
        "pass": "사용자가 고대비 화면을 켤 수 있으므로, 대안이 하나는 있습니다.",
        "fail": "색 대비가 낮은데 **고대비 화면도 없습니다**. 우선순위가 올라갑니다.",
    },
}


def _related_note(rule_id: str, by_id: dict[str, Assessment]) -> str:
    """짝이 되는 항목의 판정을 한 줄로 붙인다.

    같은 '부적합'이라도 짝 항목의 상태에 따라 점주가 할 일이 달라진다.
    글씨가 작아도 키울 수 있으면 급하지 않고, 키울 수도 없으면 급하다.
    """
    pair = RELATED_RULES.get(rule_id)
    if not pair:
        return ""
    other_id, label = pair["other"], pair["label"]
    other = by_id.get(other_id)
    if other is None:
        return f"{label}({other_id}) 가 아직 판정되지 않았습니다 — 함께 확인하세요."
    if other.verdict is Verdict.PASS:
        return f"{label}({other_id}) 적합 — {pair['pass']}"
    if other.verdict in (Verdict.FAIL, Verdict.SUSPECTED_FAIL):
        return f"{label}({other_id}) 미달 — {pair['fail']}"
    return f"{label}({other_id}) 판정: {other.verdict.value} — 함께 확인하세요."


def _short_title(rule: dict[str, Any]) -> str:
    """점주용 실행 카드의 한 줄 제목.

    **룰 DB 의 '쉬운 말'을 먼저 쓴다.** 고시 원문은 점주가 읽지 못한다 —
    합동 평가에서 '개인청취장치', '촉각 표시' 에서 참여자 세 명 모두 멈췄고,
    '최소경계상자(MBR: Minimum Bounding Box)' 같은 문구는 무엇을 하라는 건지
    알 수 없다는 답이 돌아왔다.

    원문이 사라지는 것은 아니다. 담당 기관용 리포트 B 의 40항목 매트릭스에는
    criterion 이 그대로 실린다. 독자가 다르면 문구도 달라야 한다.
    """
    plain = " ".join((rule.get("plain") or "").split())
    if plain:
        return plain

    # 쉬운 말이 없으면 원문을 줄여 쓴다 (룰 DB 가 낡았을 때의 대비)
    text = " ".join((rule.get("criterion") or "").split())
    for stop in ("어야 한다", "하여야 한다", "되어야 한다", "안된다", "안 된다"):
        idx = text.find(stop)
        if idx != -1:
            return text[: idx + len(stop)]
    return text[:80] + ("…" if len(text) > 80 else "")


def _undetermined_hint(rule: dict[str, Any], reason: Undetermined | None) -> str:
    """무엇을 하면 이 항목이 판정 가능해지는지."""
    if reason is Undetermined.NEEDS_MEASURE:
        inst = rule.get("instrument") or "계측 장비"
        return f"{inst} 로 현장 실측이 필요합니다."
    if reason is Undetermined.NEEDS_OWNER:
        return rule.get("owner_question", "점주 현장 확인이 필요합니다.")
    if reason is Undetermined.NEEDS_USER_TEST:
        return "별표5 2항 사용자 검증 대상입니다. 촬영·계측으로 대체되지 않습니다."
    if reason is Undetermined.MARKER_MISSING:
        return "기준 마커가 검출되지 않았습니다. 마커를 부착해 재촬영하세요."
    if reason is Undetermined.LOW_CONFIDENCE:
        return "자동 판정 확신도가 기준에 못 미쳐 검토자 확인 대기 중입니다."
    if reason is Undetermined.NO_INPUT:
        sets = ", ".join(rule.get("evidence", [])) or "해당 촬영 세트"
        note = rule.get("capture_note")
        base = f"{sets} 촬영이 필요합니다."
        return f"{base} {note}" if note else base
    return "판정 범위 밖입니다."


SCOPE_NOTE = (
    "이 리포트는 「무인정보단말기 접근성 검증 기준」(고시 별표5) 1. 설계지침 검증 기준에 "
    "대한 진단 결과입니다. 별표5 2. 사용자 검증 기준(장애인·고령자 12명 대상)은 이 진단의 "
    "범위에 포함되지 않으며, 촬영·계측으로 대체되지 않습니다. "
    "'위반 의심'은 자동 분석 결과이며 검토자 승인을 거쳐야 '부적합'으로 확정됩니다. "
    "이 리포트는 법정 적합성 인증이 아닙니다."
)


def l2_note(provider: str) -> str:
    """L2 를 쓴 진단은 그 사실을 리포트가 스스로 밝힌다.

    어떤 판정이 계측에서 나왔고 어떤 것이 모델 판단에서 나왔는지 읽는 사람이
    구분하지 못하면, 리포트는 있는 그대로를 말하지 않는 것이 된다.
    """
    return (
        f" 이 진단에는 촬영물을 보고 판단하는 자동 선별(공급자: {provider})이 사용되었습니다. "
        "해당 판단은 계측값이 아니며, 모델이 낸 '적합'은 적합으로 인정하지 않고 "
        "전부 검토자 확인 대상으로 넘겼습니다. 항목별 '측정값'에 공급자가 표시됩니다."
    )


def to_markdown(bundle: ReportBundle) -> str:
    """리포트 B 를 마크다운으로. PDF·HWP 변환의 중간 형식."""
    s = bundle.summary
    lines: list[str] = [
        f"# 접근성 진단 리포트 — {bundle.device_id}",
        "",
        f"제품 구분: **{bundle.product_type}** · 진단 대상 **{s['이번 진단 대상']}**항목 "
        f"(별표5 전체 {s['별표5 전체 항목']}항목 중)",
        "",
        "## 요약",
        "",
        "| 적합 | 부적합 | 위반 의심 | 미판정 | 면제 | 해당 없음 | 확정률 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {s['적합']} | {s['부적합']} | {s['위반 의심']} | {s['미판정']} | "
        f"{s['면제']} | {s['해당 없음']} | {s['확정률']}% |",
        "",
    ]

    if bundle.undetermined:
        lines += [
            "## 미판정 항목과 다음 담당",
            "",
            "> 이 표는 이 진단이 **무엇을 판정하지 못했는지** 밝히는 부분입니다.",
            "",
            "| 항목 | 영역 | 사유 | 다음 담당 | 조치 |",
            "|---|---|---|---|---|",
        ]
        for u in bundle.undetermined:
            hint = u["안내"].replace("|", "／")
            lines.append(
                f"| {u['항목']} | {u['영역']} | {u['사유']} | {u['다음 담당']} | {hint} |"
            )
        lines.append("")

    lines += [
        "## 별표5 항목별 판정",
        "",
        "| 영역 | 항목 | 구분 | 판정 | 등급 | 사유 | 다음 담당 | 출처 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in bundle.matrix:
        lines.append(
            f"| {row['영역']} | {row['항목']} | {row['구분']} | {row['판정']} | "
            f"{row['등급']} | {row['사유']} | {row['다음 담당']} | {row['출처']} |"
        )

    lines += ["", "---", "", bundle.scope_note, ""]
    return "\n".join(lines)


def owner_checklist(rs: RuleSet, bundle: ReportBundle) -> list[dict[str, str]]:
    """점주가 현장에서 답할 문항만 뽑아 낸다 (Track B)."""
    pending = {u["항목"] for u in bundle.undetermined if u["다음 담당"] == "점주"}
    out = []
    for r in rs.by_track("B_OWNER"):
        if r["id"] in pending or not pending:
            out.append({
                "항목": r["id"],
                "질문": " ".join((r.get("owner_question") or "").split()),
            })
    return out
