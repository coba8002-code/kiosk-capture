"""L2 프롬프트 조립.

저작권 경계가 이 파일의 제일 중요한 제약이다.

    별표5      행정규칙·공공저작물 → 원문을 그대로 실어도 된다. 실어야 정확해진다.
    KS X 9211  '상업적 활용을 금지합니다' → **절 번호만.** 본문 한 줄도 싣지 않는다.

    tools/check_ks_copyright.py 가 이 파일도 검사한다.

프롬프트가 지켜야 할 것
    · 모델에게 '적합을 확정하라'고 시키지 않는다. 판단 대상은 '위반이 보이는가'다.
    · 확신이 없으면 판단불가를 고르라고 명시한다. 억지 판정이 제일 위험하다.
    · 근거로 어떤 파일의 어느 부분을 봤는지 말하게 한다. 검토자가 되짚을 수 있어야 한다.
"""

from __future__ import annotations

from .provider import JudgeRequest

SYSTEM = """\
당신은 무인정보단말기(키오스크) 접근성 진단을 돕는 분석자입니다.
「장애인·고령자 등의 정보접근 및 이용편의 증진을 위한 고시」 [별표 5]
무인정보단말기 접근성 검증 기준의 항목 하나를 놓고, 제출된 촬영물에
**위반으로 보이는 정황이 있는지** 판단합니다.

반드시 지킬 것
1. 당신의 판단은 확정이 아닙니다. 최종 판정은 사람 검토자가 합니다.
2. 촬영물에서 확인되지 않는 것을 추측해 채우지 마십시오.
   보이지 않으면 '판단불가'가 정답입니다. 억지 판정이 가장 나쁜 결과입니다.
3. '적합'은 '이 촬영물에서 위반이 보이지 않는다'는 뜻일 뿐입니다.
   기준을 충족했다는 보증이 아닙니다.
4. 근거는 반드시 제출된 파일에서 실제로 본 것만 적습니다.

응답은 아래 JSON 한 덩어리로만 하십시오. 다른 말을 덧붙이지 마십시오.
{"verdict": "적합|위반|해당없음|판단불가",
 "confidence": 0.0~1.0,
 "rationale": "무엇을 보고 그렇게 판단했는지 두 문장 이내",
 "cited": ["실제로 근거가 된 파일명"]}
"""


def build(req: JudgeRequest) -> str:
    """항목 하나에 대한 사용자 메시지를 만든다."""
    lines = [
        f"[항목] {req.rule_id}  ({req.area})",
        "",
        "[별표5 기준 원문]",
        req.criterion.strip(),
        "",
        f"[제출 촬영물] {req.media_note()}",
    ]

    if req.params:
        lines += ["", "[수치 기준]"]
        lines += [f"  · {k}: {v}" for k, v in req.params.items()]

    if req.ks_refs:
        # 절 번호만. KS X 9211:2025 본문은 상업적 활용이 금지되어 있어 싣지 않는다.
        lines += ["", "[관련 기술표준 — 절 번호만 표기, 본문 미첨부]"]
        lines += [f"  · {ref}" for ref in req.ks_refs]

    if req.note:
        lines += ["", "[참고]", req.note.strip()]

    lines += [
        "",
        "위 촬영물만 근거로, 이 항목에 위반으로 보이는 정황이 있는지 판단하십시오.",
        "촬영물에 판단할 근거가 없으면 반드시 '판단불가'를 선택하십시오.",
    ]
    return "\n".join(lines)


def request_from_rule(rule: dict, area: str, media) -> JudgeRequest:
    """룰 DB 항목 하나를 요청으로 바꾼다."""
    return JudgeRequest(
        rule_id=rule["id"],
        criterion=" ".join((rule.get("criterion") or "").split()),
        area=area,
        media=list(media),
        ks_refs=list(rule.get("ks_ref") or []),
        params=dict(rule.get("params") or {}),
        note=" ".join((rule.get("owner_question") or "").split()),
    )
