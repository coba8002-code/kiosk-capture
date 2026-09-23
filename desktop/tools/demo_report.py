#!/usr/bin/env python
"""엔드투엔드 데모 — 가상의 무인카페 주문기 1대를 진단해 리포트 두 벌을 만든다.

    python tools/demo_report.py
    python tools/demo_report.py --markdown > report.md

실제 파이프라인에서는 L1 계측기와 L2 모델이 채우는 자리를, 여기서는
현장에서 흔히 나오는 값으로 채워 넣었다. 배선이 제대로 되어 있는지,
그리고 미판정이 리포트에 제대로 인쇄되는지 확인하는 용도다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir  # noqa: E402

from engine import rules as R
from engine.calc import contrast, geometry, scale
from engine.report import matrix
from engine.verdict import Evidence, Grade, Verdict, apply_ai_scope, confirm, record_external

DEVICE = "GB-CAFE-001 (무인카페 주문기 · 구미)"
PRODUCT = "중대형"
SCOPES = {"물건 투입", "배출물", "개인정보 입력"}   # 이 기기에 있는 조건부 기능


def run() -> matrix.ReportBundle:
    rs = R.load()
    out = []

    # ── L1 결정론적 계측 ─────────────────────────────────────────────────────
    # 3.i 명도 대비 — 회색 안내문구가 흰 배경에 얹혀 있다
    c = contrast.evaluate("#9AA3AB", "#FFFFFF", required=4.5, dispersion=6.0)
    fix = contrast.nearest_passing_color("#9AA3AB", "#FFFFFF", target=4.5)
    ev = Evidence(
        crop_ref="S1-01/crop_0042.png",
        measured={**c.as_measured(), "권장 색": fix},
        criterion_id="3.i",
        confidence=c.confidence,
    )
    out.append(apply_ai_scope(rs["3.i"], Verdict.FAIL if not c.passed else Verdict.PASS, evidence=ev))

    # 3.j 고대비 화면 — 고대비 모드가 아예 없다
    out.append(apply_ai_scope(
        rs["3.j"], Verdict.FAIL,
        evidence=Evidence(
            crop_ref="S1-03/none.png",
            measured={"고대비 모드": "미제공", "기본 화면 최소 대비": 3.2, "기준": 7.0},
            criterion_id="3.j", confidence=1.0,
        ),
    ))

    # 1.b 버튼 간격 — 마커 환산으로 터치 버튼 간격 측정
    s = scale.build_plane_scale(
        [(120, 210), (520, 214), (516, 612), (124, 606)],
        plane="display", frame_width_px=4032,
    )
    boxes = [
        geometry.Box("메뉴1", 0, 0, 28, 28),
        geometry.Box("메뉴2", 30, 0, 28, 28),      # 간격 2.0mm → 위반
        geometry.Box("메뉴3", 60, 0, 28, 28),
    ]
    g = geometry.check_gaps(boxes, surface="touch")
    out.append(apply_ai_scope(
        rs["1.b"], Verdict.PASS if g.passed else Verdict.FAIL,
        evidence=Evidence(
            crop_ref="S1-01/keypad.png",
            measured={**g.as_measured(), "마커 확신도": s.confidence},
            criterion_id="1.b", confidence=round(s.confidence, 3),
        ),
    ))

    # 8.a 깜박임 — 정적 화면
    out.append(apply_ai_scope(
        rs["8.a"], Verdict.PASS,
        evidence=Evidence(
            crop_ref="S2-01/t0-30s",
            measured={"최고 깜박임": 0.0, "기준": "3회/초 미만", "프레임률": 60.0},
            criterion_id="8.a", confidence=1.0,
        ),
    ))

    # ── L2 멀티모달 판정 ─────────────────────────────────────────────────────
    out.append(apply_ai_scope(
        rs["7.h"], Verdict.FAIL,
        evidence=Evidence(
            crop_ref="S1-01/crop_0107.png",
            measured={"지적 문구": ["픽업 디바이스 어셈블", "논-다이닝 옵션"], "검사 문구 수": 46},
            criterion_id="7.h", confidence=0.91,
        ),
    ))
    out.append(apply_ai_scope(
        rs["4.a"], Verdict.PASS,
        evidence=Evidence(
            crop_ref="S1-01/greyscale_diff.png",
            measured={"색 단독 구분 요소": 0, "검사 요소 수": 38},
            criterion_id="4.a", confidence=0.93,
        ),
    ))
    out.append(apply_ai_scope(
        rs["3.a"], Verdict.FAIL,
        evidence=Evidence(
            crop_ref="S1-01+S5-01/align.png",
            measured={"화면 텍스트 항목": 46, "음성 대응 항목": 12, "미대응": 34},
            criterion_id="3.a", confidence=0.88,
        ),
    ))
    # 7.f — 확신도가 낮아 자동으로 검토자 큐로 간다
    out.append(apply_ai_scope(
        rs["7.f"], Verdict.PASS,
        evidence=Evidence(
            crop_ref="S1-01/icons.png",
            measured={"문자 병기 없는 기호": 3, "국가표준 기호 추정": 2},
            criterion_id="7.f", confidence=0.61,
        ),
    ))
    # 1.a — screen 역할. 위반 신호 없음 → fail_only 라 적합 확정 못 함
    out.append(apply_ai_scope(
        rs["1.a"], Verdict.PASS,
        evidence=Evidence(
            crop_ref="S2-01/gestures.png",
            measured={"멀티터치 강제 구간": 0, "동시 입력 요구": 0},
            criterion_id="1.a", confidence=0.9,
        ),
    ))

    # ── 점주 셀프 체크 ───────────────────────────────────────────────────────
    out.append(record_external(
        rs["3.b"], Verdict.FAIL, source="OWNER",
        evidence=Evidence(criterion_id="3.b", measured={"응답": "아니오 — 이어폰 단자 없음"}),
    ))
    out.append(record_external(
        rs["3.f"], Verdict.PASS, source="OWNER", grade=Grade.NORMAL,
        evidence=Evidence(criterion_id="3.f", measured={"응답": "예 — 5번 키 돌기 있음"}),
    ))

    # ── 전문 실측 ────────────────────────────────────────────────────────────
    out.append(record_external(
        rs["9.a"], Verdict.FAIL, source="MEASURE:구미계측",
        evidence=Evidence(
            criterion_id="9.a",
            measured={"결제 모듈 높이": 1285.0, "기준": "400~1,220mm", "초과": 65.0},
        ),
    ))
    out.append(record_external(
        rs["3.g"], Verdict.PASS, source="MEASURE:구미계측",
        evidence=Evidence(criterion_id="3.g", measured={"최소 문자 높이": 8.1, "기준": 7.25}),
    ))

    # ── 검토자 승인 ──────────────────────────────────────────────────────────
    approved = {"3.i", "3.j", "3.a", "7.h"}
    out = [confirm(a, approved=True, reviewer="박검토") if a.rule_id in approved else a
           for a in out]

    return matrix.build(
        rs, out,
        device_id=DEVICE, product_type=PRODUCT, available_scopes=SCOPES,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true", help="리포트 B 를 마크다운으로 출력")
    args = ap.parse_args()

    bundle = run()

    if args.markdown:
        print(matrix.to_markdown(bundle))
        return 0

    s = bundle.summary
    print("=" * 74)
    print(f" 진단 결과 — {bundle.device_id}")
    print("=" * 74)
    print(f"\n  제품 구분   {s['제품 구분']}")
    print(f"  진단 대상   {s['이번 진단 대상']}항목  (별표5 전체 {s['별표5 전체 항목']}항목)")
    print(f"\n  적합 {s['적합']}   부적합 {s['부적합']}   위반 의심 {s['위반 의심']}   "
          f"미판정 {s['미판정']}   해당 없음 {s['해당 없음']}")
    print(f"  확정률 {s['확정률']}%")

    print("\n" + "=" * 74)
    print(" 리포트 A — 점주용 실행 카드")
    print("=" * 74)
    current = None
    for c in bundle.cards:
        if c.urgency != current:
            current = c.urgency
            print(f"\n  ▌{matrix.URGENCY_LABEL[current]}")
        cost = {1: "10만원 미만", 2: "10만~100만원", 3: "100만원 이상"}[c.cost_band]
        print(f"\n    [{c.rule_id}] {c.title[:52]}")
        print(f"      담당 {c.owner} · 처방 {c.remedy_type} · 난이도 {c.difficulty} · {cost}")
        if c.measured:
            k = list(c.measured)[0]
            print(f"      근거 {k}: {c.measured[k]}")

    print("\n\n" + "=" * 74)
    print(" 리포트 B — 미판정 항목과 다음 담당")
    print("=" * 74 + "\n")
    print(f"  {'항목':<6}{'사유':<12}{'다음 담당':<12}조치")
    print(f"  {'-'*68}")
    for u in bundle.undetermined:
        hint = " ".join(u["안내"].split())[:40]
        print(f"  {u['항목']:<6}{u['사유']:<12}{u['다음 담당']:<12}{hint}")

    print(f"\n  → 미판정 {len(bundle.undetermined)}건. 빈칸이 아니라 '누가 이어받는지'가 적힌다.")

    print("\n" + "=" * 74)
    print(" 판정 범위 고지")
    print("=" * 74 + "\n")
    for line in bundle.scope_note.split(". "):
        if line.strip():
            print(f"  {line.strip()}.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
