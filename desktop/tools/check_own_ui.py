#!/usr/bin/env python
"""우리 화면이 우리 기준을 통과하는가 — 자기 자신에게 자를 대는 검사.

    KFA.exe self-check
    python tools/check_own_ui.py

이 도구는 별표5 3.i(명도 대비 4.5:1)로 키오스크를 판정한다.
그런데 정작 **우리 화면이 그 기준에 미달**이었다. 합동 평가에서 드러난 것들:

    수집 앱  '이번 진단 대상' 숫자      1.41 : 1   ← 다크 모드에서 --deep 미정의
    수집 앱  입력 라벨 --faint          2.98 : 1   ← 색이 그냥 너무 흐렸다
    리포트A  주의색 #B26A00 (흰 배경)   4.24 : 1
    리포트A  주의색 (주의 박스 위)       3.79 : 1

크게 키운 글씨(26px)가 대비 미달을 가려 준 탓에 아무도 눈치채지 못했다.
**큰 글씨는 대비 미달을 가려 줄 뿐 없애지 않는다.**

그래서 이 검사를 만들었다. 판정에 쓰는 engine.calc.contrast 를 그대로 써서
우리 CSS 의 색 조합을 검사한다. 같은 결함이 다시 들어오면 테스트가 막는다.

무엇을 검사하지 않는가
    브라우저를 띄우지 않으므로 '실제로 화면에 어떻게 합성되는가'는 보지 못한다.
    여기서 보는 것은 **선언된 색 조합**이다. 새 조합을 추가하면 PAIRS 에도 적어야
    검사에 들어온다 — 그 수고가 곧 '이 색을 어디에 쓸 것인가'를 한 번 더 생각하게 한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.calc import contrast as C                       # noqa: E402
from engine.paths import resource_dir                       # noqa: E402

REQUIRED = C.THRESHOLD_NORMAL          # 별표5 3.i — 4.5 : 1
MIN_FONT_PT = 9.0                      # 점주용 리포트 글자 하한 (≈12px)

# ── 수집 앱 (app/style.css) ──────────────────────────────────────────────
# (전경 토큰, 배경 토큰, 어디에 쓰이는가)
APP_PAIRS = [
    ("--ink", "--paper", "본문 / 페이지 바닥"),
    ("--ink", "--surface", "본문 / 카드"),
    ("--ink-2", "--surface", "보조 본문 / 카드"),
    ("--muted", "--surface", "설명 문구 / 카드"),
    ("--faint", "--surface", "라벨 / 카드"),
    ("--faint", "--surface-2", "라벨 / 옅은 면"),
    ("--deep", "--surface", "진단 대상 숫자 / 카드"),
    ("--a", "--surface", "촬영물 분석 / 카드"),
    ("--b", "--surface", "점주 셀프 체크 / 카드"),
    ("--c", "--surface", "전문 실측 / 카드"),
    ("--ok", "--surface", "완료 표시 / 카드"),
    ("--bad", "--surface", "경고 표시 / 카드"),
    ("--warn", "--surface", "주의 표시 / 카드"),
    # 배경으로 쓰이는 토큰 — 이쪽을 빼먹으면 '글자색만' 고치다 배경을 망친다.
    # 실제로 --deep-ink 를 다크에서 밝게 바꿨다가 흰 글씨가 1.39:1 이 됐다.
    ("#FFFFFF", "--deep-ink", "흰 글씨 / 진한 버튼·머리말"),
    ("--accent", "--deep-ink", "강조 글씨 / 진한 배지"),
    ("--deep-ink", "--accent", "진한 글씨 / 강조 버튼"),
    # 마스킹 오버레이는 테마와 무관하게 항상 어둡다. 그 위에 테마 토큰을 쓰면
    # 라이트 모드에서 어두운 글씨가 어두운 바탕에 얹힌다 (실측 1.66:1).
    ("#DCE4EC", "#121A22", "오버레이 버튼 / 머리말"),
    ("#9FB0C2", "#121A22", "오버레이 안내"),
    ("#C3D2E0", "#121A22", "굵기 라벨"),
]

# ── 리포트 (engine/report/render.py) — 인쇄용이라 색을 직접 쓴다 ─────────
REPORT_PAIRS = [
    ("#131A22", "#FFFFFF", "본문"),
    ("#33404E", "#FFFFFF", "보조 본문"),
    ("#5D6B79", "#FFFFFF", "라벨"),
    ("#5D6B79", "#F6F8FA", "표 머리"),
    ("#5D6B79", "#ECF0F4", "회색 태그"),
    ("#8C5200", "#FFFFFF", "위반 의심 숫자"),
    ("#8C5200", "#FDF1DC", "주의 박스"),
    ("#1E7A4D", "#FFFFFF", "적합"),
    ("#1E7A4D", "#E3F2EA", "적합 태그"),
    ("#B3261E", "#FFFFFF", "부적합"),
    ("#B3261E", "#FBE9E7", "부적합 태그"),
    ("#17385C", "#FFFFFF", "합계 숫자"),
    ("#1B579B", "#E7EFF9", "SW 태그"),
    ("#99451F", "#F7E9E2", "HW 태그"),
    ("#24705A", "#E3F0EB", "운영 태그"),
    ("#9FB0C2", "#0E2439", "머리말 메타"),
    ("#F2B705", "#0E2439", "머리말 눈썹"),
    # 합동 평가 후속으로 추가된 것 — '함께 볼 것' 줄과 사용성 주의 안내
    ("#24705A", "#FFFFFF", "함께 볼 것"),
    ("#33404E", "#F6F8FA", "사용성 주의 안내"),
]

# ── 조작판 (tools/panel.py) ─────────────────────────────────────────────
# 조작판은 app/style.css 의 :root 만 가져다 쓴다 — 다크 블록은 가져오지 않는다.
# 그래서 테마와 무관하게 항상 밝은 화면이다(배경을 자기가 칠한다).
# 여기서 라이트 토큰으로만 검사하는 이유가 그것이다.
PANEL_PAIRS = [
    ("--ink", "--paper", "본문 / 페이지 바닥"),
    ("--muted", "--paper", "설명 문구 / 페이지 바닥"),
    ("--ink", "--surface-2", "옅은 버튼 위 글씨"),
    ("--ink", "--a-bg", "고른 항목"),
    ("--muted", "--a-bg", "고른 항목 경로"),
    ("--a", "--a-bg", "진행 중 표시"),
    ("--ok", "--ok-bg", "완료 표시"),
    ("--bad", "--bad-bg", "오류 표시"),
    ("--warn", "--warn-bg", "주의 안내"),
    ("#E4ECF4", "--deep-ink", "실행 기록 글씨"),
]

_TOKEN = re.compile(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})")
_FONT_PT = re.compile(r"font-size:\s*([0-9.]+)pt")
_HIDDEN_EL = re.compile(r"<(\w+)[^>]*\sid=\"([\w-]+)\"[^>]*\shidden[\s/>]")


def check_hidden_overlays(html: str, css: str) -> list[str]:
    """hidden 속성이 CSS 에 밀려 무력화된 요소를 찾는다.

    브라우저 기본 스타일의 [hidden]{display:none} 은 **작성자 규칙에 진다.**
    그래서 #mask-overlay 처럼 display 를 지정한 요소에 hidden 을 걸면
    숨겨지지 않는다. 실제로 마스킹 오버레이가 화면 전체를 덮고 z-index:100 으로
    모든 터치를 가로챘다 — 기기 ID 칸을 탭하면 mask-canvas 가 잡혔다.

    DOM 만 읽는 검사로는 보이지 않는다. 요소는 멀쩡히 거기 있기 때문이다.
    """
    bad = []
    for _tag, el_id in _HIDDEN_EL.findall(html):
        sets_display = re.search(
            rf"#{re.escape(el_id)}\s*\{{[^}}]*display\s*:\s*(?!none)", css)
        has_guard = re.search(
            rf"#{re.escape(el_id)}\[hidden\]\s*\{{[^}}]*display\s*:\s*none", css)
        if sets_display and not has_guard:
            bad.append(el_id)
    return bad


def read_tokens(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """라이트/다크 토큰을 각각 모은다.

    다크 블록에서 재정의되지 않은 색이 진짜 위험하다 —
    '이번 진단 대상' 숫자가 1.41:1 로 무너진 것이 정확히 그 경우였다.
    """
    dark_start = css.find("prefers-color-scheme: dark")
    light_src = css if dark_start < 0 else css[:dark_start]
    dark_src = "" if dark_start < 0 else css[dark_start:]

    light = {m.group(1): m.group(2) for m in _TOKEN.finditer(light_src)}
    dark = dict(light)
    dark.update({m.group(1): m.group(2) for m in _TOKEN.finditer(dark_src)})
    return light, dark


def check_pairs(pairs, resolve, *, theme: str) -> list[dict]:
    """색 조합을 검사한다. 토큰(--x)은 테마에서 풀고, 리터럴(#RRGGBB)은 그대로 쓴다."""
    def pick(key):
        return key if key.startswith("#") else resolve(key)

    out = []
    for fg_key, bg_key, where in pairs:
        fg, bg = pick(fg_key), pick(bg_key)
        if not fg or not bg:
            out.append({"theme": theme, "where": where, "fg": fg_key, "bg": bg_key,
                        "ratio": None, "ok": False, "note": "토큰을 찾지 못했습니다"})
            continue
        r = C.evaluate(C.hex_to_rgb(fg), C.hex_to_rgb(bg), required=REQUIRED)
        out.append({"theme": theme, "where": where, "fg": fg, "bg": bg,
                    "ratio": r.ratio, "ok": r.passed, "note": ""})
    return out


def audit() -> dict:
    root = resource_dir()
    app_css = (root / "app" / "style.css").read_text(encoding="utf-8")
    # 리포트 CSS 는 **모듈 상수에서 가져온다.** .py 를 읽으면 실행 파일 안에서
    # FileNotFoundError 로 죽는다 — exe 옆에는 소스가 없다. gaps 에서 이미 데인 함정이다.
    from engine.report.render import _CSS as report_css

    light, dark = read_tokens(app_css)
    rows = []
    rows += check_pairs(APP_PAIRS, light.get, theme="앱 · 라이트")
    rows += check_pairs(APP_PAIRS, dark.get, theme="앱 · 다크")
    rows += check_pairs(PANEL_PAIRS, light.get, theme="조작판")
    rows += check_pairs(REPORT_PAIRS, lambda k: k, theme="리포트")

    # 리포트 글자 하한 — 점주는 60~80대일 수 있다
    small = sorted({float(m.group(1)) for m in _FONT_PT.finditer(report_css)
                    if float(m.group(1)) < MIN_FONT_PT})

    # 다크 블록에서 재정의되지 않은 색 토큰 (테마 밖에서만 정의된 색)
    dark_start = app_css.find("prefers-color-scheme: dark")
    redefined = set()
    if dark_start >= 0:
        redefined = {m.group(1) for m in _TOKEN.finditer(app_css[dark_start:])}
    # 참고용이다. 두 테마에서 모두 통과하는 색이면 재정의할 필요가 없다 —
    # 진짜 보증은 위의 쌍 검사가 한다. 이 목록은 '한 번 볼 만한 곳'을 알려줄 뿐이다.
    used_in_pairs = {k for p in APP_PAIRS for k in p[:2] if not k.startswith("#")}
    unthemed = sorted(k for k in used_in_pairs if k not in redefined)

    app_html = (root / "app" / "index.html").read_text(encoding="utf-8")
    hidden_broken = check_hidden_overlays(app_html, app_css)

    return {"rows": rows, "small_pt": small, "unthemed": unthemed,
            "hidden_broken": hidden_broken,
            "failed": [r for r in rows if not r["ok"]]}


def main() -> int:
    ap = argparse.ArgumentParser(description="우리 화면이 우리 기준을 통과하는가")
    ap.add_argument("--verbose", action="store_true", help="통과한 조합도 표시")
    a = ap.parse_args()

    d = audit()
    print("=" * 74)
    print(" 자기 검사 — 우리 화면에 우리 자를 댄다")
    print("=" * 74)
    print(f"\n  기준  명도 대비 {REQUIRED} : 1 이상 (별표5 3.i) · 리포트 글자 {MIN_FONT_PT}pt 이상")
    print(f"  검사  색 조합 {len(d['rows'])}건\n")

    print("-" * 74)
    for r in d["rows"]:
        if r["ok"] and not a.verbose:
            continue
        mark = "[ OK ]" if r["ok"] else "[미달]"
        ratio = f"{r['ratio']:.2f}" if r["ratio"] is not None else "  -  "
        print(f"  {mark} {r['theme']:<12}{r['where']:<22}{ratio:>7} : 1  {r['note']}")

    if not d["failed"]:
        print("  대비 미달 없음")
    print("-" * 74)

    if d["small_pt"]:
        print(f"\n  [미달] 리포트에 {MIN_FONT_PT}pt 미만 글자: "
              f"{', '.join(f'{v:g}pt' for v in d['small_pt'])}")
        print("         점주용 리포트는 60~80대가 읽습니다.")

    if d["unthemed"]:
        print(f"\n  [참고] 다크 블록에서 재정의되지 않은 토큰: {', '.join(d['unthemed'])}")
        print("         위 쌍 검사를 통과했다면 두 테마에서 모두 쓸 수 있는 색입니다.")

    if d["hidden_broken"]:
        print(f"\n  [미달] hidden 이 CSS 에 밀려 무력화된 요소: {', '.join(d['hidden_broken'])}")
        print("         화면을 덮고 터치를 가로챕니다.")
        print("         #<id>[hidden] 규칙에 display: none 을 넣으세요.")

    bad = len(d["failed"]) + len(d["small_pt"]) + len(d["hidden_broken"])
    print("\n" + "=" * 74)
    if bad == 0:
        print(" 통과 — 우리가 요구하는 기준을 우리도 지키고 있습니다")
    else:
        print(f" 미달 {bad}건 — 키오스크에 요구하는 것을 우리가 지키지 못하고 있습니다")
    print("=" * 74 + "\n")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
