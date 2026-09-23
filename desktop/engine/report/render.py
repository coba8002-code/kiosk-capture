"""리포트 인쇄본 — 브라우저에서 열어 Ctrl+P 로 PDF 를 만든다.

    A · 점주용 한 장 (A4)      실행 카드 3단. 점수는 맨 아래.
    B · 발주·지자체용 (A4 다중) 별표5 40항목 매트릭스 + 미판정 표

왜 HTML 인가
    PDF 라이브러리를 붙이면 폰트 문제로 한글이 깨지거나, 의존성이 하나 더 는다.
    브라우저는 이미 모든 PC 에 있고 한글 조판이 정확하며 Ctrl+P 로 PDF 가 나온다.
    인쇄 여백·페이지 나눔은 @page 와 break-inside 로 제어한다.

Figma 설계(R1·R2)와 같은 토큰을 쓴다. 색이나 간격을 바꿀 때는 양쪽을 함께 고친다.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from .matrix import URGENCY_LABEL, ReportBundle

# 점주용 리포트는 60~80대가 읽는다. 합동 평가에서 10px 글씨가 34곳 나왔고
# 80대 참여자는 한 문장을 소리 내어 읽지 못했다. 그래서 하한을 올렸다.
#   7.5pt(≈10px) → 9pt   ·   8·8.5pt → 9.5pt   ·   본문 10pt → 10.5pt
# 주의색 #B26A00 은 흰 배경 4.24:1, 주의 박스 3.79:1 로 **우리가 키오스크에
# 요구하는 4.5:1 을 우리가 못 지키고 있었다.** #8C5200 으로 내려 6.32 / 5.66.
_CSS = """
@page { size: A4; margin: 14mm 13mm; }
* { box-sizing: border-box; }
body {
  margin: 0; font-family: "Malgun Gothic","맑은 고딕",-apple-system,
    "Apple SD Gothic Neo","Noto Sans KR",sans-serif;
  font-size: 10.5pt; line-height: 1.66; color: #131A22; word-break: keep-all;
  background: #fff;   /* 투명하면 뷰어 테마에 따라 글자가 안 보인다 */
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.page { max-width: 184mm; margin: 0 auto; }
.page + .page { page-break-before: always; }

.head { background: #0E2439; color: #fff; padding: 16px 20px 15px;
  border-bottom: 4px solid #F2B705; margin-bottom: 0; }
.eyebrow { font-size: 9.5pt; letter-spacing: .13em; color: #F2B705;
  text-transform: uppercase; margin: 0 0 5px; display: flex;
  justify-content: space-between; gap: 12px; }
.head h1 { font-size: 17pt; margin: 0 0 6px; font-weight: 700; letter-spacing: -.01em; }
.head .meta { font-size: 9.5pt; color: #9FB0C2; display: flex; gap: 14px; flex-wrap: wrap; }

.strip { display: flex; background: #F6F8FA; border-bottom: 1px solid #D7DFE7;
  padding: 12px 20px; gap: 4px; }
.stat { flex: 1; }
.stat b { display: block; font-size: 17pt; font-weight: 600; line-height: 1.1; }
.stat span { font-size: 9.5pt; color: #5D6B79; }
.pass b { color: #1E7A4D; } .fail b { color: #B3261E; }
.susp b { color: #8C5200; } .undet b { color: #5D6B79; } .tot b { color: #17385C; }

.body { padding: 14px 20px 16px; }
.lede { background: #fff; border-left: 4px solid #F2B705; padding: 9px 13px;
  font-size: 10pt; color: #33404E; margin: 0 0 13px; }

h2 { font-size: 11pt; margin: 15px 0 7px; display: flex; align-items: center;
  gap: 8px; page-break-after: avoid; }
h2 i { display: block; width: 4px; height: 15px; border-radius: 2px; background: #17385C; }
h2 small { font-weight: 400; font-size: 9.5pt; color: #5D6B79; margin-left: auto; }

.card { border: 1px solid #D7DFE7; border-radius: 7px; padding: 9px 12px;
  margin-bottom: 6px; display: flex; gap: 11px; align-items: flex-start;
  page-break-inside: avoid; }
.chk { width: 13px; height: 13px; border: 1.5px solid #D7DFE7; border-radius: 3px;
  flex: none; margin-top: 2px; }
.card .mid { flex: 1; min-width: 0; }
.card .t1 { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.card .t2 { font-size: 9.5pt; color: #5D6B79; margin-top: 3px; }
.card .t2.rel { color: #24705A; }
.note.gray { background: #F6F8FA; color: #33404E; }
.card .own { text-align: right; flex: none; }
.card .own small { display: block; font-size: 9pt; color: #5D6B79; margin-top: 3px; }

.tag { font-size: 9pt; padding: 2px 6px; border-radius: 4px; font-weight: 700;
  background: #F6F8FA; color: #5D6B79; white-space: nowrap; }
.tag.id { background: #0E2439; color: #fff; }
.tag.sw { background: #E7EFF9; color: #1B579B; }
.tag.hw { background: #F7E9E2; color: #99451F; }
.tag.ops { background: #E3F0EB; color: #24705A; }
.tag.ok { background: #E3F2EA; color: #1E7A4D; }
.tag.no { background: #FBE9E7; color: #B3261E; }
.tag.go { background: #FDF1DC; color: #8C5200; }
.tag.gy { background: #ECF0F4; color: #5D6B79; }

table { width: 100%; border-collapse: collapse; font-size: 9.5pt; margin: 6px 0 12px; }
th { text-align: left; background: #F6F8FA; color: #5D6B79; font-size: 9pt;
  letter-spacing: .06em; padding: 6px 8px; border-bottom: 1px solid #D7DFE7;
  text-transform: uppercase; font-weight: 600; }
td { padding: 5px 8px; border-bottom: 1px solid #E6ECF2; vertical-align: top; }
tr { page-break-inside: avoid; }
td.n { font-variant-numeric: tabular-nums; white-space: nowrap; }

.grid { display: flex; flex-wrap: wrap; gap: 4px; }
.cell { font-size: 9pt; padding: 3px 6px; border-radius: 4px; font-weight: 700;
  display: inline-flex; align-items: center; gap: 4px; }
.cell i { width: 5px; height: 5px; border-radius: 50%; display: block; }
.arearow { display: flex; align-items: flex-start; gap: 10px; padding: 5px 0;
  border-bottom: 1px solid #E6ECF2; page-break-inside: avoid; }
.arearow .nm { width: 108px; flex: none; font-size: 10pt; color: #33404E; }

.note { background: #FDF1DC; color: #8C5200; border-radius: 6px; padding: 9px 12px;
  font-size: 9.5pt; line-height: 1.6; margin: 10px 0; page-break-inside: avoid; }
.foot { border-top: 1px solid #D7DFE7; margin-top: 14px; padding-top: 9px;
  font-size: 9pt; color: #5D6B79; line-height: 1.6; }
"""

_TONE = {
    "적합": ("#1E7A4D", "#E3F2EA"),
    "부적합": ("#B3261E", "#FBE9E7"),
    "위반 의심": ("#8C5200", "#FDF1DC"),
    "미판정": ("#5D6B79", "#ECF0F4"),
    "면제": ("#1B579B", "#E7EFF9"),
    "해당 없음": ("#8A97A4", "#F6F8FA"),
}
_REMEDY_CLASS = {"SW": "sw", "HW": "hw", "OPS": "ops", "NONE": "gy"}
_COST = {1: "10만원 미만", 2: "10만~100만원", 3: "100만원 이상"}


# 근거로 보여주기에 부적절한 메타 필드 — 누가 쟀는지는 근거가 아니다
_META_KEYS = {"by", "질문", "측정 조건", "계산식", "커버리지", "주의", "기준", "출처"}


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _evidence_line(measured: dict | None) -> str:
    """측정값에서 사람이 볼 만한 한 줄을 고른다."""
    if not measured:
        return ""
    for k, v in measured.items():
        if k in _META_KEYS or isinstance(v, (list, dict)):
            continue
        return f"{k}: {v}"
    k = next(iter(measured))
    return f"{k}: {measured[k]}"


def _head(bundle: ReportBundle, kind: str, date: str) -> str:
    s = bundle.summary
    return f"""
<div class="head">
  <div class="eyebrow"><span>{_e(kind)}</span><span>{_e(date)}</span></div>
  <h1>{_e(bundle.device_id)}</h1>
  <div class="meta">
    <span>제품 구분 {_e(bundle.product_type)}</span>
    <span>진단 대상 {s['이번 진단 대상']}항목 / 별표5 {s['별표5 전체 항목']}항목</span>
  </div>
</div>
<div class="strip">
  <div class="stat pass"><b>{s['적합']}</b><span>적합</span></div>
  <div class="stat fail"><b>{s['부적합']}</b><span>부적합</span></div>
  <div class="stat susp"><b>{s['위반 의심']}</b><span>위반 의심</span></div>
  <div class="stat undet"><b>{s['미판정']}</b><span>미판정</span></div>
  <div class="stat tot"><b>{s['확정률']}%</b><span>확정률</span></div>
</div>"""


def render_owner(bundle: ReportBundle, *, date: str = "") -> str:
    """리포트 A — 점주용 한 장. 점수가 아니라 순서를 준다."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    parts = [_head(bundle, "접근성 진단 리포트 · 점주용", date), '<div class="body">']
    parts.append('<p class="lede">점수가 아니라 <b>순서</b>입니다. 위에서부터 처리하세요. '
                 '각 줄에 누가 해야 하는 일인지 적혀 있습니다.</p>')

    tiers = [("now", "#B3261E", "지금 바로, 비용 거의 없이 고칠 수 있습니다"),
             ("quarter", "#8C5200", "부품 수급이나 개발 일정이 필요합니다"),
             ("review", "#5D6B79", "지금 기기로는 비용이 큽니다. 다음 발주 때 반영하세요")]
    for urgency, color, desc in tiers:
        cards = [c for c in bundle.cards if c.urgency == urgency]
        if not cards:
            continue
        parts.append(f'<h2><i style="background:{color}"></i>{_e(URGENCY_LABEL[urgency])}'
                     f'<small>{_e(desc)} · {len(cards)}건</small></h2>')
        for c in cards:
            cls = _REMEDY_CLASS.get(c.remedy_type, "gy")
            label = {"SW": "SW 패치", "HW": "HW 보완", "OPS": "운영 개선"}.get(c.remedy_type, c.remedy_type)
            fix = _evidence_line(c.measured)
            parts.append(f"""
<div class="card">
  <div class="chk"></div>
  <div class="mid">
    <div class="t1"><span class="tag id">{_e(c.rule_id)}</span>
      <span style="font-size:9.5pt;font-weight:600">{_e(c.title[:78])}</span></div>
    <div class="t2">{_e(fix)}</div>
    {f'<div class="t2 rel">{_e(c.related)}</div>' if c.related else ''}
  </div>
  <div class="own"><span class="tag {cls}">{_e(label)}</span>
    <small>{_e(c.owner)}<br>{_e(_COST.get(c.cost_band, ''))}</small></div>
</div>""")

    if bundle.undetermined:
        by: dict[str, int] = {}
        for u in bundle.undetermined:
            by[u["다음 담당"]] = by.get(u["다음 담당"], 0) + 1
        rows = " · ".join(f"{k} {v}항목" for k, v in sorted(by.items(), key=lambda kv: -kv[1]))
        parts.append(f'<div class="note"><b>아직 판정하지 못한 {len(bundle.undetermined)}항목</b> — '
                     f'{_e(rows)}. 아래 항목은 추가 촬영·실측·확인이 필요합니다.</div>')
        parts.append('<table><tr><th>항목</th><th>무엇을 확인하나요</th><th>다음에 할 일</th></tr>')
        for u in bundle.undetermined:
            parts.append(f'<tr><td class="n"><b>{_e(u["항목"])}</b></td>'
                         f'<td>{_e(u["항목명"])}</td>'
                         f'<td>{_e(" ".join(u["안내"].split())[:110])}</td></tr>')
        parts.append('</table>')

    if bundle.cautions:
        from .. import usability
        parts.append('<h2><i style="background:#5D6B79"></i>참고 — 기준은 통과했지만 '
                     '쓰기 어려울 수 있는 것</h2>')
        parts.append(f'<div class="note gray">{_e(usability.NOTE)}</div>')
        for c in bundle.cautions:
            parts.append(f"""<div class="card">
  <div class="main">
    <div class="t1"><span class="tag gy">{_e(c.rule_id)}</span>
      <span style="font-size:9.5pt;font-weight:600">{_e(c.label)} {c.measured}mm</span></div>
    <div class="t2">{_e(c.why)}</div>
    <div class="t2 rel">{_e(c.advice)}</div>
  </div>
  <div class="own"><span class="tag gy">참고</span><small>{_e(c.band)}</small></div>
</div>""")

    parts.append(f'<div class="foot">{_e(bundle.scope_note)}</div></div>')
    return _wrap("접근성 진단 리포트 (점주용)", "".join(parts))


def render_authority(bundle: ReportBundle, *, date: str = "") -> str:
    """리포트 B — 발주·지자체용. 미판정을 빈칸이 아니라 명시 항목으로 인쇄한다."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    parts = [_head(bundle, "별표5 항목별 판정 결과", date), '<div class="body">']

    parts.append(f'<div class="note"><b>판정 범위 고지</b><br>{_e(bundle.scope_note)}</div>')

    # 영역별 집계
    parts.append('<h2><i></i>별표5 10개 영역 집계</h2>')
    for area, counts in bundle.summary["영역별"].items():
        cells = []
        for k in ("적합", "부적합", "위반 의심", "미판정", "면제", "해당 없음"):
            n = counts.get(k, 0)
            if not n:
                continue
            fg, bg = _TONE[k]
            cells.append(f'<span class="cell" style="background:{bg};color:{fg}">'
                         f'<i style="background:{fg}"></i>{_e(k)} {n}</span>')
        parts.append(f'<div class="arearow"><div class="nm">{_e(area)}</div>'
                     f'<div class="grid">{"".join(cells)}</div></div>')

    # 미판정 표 — 이 리포트의 신뢰 장치
    if bundle.undetermined:
        parts.append(f'<h2><i style="background:#8C5200"></i>미판정 {len(bundle.undetermined)}항목 — '
                     f'무엇을 판정하지 못했고, 누가 이어받는가</h2>')
        parts.append('<table><tr><th>항목</th><th>무엇을 확인하나요</th><th>영역</th><th>사유</th>'
                     '<th>다음 담당</th><th>조치</th></tr>')
        for u in bundle.undetermined:
            parts.append(f'<tr><td class="n"><b>{_e(u["항목"])}</b></td>'
                         f'<td>{_e(u["항목명"])}</td>'
                         f'<td>{_e(u["영역"])}</td><td>{_e(u["사유"])}</td>'
                         f'<td>{_e(u["다음 담당"])}</td>'
                         f'<td>{_e(" ".join(u["안내"].split())[:70])}</td></tr>')
        parts.append('</table>')

    # 40항목 매트릭스
    parts.append('<h2><i></i>별표5 40항목 판정 매트릭스</h2>')
    parts.append('<table><tr><th>항목</th><th>무엇을 확인하나요</th><th>구분</th><th>판정</th><th>등급</th>'
                 '<th>트랙</th><th>출처</th><th>측정값</th></tr>')
    for row in bundle.matrix:
        fg, bg = _TONE.get(row["판정"], ("#5D6B79", "#F6F8FA"))
        m = row["측정값"]
        mtxt = ""
        if isinstance(m, dict) and m:
            k = next(iter(m))
            mtxt = f"{k}: {m[k]}"
        parts.append(
            f'<tr><td class="n"><b>{_e(row["항목"])}</b></td>'
            f'<td>{_e(row["항목명"])}</td><td>{_e(row["구분"])}</td>'
            f'<td><span class="cell" style="background:{bg};color:{fg}">{_e(row["판정"])}</span></td>'
            f'<td>{_e(row["등급"])}</td><td class="n">{_e(row["트랙"])}</td>'
            f'<td>{_e(row["출처"])}</td><td>{_e(str(mtxt)[:52])}</td></tr>')
    parts.append('</table>')

    parts.append('<div class="foot">모든 판정에는 근거 이미지 · 측정값 · 별표5 항목 ID · 확신도가 '
                 '함께 보관됩니다. 결정론적 계측 항목은 계산식과 구현 파일명이 기록되어 '
                 '같은 입력에 대해 같은 값이 재현됩니다.</div>')
    parts.append('</div>')
    return _wrap("별표5 항목별 판정 결과", "".join(parts))


def _wrap(title: str, body: str) -> str:
    return (f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title><style>{_CSS}</style></head>'
            f'<body><div class="page">{body}</div></body></html>\n')
