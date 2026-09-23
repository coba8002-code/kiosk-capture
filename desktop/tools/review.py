#!/usr/bin/env python
"""검토자 콘솔 — '위반 의심'을 '부적합'으로 확정하는 유일한 경로.

    KFA.exe review <번들폴더>
    KFA.exe review <번들폴더> --list        결정하지 않고 목록만
    KFA.exe review <번들폴더> --measure     실측값만 입력
    KFA.exe review <번들폴더> --exempt      면제 경로만 지정

왜 필요한가
────────────────────────────────────────────────────────────────────
    설계상 AI 는 '부적합'을 확정하지 못한다. 최대치가 '위반 의심'이다.
    그런데 사람의 승인을 되먹이는 경로가 없으면, 그 판정은 **영원히 의심에 머문다.**
    리포트가 결론을 내지 못하는 셈이다.

    이 도구가 그 마지막 한 칸을 채운다. 결정은 번들 옆 review.json 에 남으므로
    같은 번들을 다시 진단해도 같은 결과가 나온다 — 재현 가능하고, 감사에도 대응된다.

무엇을 결정하는가
    ① 위반 의심 → 승인(부적합) / 반려(미판정)
    ② 실측값 입력 — Track C 7항목
    ③ 면제 경로 — 무인민원발급기 표준규격 적합 시 6항목 제외
    ④ 우수/보통 등급 — 별표5 8항목
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import pipeline, rules as R          # noqa: E402
from engine.verdict import Grade, Verdict        # noqa: E402

LINE = "-" * 74


def ask(prompt: str, valid: set[str] | None = None, default: str = "") -> str:
    while True:
        try:
            v = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "q"
        if not v and default:
            return default
        if valid is None or v.lower() in valid:
            return v.lower() if valid else v
        print(f"      {'/'.join(sorted(valid))} 중에서 입력하세요.")


def show_evidence(a) -> None:
    ev = a.evidence
    if not ev:
        return
    if ev.confidence is not None:
        print(f"      확신도   {ev.confidence:.2f}")
    if ev.crop_ref:
        print(f"      근거     {ev.crop_ref}")
    for k, v in list(ev.measured.items())[:8]:
        if isinstance(v, (list, dict)):
            v = str(v)[:56]
        print(f"      {k:<14}{v}")


def review_suspected(rs, bundle, run) -> dict:
    """① 위반 의심 승인/반려."""
    todo = pipeline.pending_review(rs, run.assessments)
    decisions = dict(bundle.review.get("decisions") or {})
    if not todo:
        print("\n  승인 대기 중인 '위반 의심'이 없습니다.")
        return decisions

    print(f"\n  위반 의심 {len(todo)}항목 — 확신도 낮은 순\n" + LINE)
    for i, a in enumerate(todo, 1):
        rule = rs[a.rule_id]
        cat = rs.category_of(a.rule_id)
        crit = " ".join((rule.get("criterion") or "").split())
        print(f"\n  [{i}/{len(todo)}]  {a.rule_id}   {cat['name']}")
        print(f"      {crit[:150]}{'…' if len(crit) > 150 else ''}")
        show_evidence(a)
        prev = decisions.get(a.rule_id)
        if prev:
            print(f"      (이전 결정: {prev})")

        v = ask("\n      [a]승인=부적합  [r]반려=미판정  [s]건너뛰기  [q]종료 > ",
                {"a", "r", "s", "q"}, default="s")
        if v == "q":
            break
        if v == "a":
            decisions[a.rule_id] = "approve"
            print("      → 부적합으로 확정합니다.")
        elif v == "r":
            decisions[a.rule_id] = "reject"
            print("      → 미판정으로 되돌립니다. (반려해도 적합이 되지는 않습니다)")
    return decisions


def input_measurements(rs, bundle) -> dict:
    """② 실측값 입력 — Track C."""
    ms = dict(bundle.review.get("measurements") or {})
    items = rs.by_track("C_MEASURE")
    print(f"\n  전문 실측 {len(items)}항목\n" + LINE)
    print("  값을 비워두면 건너뜁니다. 측정 조건은 measurement-sop.yaml 을 따르세요.\n")

    by = ask("      측정 기관/담당 (엔터=건너뛰기) > ", None, default="")
    if not by:
        return ms

    for r in items:
        rid = r["id"]
        crit = " ".join((r.get("criterion") or "").split())
        print(f"\n  {rid}  {crit[:120]}{'…' if len(crit) > 120 else ''}")
        print(f"      장비: {r.get('instrument', '—')}")
        if rid in ms:
            print(f"      (이전 입력: {ms[rid].get('verdict')})")
        v = ask("      [p]적합  [f]부적합  [n]해당없음  [s]건너뛰기 > ",
                {"p", "f", "n", "s", "q"}, default="s")
        if v == "q":
            break
        if v == "s":
            continue
        rec = {"verdict": {"p": "pass", "f": "fail", "n": "na"}[v], "by": by}
        val = ask("      측정값 (예: 1285mm, 18.4N — 엔터=생략) > ", None, default="")
        if val:
            rec["측정값"] = val
        cond = ask("      측정 조건 (엔터=SOP 기본) > ", None, default="measurement-sop.yaml 기준")
        rec["측정 조건"] = cond
        ms[rid] = rec
    return ms


def pick_exemptions(rs, bundle) -> list[str]:
    """③ 면제 경로."""
    cur = set(bundle.review.get("exemptions") or [])
    print("\n  면제 경로\n" + LINE)
    for ex in rs.exemptions:
        on = ex["id"] in cur
        print(f"\n  {ex['id']}{'  (지정됨)' if on else ''}")
        print(f"      근거   {ex['source']}")
        print(f"      면제   {', '.join(ex['exempts'])}  ({len(ex['exempts'])}항목)")
        print(f"      제출물 {ex['evidence_required']}")
        v = ask("      적용합니까? [y]예 [n]아니오 > ", {"y", "n", "q"},
                default="y" if on else "n")
        if v == "q":
            break
        if v == "y":
            cur.add(ex["id"])
        else:
            cur.discard(ex["id"])
    return sorted(cur)


def set_grades(rs, bundle, run) -> dict:
    """④ 우수/보통 등급 — 적합으로 확정된 항목만."""
    grades = dict(bundle.review.get("grades") or {})
    passed = {a.rule_id for a in run.assessments if a.verdict is Verdict.PASS}
    targets = [r for r in rs.graded() if r["id"] in passed]
    if not targets:
        print("\n  등급을 매길 수 있는 항목이 없습니다. (적합으로 확정된 등급 항목이 없음)")
        return grades

    print(f"\n  우수/보통 등급 {len(targets)}항목\n" + LINE)
    for r in targets:
        g = (r.get("verdict") or {}).get("grade") or {}
        print(f"\n  {r['id']}")
        print(f"      우수: {' '.join(str(g.get('우수', '')).split())[:110]}")
        print(f"      보통: {' '.join(str(g.get('보통', '')).split())[:110]}")
        v = ask("      [e]우수  [n]보통  [s]건너뛰기 > ", {"e", "n", "s", "q"}, default="s")
        if v == "q":
            break
        if v == "e":
            grades[r["id"]] = "우수"
        elif v == "n":
            grades[r["id"]] = "보통"
    return grades


def main() -> int:
    ap = argparse.ArgumentParser(description="검토자 콘솔")
    ap.add_argument("bundle", help="수집 번들 폴더")
    ap.add_argument("--list", action="store_true", help="결정하지 않고 대기 목록만")
    ap.add_argument("--measure", action="store_true", help="실측값 입력만")
    ap.add_argument("--exempt", action="store_true", help="면제 경로만")
    ap.add_argument("--grade", action="store_true", help="등급 부여만")
    ap.add_argument("--reviewer", default="", help="검토자 이름")
    a = ap.parse_args()

    try:
        bundle = pipeline.load_bundle(a.bundle)
    except pipeline.BundleError as exc:
        print(f"  [실패] {exc}")
        return 2

    rs = R.load()
    print("=" * 74)
    print(" 검토자 콘솔")
    print("=" * 74)
    print(f"\n  기기   {bundle.device_id}  {bundle.location}")
    print(f"  번들   {bundle.root}")

    print("\n  분석 중…")
    run = pipeline.analyze(bundle, rs)

    if a.list:
        todo = pipeline.pending_review(rs, run.assessments)
        print(f"\n  승인 대기 {len(todo)}항목\n" + LINE)
        for x in todo:
            conf = x.evidence.confidence if x.evidence else None
            print(f"   {x.rule_id:<7}{x.verdict.value:<12}"
                  f"확신도 {conf:.2f}" if conf is not None else f"   {x.rule_id}")
        return 0

    reviewer = a.reviewer or ask("\n  검토자 이름 > ", None, default="검토자")
    if reviewer == "q":
        return 0

    review = dict(bundle.review)
    review["reviewer"] = reviewer

    only = a.measure or a.exempt or a.grade
    if not only or a.exempt:
        review["exemptions"] = pick_exemptions(rs, bundle)
    if not only:
        review["decisions"] = review_suspected(rs, bundle, run)
    if not only or a.measure:
        review["measurements"] = input_measurements(rs, bundle)
    if not only or a.grade:
        # 등급은 결정 반영 후의 상태에서 매겨야 정확하다
        bundle.review = review
        run2 = pipeline.analyze(bundle, rs)
        review["grades"] = set_grades(rs, bundle, run2)

    bundle.review = review
    path = bundle.save_review()

    # 반영 결과 요약
    bundle2 = pipeline.load_bundle(a.bundle)
    run3 = pipeline.analyze(bundle2, rs)
    from engine.report import matrix
    rep = matrix.build(rs, run3.assessments, device_id=bundle2.device_id,
                       product_type=bundle2.product_type,
                       available_scopes=bundle2.scopes,
                       exemption_ids=bundle2.exemptions)
    s = rep.summary

    print("\n" + "=" * 74)
    print(" 반영 결과")
    print("=" * 74)
    print(f"\n  적합 {s['적합']}   부적합 {s['부적합']}   위반 의심 {s['위반 의심']}   "
          f"미판정 {s['미판정']}   면제 {s['면제']}   해당 없음 {s['해당 없음']}")
    print(f"  확정률 {s['확정률']}%")
    print(f"\n  결정 저장  {path}")
    print(f"\n  리포트를 다시 만들려면:\n    KFA.exe ingest \"{bundle.root}\"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
