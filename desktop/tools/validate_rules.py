#!/usr/bin/env python
"""룰 DB 검증 + 커버리지 리포트.

    python tools/validate_rules.py            # 검증만
    python tools/validate_rules.py --coverage # 검증 + 트랙/촬영 세트 커버리지

CI 게이트로 쓴다. 룰 DB가 깨진 채로 엔진이 도는 일은 없어야 한다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir  # noqa: E402

from engine import rules as R  # noqa: E402

TRACK_LABEL = {
    "A_AI": "AI 자동 판정",
    "B_OWNER": "점주 셀프 체크",
    "C_MEASURE": "전문 실측",
    "D_USER": "사용자 검증",
}
SCOPE_LABEL = {
    "both": "양방향",
    "fail_only": "위반만",
    "none": "관여 안 함",
}


def bar(n: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return " " * width
    filled = round(n / total * width)
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser(description="별표5 룰 DB 검증")
    ap.add_argument("--rules", default=str(R.DEFAULT_RULES_PATH))
    ap.add_argument("--protocol", default=str(R.DEFAULT_PROTOCOL_PATH))
    ap.add_argument("--coverage", action="store_true", help="커버리지 리포트 출력")
    args = ap.parse_args()

    print("=" * 72)
    print(" 별표5 판정 룰 DB 검증")
    print("=" * 72)

    try:
        rs = R.load(args.rules, validate=False)
    except R.RuleError as exc:
        print(f"\n✗ 로드 실패: {exc}")
        return 2

    problems = R.check(rs)

    try:
        protocol = R.load_protocol(args.protocol)
        problems += R.cross_check(rs, protocol)
    except R.RuleError as exc:
        problems.append(f"촬영 프로토콜: {exc}")
        protocol = None

    print(f"\n  룰 DB      {Path(args.rules).name}  v{rs.meta.get('version')}")
    print(f"  항목 수     {len(rs)}  (meta.total_items={rs.meta.get('total_items')})")
    print(f"  등급 항목   {len(rs.graded())}  (meta.graded_items={rs.meta.get('graded_items')})")
    print(f"  마커 필요   {len(rs.requiring_marker())}")
    print(f"  면제 경로   {len(rs.exemptions)}")

    if problems:
        print(f"\n✗ 문제 {len(problems)}건\n")
        for p in problems:
            print(f"   - {p}")
        return 1

    print("\n✓ 스키마·정합성 검증 통과")

    if args.coverage:
        print_coverage(rs, protocol)

    return 0


def print_coverage(rs: R.RuleSet, protocol) -> None:
    total = len(rs)

    print("\n" + "=" * 72)
    print(" 트랙 분포 — 누가 판정하는가")
    print("=" * 72 + "\n")
    tracks = Counter(r["track"] for r in rs)
    for t in ("A_AI", "B_OWNER", "C_MEASURE", "D_USER"):
        n = tracks.get(t, 0)
        print(f"  {TRACK_LABEL[t]:<12} {n:>3}항목  {bar(n, total)}  {n/total*100:4.1f}%")

    print("\n" + "=" * 72)
    print(" AI 사정거리 — AI가 무엇까지 말할 수 있는가")
    print("=" * 72 + "\n")
    scopes = Counter(r["ai_verdict_scope"] for r in rs)
    for s in ("both", "fail_only", "none"):
        n = scopes.get(s, 0)
        print(f"  {SCOPE_LABEL[s]:<12} {n:>3}항목  {bar(n, total)}  {n/total*100:4.1f}%")
    both = scopes.get("both", 0)
    print(f"\n  → AI가 '적합'까지 말할 수 있는 항목은 {both}/{total}개({both/total*100:.0f}%)뿐이다.")
    print("     나머지는 위반을 잡아내거나, 사람에게 넘긴다.")

    print("\n" + "=" * 72)
    print(" AI 역할 — 어떻게 판정하는가")
    print("=" * 72 + "\n")
    roles = Counter(r["ai_role"] for r in rs)
    labels = {
        "measure": "L1 결정론적 계측",
        "judge": "L2 멀티모달 해석",
        "screen": "1차 스크리닝만",
        "none": "AI 미관여",
    }
    for k in ("measure", "judge", "screen", "none"):
        n = roles.get(k, 0)
        print(f"  {labels[k]:<18} {n:>3}항목  {bar(n, total)}")

    print("\n" + "=" * 72)
    print(" 영역별 항목 수")
    print("=" * 72 + "\n")
    for cat in rs.categories:
        items = cat["items"]
        tr = Counter(i["track"] for i in items)
        detail = " ".join(
            f"{TRACK_LABEL[k].split()[0]}{v}" for k, v in sorted(tr.items())
        )
        print(f"  {cat['area']:>2}. {cat['name']:<18} {len(items):>2}항목   {detail}")

    if protocol:
        print("\n" + "=" * 72)
        print(" 촬영 세트별 기여 항목 수")
        print("=" * 72 + "\n")
        for s in protocol["sets"]:
            fed = rs.fed_by(s["id"])
            req = "필수" if s.get("required") else "선택"
            marker = "마커" if s.get("marker_plane") else "  "
            print(f"  {s['id']}  {s['name']:<14} [{req}] {marker}  {len(fed):>2}항목 기여")

    print("\n" + "=" * 72)
    print(" 마커가 필요한 항목 — 마커 카드 한 장이 좌우한다")
    print("=" * 72 + "\n")
    for r in rs.requiring_marker():
        cat = rs.category_of(r["id"])
        print(f"  {r['id']:<5} {cat['name']:<18} {TRACK_LABEL[r['track']]}")

    print("\n" + "=" * 72)
    print(" 면제 경로")
    print("=" * 72 + "\n")
    for ex in rs.exemptions:
        print(f"  {ex['id']}")
        print(f"    근거   {ex['source']}")
        print(f"    면제   {', '.join(ex['exempts'])}  ({len(ex['exempts'])}항목)")
        print(f"    제출물 {ex['evidence_required']}")

    uv = rs.user_validation
    if uv:
        print("\n" + "=" * 72)
        print(" Track D — 사용자 검증 (이 엔진의 판정 대상 아님)")
        print("=" * 72 + "\n")
        for c in uv["composition"]:
            print(f"  {c['type']:<26} {c['min_count']}명")
        print(f"  {'합계':<26} {uv['total_min_count']}명")
        print(f"\n  AI 대체 가능: {uv.get('ai_replaceable')}")

    print()


if __name__ == "__main__":
    raise SystemExit(main())
