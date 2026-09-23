#!/usr/bin/env python
"""수집 번들 진단 — 진단 PC 가 실제로 하는 일.

    KFA.exe ingest <번들폴더>
    KFA.exe ingest <번들폴더> --markdown report.md
    KFA.exe ingest <번들폴더> --csv
    KFA.exe ingest <번들폴더> --l2 anthropic --yes

수집 앱이 만든 폴더를 통째로 넘기면 판정과 리포트가 나온다.
사진마다 마커를 찾아 평면을 환산하고, 계측하고, 판정을 낸 뒤,
자동으로 판정하지 못한 것은 담당자를 지정해 남긴다.
"""

from __future__ import annotations

import argparse
import re
import tempfile
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import pipeline, rules as R                       # noqa: E402
from engine.paths import output_dir                           # noqa: E402
from engine.report import export as rexport, matrix, render   # noqa: E402


def _setup_l2(name, bundle, *, confirmed: bool):
    """L2 공급자를 준비한다. 외부 전송이면 무엇이 나가는지 먼저 알린다.

    사진을 밖으로 보내는 것은 되돌릴 수 없다. 현장 사진에는 개인정보가 남아 있을 수
    있고, 앱의 기기 내 마스킹이 완전하다고 가정하지 않는다. 그래서 --l2 만으로는
    부족하고, 무엇이 전송되는지 화면에 찍은 뒤 동의를 받는다.
    """
    from engine import l2 as L2

    try:
        provider = L2.resolve(name)
    except ValueError as exc:
        print(f"\n  [실패] {exc}")
        return None

    ok, why = provider.available()
    print("\n" + "-" * 74)
    print(f" L2 공급자  {provider.name}")
    print("-" * 74)
    print(f"  상태  {'사용 가능' if ok else '사용 불가'} — {why}")
    if not ok:
        print("\n  L2 항목 25개는 검토자에게 넘어갑니다. 진단은 계속됩니다.")
        return provider          # judge.run 이 available() 을 보고 비켜난다

    if provider.sends_media_externally:
        print("\n  [주의] 이 공급자는 촬영물을 외부 API 로 전송합니다.")
        print(f"         전송 대상  촬영물 {len(bundle.shots)}건 ({bundle.device_id})")
        print(f"         기기 위치  {bundle.location or '미기재'}")
        print("         현장 사진에 개인정보가 남아 있을 수 있습니다.")
        print("         앱의 기기 내 마스킹이 완전하다고 가정하지 마십시오.")
        if not confirmed:
            if not sys.stdin.isatty():
                print("\n  [중단] 확인이 필요합니다. 동의하면 --yes 를 붙여 다시 실행하세요.")
                return None
            if input("\n  전송에 동의하십니까? (y/N) ").strip().lower() not in ("y", "yes"):
                print("  중단했습니다.")
                return None
    return provider


