#!/usr/bin/env python
"""합성 촬영 검증 — 측정 체인이 실제 이미지에서 성립하는지 확인한다.

    python tools/simulate_capture.py
    python tools/simulate_capture.py --save   # 합성 사진을 assets/ 에 저장

무엇을 검증하는가
────────────────────────────────────────────────────────────────────
    현장 사진이 없어도 측정 체인은 검증할 수 있다.
    **정답을 아는 화면**을 합성하고, 실제 마커 카드를 붙이고,
    비스듬히 찍은 것처럼 왜곡시킨 뒤, 엔진이 정답을 되찾아내는지 본다.

        정답 생성 → 마커 부착 → 사영 왜곡 + 노이즈 + JPEG
          → 마커 검출 → 평면 환산 → 치수·대비 계측 → 판정

    이 경로가 통과하면, 남은 것은 '진짜 사진을 넣는 배선'뿐이다.
    계측 자체는 이미 동작한다는 뜻이다.

    쓰이는 마커는 tools/make_marker.py 가 만든 **실물 카드 이미지 그대로**다.
    합성 마커가 아니므로 인쇄해서 찍은 것과 같은 검출 경로를 탄다.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import cv2
import numpy as np

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir  # noqa: E402

ROOT = resource_dir()

from engine import rules as R                                    # noqa: E402
from engine.calc import contrast, geometry, scale                # noqa: E402
from engine.verdict import Evidence, Verdict, apply_ai_scope     # noqa: E402

# ── 합성 조건 ─────────────────────────────────────────────────────────────
PPM = 8.0                 # 합성 캔버스 배율 (px/mm). 현장 사진의 해상도 감각에 맞춘다
PANEL_W_MM, PANEL_H_MM = 260.0, 170.0

# 정답 — 엔진이 이 값들을 되찾아야 한다
TRUTH = {
    "btn_ok":     {"w": 14.0, "h": 14.0, "x": 20.0, "y": 40.0},   # 별표5 1.c 통과
    "btn_narrow": {"w": 10.0, "h": 22.0, "x": 20.0, "y": 62.0},   # 한 변 10mm → 부적합
    "gap_pair_a": {"w": 16.0, "h": 16.0, "x": 60.0, "y": 40.0},
    "gap_pair_b": {"w": 16.0, "h": 16.0, "x": 78.0, "y": 40.0},   # 간격 2.0mm → 부적합
    "text_fg": "#9AA3AB", "text_bg": "#FFFFFF",                    # 대비 2.56 → 부적합
}
GAP_TRUTH_MM = TRUTH["gap_pair_b"]["x"] - (TRUTH["gap_pair_a"]["x"] + TRUTH["gap_pair_a"]["w"])


def mm(v: float) -> int:
    return int(round(v * PPM))


def hex_bgr(h: str) -> tuple[int, int, int]:
    r, g, b = contrast.hex_to_rgb(h)
    return (b, g, r)


def build_panel() -> tuple[np.ndarray, dict[str, list[tuple[float, float]]]]:
    """정답을 아는 조작부 패널을 그린다. 반환: (이미지, 정답 사각형 px 좌표)"""
    W, H = mm(PANEL_W_MM), mm(PANEL_H_MM)
    img = np.full((H, W, 3), 236, np.uint8)
    cv2.rectangle(img, (0, 0), (W - 1, H - 1), (200, 200, 200), 2)

    quads: dict[str, list[tuple[float, float]]] = {}
    for name in ("btn_ok", "btn_narrow", "gap_pair_a", "gap_pair_b"):
        t = TRUTH[name]
        x0, y0 = mm(t["x"]), mm(t["y"])
        x1, y1 = mm(t["x"] + t["w"]), mm(t["y"] + t["h"])
        cv2.rectangle(img, (x0, y0), (x1, y1), (90, 90, 90), -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (40, 40, 40), 1)
        quads[name] = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    # 대비 검사용 문구 영역 — 흰 배경에 회색 글자
    tx0, ty0 = mm(20.0), mm(105.0)
    cv2.rectangle(img, (tx0, ty0), (mm(200.0), mm(135.0)), hex_bgr(TRUTH["text_bg"]), -1)
    cv2.putText(img, "Select payment method", (tx0 + mm(4), ty0 + mm(20)),
                cv2.FONT_HERSHEY_SIMPLEX, PPM / 8 * 1.1, hex_bgr(TRUTH["text_fg"]),
                max(1, int(PPM / 4)), cv2.LINE_AA)

    return img, quads


def attach_marker(panel: np.ndarray, quads: dict) -> tuple[np.ndarray, dict]:
    """실물 마커 카드를 같은 평면에 붙인다.

    파일이 있으면 쓰고, 없으면 그 자리에서 만든다 — 검증이 '마커 카드를 먼저
    생성했는가'라는 실행 순서에 의존하면 안 되기 때문이다. 어느 쪽이든
    tools/make_marker.py 의 build() 가 만든 것과 동일한 카드다.
    """
    card = None
    for base in (output_dir(), ROOT / "assets"):
        p = base / "BFK-MARK-A_id0.png"
        if p.exists():
            card = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
            break
    if card is None:
        from tools.make_marker import build as build_marker
        card = cv2.cvtColor(build_marker(0), cv2.COLOR_GRAY2BGR)
    # 카드는 85.6 x 54.0mm. 패널 배율(PPM)로 축소해 같은 평면에 놓는다
    cw, ch = mm(85.6), mm(54.0)
    card = cv2.resize(card, (cw, ch), interpolation=cv2.INTER_AREA)

    cx, cy = mm(150.0), mm(35.0)
    panel[cy:cy + ch, cx:cx + cw] = card
    return panel, quads


def photograph(img: np.ndarray, *, tilt: float = 0.18, jpeg_q: int = 82,
               noise: float = 2.5) -> tuple[np.ndarray, np.ndarray]:
    """비스듬히 찍은 사진처럼 만든다. 반환: (사진, 사영변환 행렬)"""
    H, W = img.shape[:2]
    pad = int(max(W, H) * 0.12)
    canvas = np.full((H + 2 * pad, W + 2 * pad, 3), 128, np.uint8)
    canvas[pad:pad + H, pad:pad + W] = img

    src = np.float32([[pad, pad], [pad + W, pad], [pad + W, pad + H], [pad, pad + H]])
    dx, dy = W * tilt, H * tilt * 0.35
    dst = np.float32([
        [pad + dx * 0.55, pad + dy],
        [pad + W - dx * 0.10, pad + dy * 0.30],
        [pad + W - dx * 0.30, pad + H - dy * 0.15],
        [pad + dx * 0.20, pad + H - dy * 0.55],
    ])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(canvas, M, (canvas.shape[1], canvas.shape[0]),
                                 flags=cv2.INTER_LINEAR, borderValue=(128, 128, 128))

    rng = np.random.default_rng(20260822)
    warped = np.clip(warped.astype(np.float32) + rng.normal(0, noise, warped.shape), 0, 255).astype(np.uint8)

    ok, enc = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, jpeg_q])
    photo = cv2.imdecode(enc, cv2.IMREAD_COLOR) if ok else warped

    # 정답 좌표도 같은 변환을 태운다 (검증용 — 엔진은 이 값을 모른다)
    def fwd(p):
        v = M @ np.array([p[0] + pad, p[1] + pad, 1.0])
        return (float(v[0] / v[2]), float(v[1] / v[2]))
    return photo, fwd


def main() -> int:
    ap = argparse.ArgumentParser(description="합성 촬영으로 측정 체인 검증")
    ap.add_argument("--save", action="store_true", help="합성 사진 저장")
    a = ap.parse_args()

    print("=" * 74)
    print(" 합성 촬영 검증 — 정답을 아는 화면을 찍어 엔진이 되찾는지 본다")
    print("=" * 74)

    panel, quads = build_panel()
    panel, quads = attach_marker(panel, quads)
    photo, fwd = photograph(panel)

    print(f"\n  합성 사진   {photo.shape[1]}×{photo.shape[0]}px  "
          f"(사영 왜곡 + 가우시안 노이즈 + JPEG 82%)")
    print(f"  마커        assets/BFK-MARK-A_id0.png 실물 카드 이미지")

    if a.save:
        out = output_dir() / "simulated-capture.jpg"
        cv2.imencode(".jpg", photo)[1].tofile(str(out))
        print(f"  저장        {out}")

    # ── ① 마커 검출 ─────────────────────────────────────────────────────
    print("\n" + "─" * 74)
    print(" ① 마커 검출")
    print("─" * 74)
    found = scale.detect_marker_corners(photo, marker_id=0)
    if not found:
        print("  [FAIL] 마커를 찾지 못했습니다")
        return 1
    corners, mid = found
    print(f"  [ OK ] id={mid} · 4점 검출 · 경계 보정 적용")

    # ── ② 평면 환산 ─────────────────────────────────────────────────────
    print("\n" + "─" * 74)
    print(" ② 평면 환산 (호모그래피)")
    print("─" * 74)
    try:
        s = scale.build_plane_scale(corners, plane="control_panel", frame_width_px=photo.shape[1])
    except scale.MarkerError as e:
        print(f"  [FAIL] {e}")
        return 1
    print(f"  [ OK ] 배율 {s.px_per_mm:.2f} px/mm · 왜곡비 {s.skew:.3f} · 확신도 {s.confidence}")
    print(f"         (합성 원본 배율 {PPM:.2f} px/mm — 왜곡으로 달라지는 것이 정상)")

    # ── ③ 치수 계측 — 정답과 대조 ──────────────────────────────────────
    print("\n" + "─" * 74)
    print(" ③ 치수 계측 — 정답을 되찾는가")
    print("─" * 74)
    warped_quads = {k: [fwd(p) for p in v] for k, v in quads.items()}
    boxes = geometry.boxes_from_pixels(warped_quads, s)
    by = {b.id: b for b in boxes}

    print(f"\n  {'작동부':<14}{'정답 W×H':>14}{'측정 W×H':>16}{'오차':>10}")
    print("  " + "-" * 56)
    worst = 0.0
    for name in ("btn_ok", "btn_narrow", "gap_pair_a"):
        t, b = TRUTH[name], by[name]
        ew = abs(b.w - t["w"]) / t["w"] * 100
        eh = abs(b.h - t["h"]) / t["h"] * 100
        worst = max(worst, ew, eh)
        print(f"  {name:<14}{t['w']:>6.1f}×{t['h']:<7.1f}{b.w:>8.2f}×{b.h:<7.2f}{max(ew, eh):>8.1f}%")

    gap = geometry.check_gaps([by["gap_pair_a"], by["gap_pair_b"]], surface="touch")
    ge = abs(gap.min_gap_mm - GAP_TRUTH_MM) / GAP_TRUTH_MM * 100
    worst = max(worst, ge)
    print(f"  {'버튼 간격':<14}{GAP_TRUTH_MM:>13.1f}{gap.min_gap_mm:>16.2f}{ge:>9.1f}%")
    print(f"\n  최대 오차 {worst:.1f}%   (목표 ±10% 이내)  "
          f"{'[ OK ]' if worst <= 10 else '[FAIL]'}")

    # ── ④ 명도 대비 — 사진에서 색을 뽑아 계산 ──────────────────────────
    print("\n" + "─" * 74)
    print(" ④ 명도 대비 (3.i) — 사진 픽셀에서 직접")
    print("─" * 74)
    # 문구 영역을 사영변환해 그 안에서만 표본을 뽑는다.
    # 백분위로 뽑으면 얇은 글자를 놓치고 배경만 잡힌다 → Otsu 로 두 무리를 가른다.
    text_quad = [fwd((mm(20.0), mm(107.0))), fwd((mm(200.0), mm(107.0))),
                 fwd((mm(200.0), mm(133.0))), fwd((mm(20.0), mm(133.0)))]
    xs = [p[0] for p in text_quad]; ys = [p[1] for p in text_quad]
    x0, x1 = int(max(0, min(xs))), int(min(photo.shape[1], max(xs)))
    y0, y1 = int(max(0, min(ys))), int(min(photo.shape[0], max(ys)))
    patch = photo[y0:y1, x0:x1]

    sampled = contrast.sample_fg_bg(patch)
    if sampled is None:
        print("  [FAIL] 문구 영역에서 전경·배경을 가르지 못했습니다")
        return 1
    fg, bg, disp = sampled
    c = contrast.evaluate(fg, bg, required=contrast.THRESHOLD_NORMAL, dispersion=disp)

    truth_ratio = contrast.contrast_ratio(TRUTH["text_fg"], TRUTH["text_bg"])
    err = abs(c.ratio - truth_ratio) / truth_ratio * 100
    print(f"  표본 영역   {patch.shape[1]}×{patch.shape[0]}px  (Otsu 이진화로 전경·배경 분리)")
    print(f"  정답 대비   {truth_ratio:.2f} : 1   ({TRUTH['text_fg']} on {TRUTH['text_bg']})")
    print(f"  측정 대비   {c.ratio:.2f} : 1   ({c.fg} on {c.bg})")
    print(f"  오차        {err:.1f}%   {'[ OK ]' if err <= 15 else '[FAIL]'}")
    print(f"  색 산포     {disp:.1f}  →  확신도 {c.confidence}")
    print(f"  판정        기준 4.5:1 → {'미달' if not c.passed else '충족'}")

    # ── ⑤ 판정 → 사정거리 통과 ────────────────────────────────────────
    print("\n" + "─" * 74)
    print(" ⑤ 판정 (apply_ai_scope 통과)")
    print("─" * 74)
    rs = R.load()

    a_3i = apply_ai_scope(
        rs["3.i"], Verdict.FAIL if not c.passed else Verdict.PASS,
        evidence=Evidence(crop_ref="simulated/text.png", measured=c.as_measured(),
                          criterion_id="3.i", confidence=c.confidence),
    )
    mbr = geometry.check_mbr(boxes)
    a_1c = apply_ai_scope(
        rs["1.c"], Verdict.FAIL if not mbr.passed else Verdict.PASS,
        evidence=Evidence(crop_ref="simulated/panel.png", measured=mbr.as_measured(),
                          criterion_id="1.c", confidence=s.confidence),
    )
    a_1b = apply_ai_scope(
        rs["1.b"], Verdict.FAIL if not gap.passed else Verdict.PASS,
        evidence=Evidence(crop_ref="simulated/panel.png", measured=gap.as_measured(),
                          criterion_id="1.b", confidence=s.confidence),
    )

    print(f"\n  {'항목':<7}{'판정':<12}{'다음 담당':<12}근거")
    print("  " + "-" * 64)
    for a_, note in ((a_3i, f"대비 {c.ratio:.2f}:1"),
                     (a_1c, f"미달 {len(mbr.violations)}개"),
                     (a_1b, f"최소 간격 {gap.min_gap_mm:.2f}mm")):
        print(f"  {a_.rule_id:<7}{a_.verdict.value:<12}{(a_.next_owner or ''):<12}{note}")

    print("\n" + "=" * 74)
    ok = (worst <= 10 and err <= 15
          and not c.passed and not mbr.passed and not gap.passed)
    if ok:
        print(" 측정 체인 정상 — 사진에서 mm 를 되찾아 판정까지 도달했습니다")
        print(" 남은 것은 '진짜 사진을 폴더에서 읽어 오는 배선'뿐입니다")
    else:
        print(" 검증 실패 — 위 수치를 확인하세요")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
