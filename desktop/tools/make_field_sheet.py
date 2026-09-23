#!/usr/bin/env python
"""현장 촬영 체크리스트 생성 — 수집 앱이 나오기 전까지 이걸로 쓴다.

    python tools/make_field_sheet.py            # assets/field-sheet.md
    python tools/make_field_sheet.py --html     # 인쇄용 HTML 도 함께

수집 앱(PWA)은 아직 없다. 하지만 촬영 프로토콜과 점주 문항은 이미 확정돼 있으므로,
종이 체크리스트만 있으면 오늘 당장 현장에 나갈 수 있다.

내용은 손으로 쓰지 않는다 — rules/capture-protocol.yaml 과 rules/annex5.yaml 에서
자동 생성한다. 프로토콜이 바뀌면 체크리스트도 따라 바뀐다.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir  # noqa: E402

ROOT = resource_dir()

from engine import rules as R  # noqa: E402


def build_markdown(rs: R.RuleSet, protocol: dict) -> str:
    m = protocol["marker"]
    L: list[str] = []
    A = L.append

    A("# 현장 촬영 체크리스트")
    A("")
    A(f"촬영 프로토콜 v{protocol['meta']['version']} · 룰 DB v{rs.meta['version']}")
    A("")
    A("| 기기 ID | 설치 장소 | 촬영일 | 촬영자 |")
    A("|---|---|---|---|")
    A("|  |  |  |  |")
    A("")

    # ── 촬영 전 ──────────────────────────────────────────────────────────
    A("## 촬영 전 확인")
    A("")
    A("- [ ] **마커 카드**를 가져왔다 — 실제 크기 100% 인쇄본, 20mm 눈금을 자로 확인함")
    A(f"- [ ] 마커는 {m['size_mm']['width']}×{m['size_mm']['height']}mm, "
      f"내부 정사각 {m['inner_square_mm']}mm ({m['print']['finish']})")
    A("- [ ] 촬영 기기가 **30fps 이상** 영상 촬영이 되는지 확인했다 (8.a 판정에 필요)")
    A("- [ ] 매장 촬영 **동의**를 받았다")
    A("- [ ] 화면 캡처는 **보정 없는 원본**으로 저장되도록 설정했다 (명도 대비 계산용)")
    A("")
    A("### 제품 구분 — 이걸 정해야 평가 항목 수가 정해진다")
    A("")
    A("- [ ] 중·대형 (화면 대각선 28cm 초과)   - [ ] 소형 (28cm 이하)")
    A("")
    A("### 이 기기에 있는 기능 — 해당하는 것만 체크 (조건부 항목)")
    A("")
    scopes = [s for s in rs.enums["scope"] if s != "기본"]
    for i in range(0, len(scopes), 2):
        pair = scopes[i:i + 2]
        A("- " + "   ".join(f"[ ] {s}" for s in pair))
    A("")
    A("---")
    A("")

    # ── 촬영 세트 ────────────────────────────────────────────────────────
    A("## 촬영 세트")
    A("")
    for s in protocol["sets"]:
        fed = rs.fed_by(s["id"])
        req = "필수" if s.get("required") else "선택"
        marker = " · **마커 필요**" if s.get("marker_plane") else ""
        A(f"### {s['id']} · {s['name']}  ({req}{marker})")
        A("")
        if fed:
            A(f"> 별표5 {len(fed)}개 항목이 이 세트에 달려 있다 — "
              f"{', '.join(r['id'] for r in fed[:8])}{' 외' if len(fed) > 8 else ''}")
            A("")
        if s.get("constraints"):
            for k, v in s["constraints"].items():
                A(f"- 조건: `{k}` = {v}")
            A("")
        for shot in s.get("shots", []):
            A(f"- [ ] **{shot['id']}** {shot['label']}")
            if shot.get("note"):
                A(f"      - {' '.join(shot['note'].split())}")
            if shot.get("required_for"):
                A(f"      - 이 컷이 없으면 판정 불가: {', '.join(shot['required_for'])}")
        A("")
        if s.get("note"):
            A(f"*{' '.join(s['note'].split())}*")
            A("")

    A("---")
    A("")

    # ── 마커 부착 위치 ───────────────────────────────────────────────────
    A("## 마커 부착 위치")
    A("")
    A("> 호모그래피는 **한 평면에만** 성립한다. 화면과 물리 버튼은 깊이가 다르므로")
    A("> 각 평면에 따로 붙이고 따로 찍는다. 다른 평면의 마커로 재면 깊이 차이가 그대로 오차가 된다.")
    A("")
    A("| 평면 | 붙이는 곳 | 이 평면으로 재는 항목 |")
    A("|---|---|---|")
    for p in m["usage"]["placements"]:
        A(f"| `{p['plane']}` | {p['where']} | {', '.join(p['used_by'])} |")
    A("")
    A("### 다시 찍어야 하는 경우")
    A("")
    for r in m["accuracy"]["reject_if"]:
        A(f"- {r}")
    A("")
    A("---")
    A("")

    # ── 점주 문항 ────────────────────────────────────────────────────────
    A("## 점주 확인 문항 — 카메라가 볼 수 없는 것")
    A("")
    A("> 직접 해보고 답한다. 미응답은 리포트에 '점주 확인 필요'로 남는다.")
    A("")
    for r in rs.by_track("B_OWNER"):
        q = " ".join((r.get("owner_question") or "").split())
        A(f"**{r['id']}**  {q}")
        A("")
        A("- [ ] 예    - [ ] 아니오    - [ ] 해당 없음")
        A("")

    A("---")
    A("")

    # ── 실측 의뢰 목록 ───────────────────────────────────────────────────
    A("## 전문 실측 의뢰 목록 — 촬영으로 안 되는 것")
    A("")
    A("> 이 항목들은 장비가 필요하다. 촬영 때 확인하지 말고 실측 업체에 넘긴다.")
    A("")
    A("| 항목 | 무엇을 재는가 | 장비 |")
    A("|---|---|---|")
    for r in rs.by_track("C_MEASURE"):
        crit = " ".join((r.get("criterion") or "").split())
        short = crit[:44] + ("…" if len(crit) > 44 else "")
        A(f"| {r['id']} | {short} | {r.get('instrument', '—')} |")
    A("")
    A("---")
    A("")

    # ── 개인정보 ────────────────────────────────────────────────────────
    A("## 개인정보 처리 — 촬영 직후 바로")
    A("")
    pv = protocol["privacy"]
    for t in pv["on_device_masking"]["targets"]:
        A(f"- [ ] {t['note']} → {t['kind']} {t['action']}")
    A(f"- [ ] {pv['on_device_masking']['timing']}")
    A(f"- [ ] 음성 녹음: {pv['audio']['note']}")
    A("")
    A("---")
    A("")
    A("## 제출 전 최종 확인")
    A("")
    for b in protocol["submission_gate"]["block_if"]:
        A(f"- [ ] {b} — 해당 없음 확인")
    A("")
    A(f"*{' '.join(protocol['submission_gate']['on_partial']['note'].split())}*")
    A("")
    return "\n".join(L)


HTML_CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: "Malgun Gothic","맑은 고딕",sans-serif; font-size: 10pt;
       line-height: 1.6; color: #111; max-width: 190mm; margin: 0 auto; }
h1 { font-size: 19pt; border-bottom: 3px solid #F2B705; padding-bottom: 6px; margin: 0 0 4px; }
h2 { font-size: 13pt; margin: 22px 0 8px; padding-left: 8px; border-left: 4px solid #17385C;
     page-break-after: avoid; }
h3 { font-size: 11pt; margin: 14px 0 6px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt; }
th, td { border: 1px solid #bbb; padding: 5px 7px; text-align: left; vertical-align: top; }
th { background: #eef2f6; }
blockquote { margin: 8px 0; padding: 7px 12px; background: #f6f8fa;
             border-left: 3px solid #8A97A4; font-size: 9pt; }
ul { padding-left: 18px; margin: 6px 0; }
li { margin: 3px 0; }
code { background: #eef2f6; padding: 1px 4px; border-radius: 2px; font-size: 9pt; }
hr { border: none; border-top: 1px solid #ddd; margin: 18px 0; }
strong { color: #0E2439; }
em { color: #5D6B79; font-style: normal; font-size: 9pt; }
"""