def main() -> int:
    ap = argparse.ArgumentParser(description="수집 번들을 진단한다")
    ap.add_argument("bundle", help="수집 번들 폴더 (manifest.json 이 있는 곳)")
    ap.add_argument("--markdown", metavar="FILE", help="리포트 B 를 마크다운으로 저장")
    ap.add_argument("--csv", action="store_true", help="매트릭스·미판정 CSV 저장")
    ap.add_argument("--json", metavar="FILE", help="전체 구조화 데이터 저장")
    ap.add_argument("--quiet", action="store_true", help="사진별 진행 표시 생략")
    ap.add_argument("--l2", nargs="?", const="", metavar="공급자",
                    help="촬영물을 보고 판단·선별하는 25항목까지 돌린다 (기본: 꺼짐)")
    ap.add_argument("--yes", action="store_true",
                    help="외부 전송 확인을 묻지 않고 진행한다")
    a = ap.parse_args()

    print("=" * 74)
    print(" 수집 번들 진단")
    print("=" * 74)

    # ── 번들 읽기 ────────────────────────────────────────────────────────
    try:
        bundle = pipeline.load_bundle(a.bundle)
    except pipeline.BundleError as exc:
        print(f"\n  [실패] {exc}")
        print("\n  번들은 manifest.json 과 촬영 파일로 이뤄집니다.")
        print("  형식은 docs/capture-bundle.md 를 보세요.")
        return 2

    rs = R.load()

    # ── L2 공급자 — 켠 경우에만, 그리고 무엇이 나가는지 알린 뒤에만 ────────
    provider = None
    if a.l2 is not None:
        provider = _setup_l2(a.l2 or None, bundle, confirmed=a.yes)
        if provider is None:
            return 3
    print(f"\n  기기        {bundle.device_id}  {bundle.location}")
    print(f"  제품 구분   {bundle.product_type}")
    print(f"  조건부 기능 {', '.join(sorted(bundle.scopes)) or '없음'}")
    print(f"  촬영        {bundle.captured_at}  {bundle.captured_by}")
    print(f"  파일        {len(bundle.shots)}개")
    if bundle.owner_answers:
        print(f"  점주 응답   {len(bundle.owner_answers)}개")
    if bundle.measurements:
        print(f"  실측값      {len(bundle.measurements)}개")

    # ── 분석 ─────────────────────────────────────────────────────────────
    print("\n" + "-" * 74)
    print(" 사진 분석")
    print("-" * 74)
    run = pipeline.analyze(bundle, rs, progress=None if a.quiet else print,
                           l2_provider=provider)

    expected, ok = run.marker_expected(), run.marker_ok()
    if expected:
        mark = "[ OK ]" if ok == expected else "[주의]"
        print(f"\n  {mark} 마커 환산  {ok}/{expected} 성공")
        for p in run.photos:
            if p.shot.marker_plane and p.scale is None:
                print(f"         · {p.shot.path.name} — {p.marker_note}")

    blocks = sum(len(p.contrast_samples) for p in run.photos)
    print(f"  문구 블록  {blocks}개 검출 (S1 화면)")
    for p in run.photos:
        if p.text_note:
            print(f"  [주의] {p.shot.path.name} — {p.text_note}")
    if run.ocr:
        print(f"  OCR 엔진   {run.ocr}")

    # ── 리포트 ───────────────────────────────────────────────────────────
    report = matrix.build(
        rs, run.assessments,
        device_id=f"{bundle.device_id} ({bundle.location})" if bundle.location else bundle.device_id,
        product_type=bundle.product_type,
        available_scopes=bundle.scopes,
        exemption_ids=bundle.exemptions,
        l2_provider=(run.l2.provider if run.l2 is not None and run.l2.ran else None),
    )
    s = report.summary

    print("\n" + "=" * 74)
    print(" 판정 결과")
    print("=" * 74)
    print(f"\n  진단 대상  {s['이번 진단 대상']}항목  (별표5 전체 {s['별표5 전체 항목']}항목)")
    print(f"  적합 {s['적합']}   부적합 {s['부적합']}   위반 의심 {s['위반 의심']}   "
          f"미판정 {s['미판정']}   면제 {s['면제']}   해당 없음 {s['해당 없음']}")

    auto = [x for x in run.assessments if x.source == "AI"]
    ext = [x for x in run.assessments if x.source != "AI"]
    print(f"\n  자동 판정  {len(auto)}항목   입력 반영  {len(ext)}항목")

    if auto:
        print("\n  자동 판정 내역")
        print(f"    {'항목':<7}{'판정':<12}{'확신도':<9}근거")
        print("    " + "-" * 62)
        for x in auto:
            conf = x.evidence.confidence if x.evidence else None
            key = next(iter(x.evidence.measured), "") if x.evidence and x.evidence.measured else ""
            val = x.evidence.measured.get(key, "") if x.evidence and x.evidence.measured else ""
            print(f"    {x.rule_id:<7}{x.verdict.value:<12}"
                  f"{(f'{conf:.2f}' if conf is not None else '-'):<9}{key} {val}")

    if run.l2 is not None:
        r = run.l2
        print(f"\n  L2 판정    공급자 {r.provider} · 판정 {r.judged}항목 · "
              f"모델 판단불가 {r.declined}항목 · 호출 실패 {len(r.failed)}항목")
        print("             모델의 '적합'은 적합으로 인정하지 않습니다 — 전부 검토자로 갑니다.")

    if run.skipped:
        print("\n  자동 판정하지 못한 이유")
        for msg in run.skipped:
            print(f"    · {msg}")

    if report.cautions:
        print(f"\n  사용성 주의 {len(report.cautions)}항목 (별표5 판정 아님 — 참고)")
        for c in report.cautions:
            print(f"    {c.rule_id:<6}{c.label:<10}{c.measured}mm — {c.band}")

    print("\n  미판정 항목의 다음 담당")
    by_owner: dict[str, int] = {}
    for u in report.undetermined:
        by_owner[u["다음 담당"]] = by_owner.get(u["다음 담당"], 0) + 1
    for owner, n in sorted(by_owner.items(), key=lambda kv: -kv[1]):
        print(f"    {owner:<14}{n}항목")

    # ── 저장 ─────────────────────────────────────────────────────────────
    safe_id = re.sub(r'[^\w.-]', '_', str(bundle.device_id))[:60].strip('. ') or 'capture'
    outdir = Path(tempfile.mkdtemp(prefix=f"report_{safe_id}_", dir=output_dir()))
    saved: list[Path] = []

    md_path = Path(a.markdown) if a.markdown else outdir / "report.md"
    md_path.write_text(matrix.to_markdown(report), encoding="utf-8")
    saved.append(md_path)

    if a.csv:
        (outdir / "matrix.csv").write_text(rexport.to_csv(report), encoding="utf-8")
        (outdir / "todo.csv").write_text(rexport.undetermined_csv(report), encoding="utf-8")
        saved += [outdir / "matrix.csv", outdir / "todo.csv"]

    json_path = Path(a.json) if a.json else outdir / "result.json"
    json_path.write_text(rexport.to_json(report), encoding="utf-8")
    saved.append(json_path)

    # 인쇄본 — 브라우저에서 열어 Ctrl+P 로 PDF 를 만든다
    a_path = outdir / "리포트A_점주용.html"
    b_path = outdir / "리포트B_담당자용.html"
    from engine.report.coverage import render as render_coverage
    coverage = render_coverage(run)
    a_path.write_text(render.render_owner(report).replace('</body>', coverage+'</body>'), encoding="utf-8")
    b_path.write_text(render.render_authority(report).replace('</body>', coverage+'</body>'), encoding="utf-8")
    saved += [a_path, b_path]

    print("\n" + "=" * 74)
    print(" 저장")
    print("=" * 74 + "\n")
    for p in saved:
        print(f"  {p}")
    print("\n  HTML 두 개는 브라우저에서 열어 Ctrl+P 로 PDF 저장하세요.")

    print("\n  " + " ".join(report.scope_note.split())[:150] + " …")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
