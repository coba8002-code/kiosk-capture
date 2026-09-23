#!/usr/bin/env python
"""구멍 점검 — 무엇이 아직 없는지 코드에서 직접 대조한다.

    KFA.exe gaps

"무엇이 미개발인가"를 기억이나 문서에 의존해 답하면 반드시 틀린다.
문서는 낡고 기억은 낙관적이다. 그래서 **모듈을 실제로 불러** 기능이 있는지 보고,
룰 DB 와 대조한 결과만 출력한다.

소스 텍스트를 정규식으로 훑지 않는 이유는 두 번 데였기 때문이다.
한 번은 방금 만든 기능을 '없다'고 보고했고, 한 번은 실행 파일 안에 .py 가 없어
구멍 0개짜리 프로젝트가 구멍 34개로 보였다.

이 도구는 코드 연결만 확인한다. 구멍 0개는 현장 정확도나 제품 완성을 뜻하지 않는다.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import io
import sys
from collections import Counter
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import pipeline                        # noqa: E402
from engine import rules as R                      # noqa: E402
from engine.paths import resource_dir              # noqa: E402

ROOT = resource_dir()


def _read(rel: str) -> str:
    p = ROOT / rel
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _has(module_name: str, *attrs: str) -> bool:
    """모듈을 실제로 불러 기능이 있는지 본다.

    소스 텍스트를 정규식으로 훑던 방식은 두 번 틀렸다.
    첫 번째는 자기가 방금 만든 기능을 '없다'고 보고했고,
    두 번째는 **실행 파일 안에서 .py 가 없어 전부 '없다'가 됐다** —
    exe 로 돌리면 구멍 0개짜리 프로젝트가 구멍 34개로 보였다.
    코드가 있는지 알고 싶으면 코드를 부르면 된다.
    """
    try:
        mod = importlib.import_module(module_name)
    except Exception:                                    # noqa: BLE001
        return False
    return all(hasattr(mod, a) for a in attrs)


def audit() -> dict:
    rs = R.load()
    proto = R.load_protocol()
    app = _read("app/app.js")           # 앱은 datas 로 동봉되므로 실행 파일에서도 읽힌다

    # 파이프라인이 계측만으로 판정을 만드는 항목 — 파이프라인 자신의 선언을 믿는다
    auto = set(pipeline.AUTO_RULES)

    # L2 계층 — '코드가 없다'와 '켜지 않았다'는 전혀 다른 상태다. 뭉뚱그리면
    # 다 만들어 놓고도 영원히 미개발로 보고된다.
    from engine import l2 as L2mod
    l2_built = _has("engine.l2.judge", "run", "L2_SCOPE_CEILING", "to_assessment")
    l2_wired = "l2_provider" in inspect.signature(pipeline.analyze).parameters
    l2_ok, l2_why = L2mod.resolve().available()

    # OCR 계층 — 3.g 하나가 여기 달려 있다. L2 와 같은 구분을 적용한다.
    from engine import ocr as OCRmod
    from engine.calc import text_height as TH
    ocr_built = _has("engine.ocr.provider", "resolve", "Provider")
    ocr_wired = "ocr_provider" in inspect.signature(pipeline.analyze).parameters
    _ocr = OCRmod.resolve()
    ocr_ok, ocr_why = _ocr.available()
    _factor, ocr_calibrated, _ = TH.load_ocr_calibration(
        getattr(_ocr, "calibration_key", _ocr.name) if ocr_ok else "")

    items = []
    for cat in rs.categories:
        for r in cat["items"]:
            rid = r["id"]
            if r["track"] == "C_MEASURE":
                # AI 가 선별까지 하는 실측 항목은 따로 센다 —
                # '아무것도 안 한다'와 '위반 후보를 골라 준다'는 다르다
                if rid == "3.g" and not ocr_ok:
                    state = "실측 입력 대기 (OCR 미구성)"
                else:
                    state = ("실측 입력 대기 (AI 선별)" if rid in auto
                             else "실측 입력 대기")
            elif r["track"] == "B_OWNER":
                state = "점주 응답"
            elif rid in auto:
                state = "자동 판정"
            else:
                # judge 와 screen 은 둘 다 L2 가 맡는다 — 상태를 나눠 보고하면
                # 다 만들어 놓은 경로를 '미개발'로 잘못 세게 된다.
                judge_state = "L2 미구성" if (l2_built and l2_wired) else "L2 미개발"
                state = {"judge": judge_state, "screen": judge_state,
                         "measure": "계측기 미개발"}.get(r["ai_role"], "미분류")
            items.append({"id": rid, "track": r["track"], "role": r["ai_role"], "state": state})

    # 촬영 세트별 소비자
    sets = []
    for s in proto["sets"]:
        sid = s["id"]
        consumed = sid in pipeline.CONSUMED_SETS
        via_l2 = sid in pipeline.L2_CONSUMED_SETS
        sets.append({"id": sid, "name": s["name"], "feeds": len(rs.fed_by(sid)),
                     "consumed": consumed, "via_l2": via_l2,
                     "required": bool(s.get("required"))})

    # 사람 손이 필요한 경로.
    # 탐지 기준은 **그 기능을 실제로 수행하는 코드**를 봐야 한다.
    # 잘못된 구멍을 보고하는 점검기는 놓치는 것만큼 나쁘다.
    paths = [
        {"name": "검토자 승인 (위반 의심 → 부적합)",
         "impl": _has("engine.pipeline", "apply_review")
                 and _has("tools.review", "review_suspected"),
         "blocks": sum(1 for i in items if i["state"] == "자동 판정"),
         "why": "없으면 AI 가 찾은 위반이 영원히 '위반 의심'에 머문다"},
        {"name": "실측값 입력",
         "impl": _has("tools.review", "input_measurements"),
         "blocks": len(rs.by_track("C_MEASURE")),
         "why": "manifest 를 손으로 고치지 않으면 실측 7항목이 들어갈 자리가 없다"},
        {"name": "면제 경로 선택 (무인민원발급기)",
         "impl": _has("tools.review", "pick_exemptions"),
         "blocks": len(rs.exemptions[0]["exempts"]) if rs.exemptions else 0,
         "why": "공공 민원 단말이면 6항목이 진단 범위에서 빠진다"},
        {"name": "  └ 앱에서도 지정 가능",
         "impl": "toggleExemption" in app,
         "blocks": 0,
         "why": "지금은 검토자 콘솔에서만 지정된다"},
        {"name": "우수/보통 등급 부여",
         "impl": _has("tools.review", "set_grades"),
         "blocks": len(rs.graded()),
         "why": "별표5 8항목은 적합/부적합 외에 등급이 따로 있다"},
        {"name": "리포트 A/B 인쇄본 (A4/HTML)",
         "impl": _has("engine.report.render", "render_owner", "render_authority"),
         "blocks": 0,
         "why": "지금은 마크다운·CSV 만 나온다. Figma 설계본이 구현 안 됨"},
        {"name": "L2 판정 (촬영물을 보고 선별)",
         "impl": l2_built and l2_wired,
         "blocks": sum(1 for i in items if i["state"].startswith("L2")),
         "why": "별표5 25항목이 여기 달려 있다"},
        {"name": "OCR 판정 (문자 높이)",
         "impl": ocr_built and ocr_wired,
         "blocks": 1,
         "why": "3.g 가 여기 달려 있다"},
    ]

    return {"rules": rs, "items": items, "sets": sets, "paths": paths,
            "l2": {"built": l2_built, "wired": l2_wired,
                   "configured": l2_ok, "why": l2_why},
            "ocr": {"built": ocr_built, "wired": ocr_wired, "configured": ocr_ok,
                    "calibrated": ocr_calibrated, "engine": _ocr.name, "why": ocr_why}}


def main() -> int:
    ap = argparse.ArgumentParser(description="미개발 구멍 점검")
    ap.add_argument("--verbose", action="store_true", help="항목별 상세")
    a = ap.parse_args()

    d = audit()
    items, sets, paths = d["items"], d["sets"], d["paths"]
    total = len(items)

    print("=" * 76)
    print(" 구멍 점검 — 코드에서 직접 대조한 결과")
    print("=" * 76)

    print("\n1. 별표5 40항목 — 판정이 어디까지 오는가\n" + "-" * 76)
    cnt = Counter(i["state"] for i in items)
    order = ["자동 판정", "점주 응답", "실측 입력 대기 (AI 선별)", "실측 입력 대기",
             "실측 입력 대기 (OCR 미구성)",
             "L2 미구성", "L2 미개발", "계측기 미개발", "스크리닝 미개발"]
    for k in order:
        n = cnt.get(k, 0)
        if not n:
            continue
        bar = "█" * round(n / total * 34) + "·" * (34 - round(n / total * 34))
        mark = "  " if ("미개발" in k or "대기" in k or "미구성" in k) else "○ "
        print(f"   {mark}{k:<22}{n:>3}항목  {bar}")
    done = cnt.get("자동 판정", 0) + cnt.get("점주 응답", 0)
    print(f"\n   → 사람 개입 없이 값이 나오는 항목: {cnt.get('자동 판정', 0)}/{total}"
          f"   앱까지 포함: {done}/{total}")

    if a.verbose:
        print("\n   미개발 항목")
        for i in items:
            if "미개발" in i["state"]:
                print(f"     {i['id']:<6}{i['role']:<9}{i['state']}")

    print("\n2. 촬영 세트 — 찍는데 아무도 안 읽는 것\n" + "-" * 76)
    orphan = 0
    for s in sets:
        if s["consumed"]:
            state = "소비 중"
        elif s["via_l2"]:
            state = "음성·영상 자동 분석 미지원 — 검토자가 직접 확인"
            orphan += 1
        elif s["feeds"]:
            state = f"소비자 없음  ← {s['feeds']}항목이 이 세트를 기다린다"
            orphan += 1
        else:
            state = "참고 수집 (판정 대상 아님)"
        print(f"   {s['id']}  {s['name']:<14}기여 {s['feeds']:>2}항목   {state}")
    if orphan:
        print(f"\n   → {orphan}개 세트가 촬영은 되지만 파이프라인이 읽지 않는다.")

    l2 = d["l2"]
    l2_items = sum(1 for i in items if i["state"].startswith("L2"))
    print(f"\n   L2 계층 — 촬영물을 보고 판단·선별하는 {l2_items}항목")
    print(f"     코드 {'있음' if l2['built'] else '없음'}"
          f"   파이프라인 연결 {'있음' if l2['wired'] else '없음'}"
          f"   공급자 {'설정됨' if l2['configured'] else '설정 안 됨'}")
    if not l2["configured"]:
        print(f"     {l2['why']}")
    if l2["built"] and not l2["configured"]:
        print("     → 코드는 있고 켜지지 않았습니다. 켜는 법은 OPERATIONS.md 를 보세요.")

    o = d["ocr"]
    print("\n   OCR 계층 — 문자 높이(3.g) 1항목")
    print(f"     코드 {'있음' if o['built'] else '없음'}"
          f"   파이프라인 연결 {'있음' if o['wired'] else '없음'}"
          f"   엔진 {o['engine'] if o['configured'] else '없음'}"
          f"   상자 계수 {'교정됨' if o['calibrated'] else '미교정'}")
    if not o["configured"]:
        print(f"     {o['why']}")
    elif not o["calibrated"]:
        # 교정하지 않으면 문자 높이가 통째로 틀어져 위반을 놓칠 수 있다
        print("     → 엔진은 있는데 상자 계수가 미교정입니다.  KFA.exe calibrate-ocr")

    print("\n3. 사람 손이 필요한 경로\n" + "-" * 76)
    missing = [p for p in paths if not p["impl"]]
    for p in paths:
        mark = "○" if p["impl"] else "✗"
        blocks = f"{p['blocks']}항목 막힘" if p["blocks"] else ""
        print(f"   {mark} {p['name']:<34}{blocks}")
        if not p["impl"]:
            print(f"       {p['why']}")

    print("\n" + "=" * 76)
    holes = len(missing) + orphan + sum(1 for i in items if "미개발" in i["state"])
    if holes == 0:
        print(" 구멍 없음")
    else:
        print(f" 구멍 {holes}개 — 항목 {sum(1 for i in items if '미개발' in i['state'])}, "
              f"세트 {orphan}, 경로 {len(missing)}")
    print("=" * 76 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