def to_html(md: str) -> str:
    """인쇄용 최소 변환 — 체크박스를 실제 네모로 바꾼다."""
    out: list[str] = []
    in_table = in_list = False

    for raw in md.split("\n"):
        ln = raw.rstrip()
        if in_table and not ln.startswith("|"):
            out.append("</table>"); in_table = False
        if in_list and not ln.lstrip().startswith("- "):
            out.append("</ul>"); in_list = False

        if not ln:
            continue
        if ln.startswith("# "):
            out.append(f"<h1>{html.escape(ln[2:])}</h1>")
        elif ln.startswith("## "):
            out.append(f"<h2>{html.escape(ln[3:])}</h2>")
        elif ln.startswith("### "):
            out.append(f"<h3>{html.escape(ln[4:])}</h3>")
        elif ln.startswith("---"):
            out.append("<hr>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{_inline(ln[2:])}</blockquote>")
        elif ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<table>"); in_table = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        elif ln.lstrip().startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(ln.lstrip()[2:])}</li>")
        elif ln.startswith("*") and ln.endswith("*"):
            out.append(f"<p><em>{_inline(ln.strip('*'))}</em></p>")
        else:
            out.append(f"<p>{_inline(ln)}</p>")

    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ul>")

    body = "\n".join(out)
    return (f"<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
            f"<title>현장 촬영 체크리스트</title><style>{HTML_CSS}</style></head>"
            f"<body>\n{body}\n</body></html>\n")


def _inline(t: str) -> str:
    t = html.escape(t)
    t = t.replace("[ ]", "☐")
    for a, b in (("**", "strong"), ("`", "code")):
        parts = t.split(a)
        t = "".join(p if i % 2 == 0 else f"<{b}>{p}</{b}>" for i, p in enumerate(parts))
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description="현장 촬영 체크리스트 생성")
    ap.add_argument("--out", default="assets")
    ap.add_argument("--html", action="store_true", help="인쇄용 HTML 도 생성")
    a = ap.parse_args()

    rs = R.load()
    protocol = R.load_protocol()
    md = build_markdown(rs, protocol)

    outdir = Path(a.out) if a.out != "assets" else output_dir()
    outdir.mkdir(parents=True, exist_ok=True)

    md_path = outdir / "field-sheet.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"  [OK] {md_path}   {len(md.splitlines())}줄")

    if a.html:
        h_path = outdir / "field-sheet.html"
        h_path.write_text(to_html(md), encoding="utf-8")
        print(f"  [OK] {h_path}   브라우저에서 열어 인쇄(Ctrl+P)")

    shots = sum(len(s.get("shots", [])) for s in protocol["sets"])
    print(f"\n  촬영 컷 {shots}개 · 점주 문항 {len(rs.by_track('B_OWNER'))}개 "
          f"· 실측 의뢰 {len(rs.by_track('C_MEASURE'))}개")
    print("  마커 카드(assets/BFK-MARK-A_A4_sheet.png)와 함께 인쇄해 가세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
