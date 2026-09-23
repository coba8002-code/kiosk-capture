#!/usr/bin/env python
"""BFK-MARK-A 기준 스케일 마커 카드 생성기.

    python tools/make_marker.py                # id 0~3 카드 4장
    python tools/make_marker.py --ids 0 --sheet  # A4 인쇄 시트까지

신용카드 크기(85.6 × 54mm) 카드에 ArUco 마커를 정확한 물리 치수로 인쇄한다.
이 카드 한 장이 별표5 치수 항목 6개(1.b·1.c·1.i·3.g·9.a·9.b)를
'추정'에서 '측정'으로 옮긴다. 이 사업에서 가장 값싼 결정.

해상도를 762dpi 로 고정한 이유 ------------------------------------------
    762dpi = 정확히 30 px/mm 다. 이 격자에서만 카드(2568×1620px)와
    마커(1200px = 6셀 × 200px)가 모두 정수로 떨어진다.
    600dpi 로 만들면 마커 6셀이 정수 픽셀에 맞지 않아 렌더 격자가 어긋나고,
    검출된 변 길이가 40mm 가 아니라 39.50mm 로 나온다(-1.3%).
    이 오차는 문자 높이 7.25mm 판정을 그대로 0.09mm 씩 밀어버린다.

검출기 경계 규약 ---------------------------------------------------------
    ArUco 검출기는 마커의 마지막 검은 '픽셀 인덱스'를 코너로 돌려준다.
    실제 경계보다 1px 안쪽이므로 검출 변은 1200px 이 아니라 1199px 이다.
    비율로 0.083% — 목표 정확도(±10%) 대비 무시할 수준이지만,
    현장 이미지 해상도가 낮으면 상대적으로 커지므로 상수로 명시해 둔다.

인쇄 규칙 — 어기면 환산이 통째로 틀어진다 -------------------------------
    · '실제 크기 100%'로 인쇄한다. '용지에 맞춤'은 절대 쓰지 않는다.
    · 무광 코팅 200g 이상. 유광은 화면 반사와 겹쳐 검출률이 떨어진다.
    · 인쇄 후 카드의 20mm 눈금자를 실제 자로 대어 검증한다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import output_dir  # noqa: E402

PPM = 30                      # px/mm — 정수 격자. 바꾸지 말 것.
DPI = int(round(PPM * 25.4))  # 762
CARD_W_MM, CARD_H_MM = 85.6, 54.0
MARKER_MM = 40.0
QUIET_MM = 4.0
DETECTOR_EDGE_BIAS_PX = 1.0   # 검출기가 실제 경계보다 1px 안쪽을 돌려준다

DICT = cv2.aruco.DICT_4X4_50
TOLERANCE_MM = 0.1


def px(v_mm: float) -> int:
    return int(round(v_mm * PPM))


def build(marker_id: int) -> np.ndarray:
    W, H = px(CARD_W_MM), px(CARD_H_MM)
    card = np.full((H, W), 255, np.uint8)

    d = cv2.aruco.getPredefinedDictionary(DICT)
    side = px(MARKER_MM)                       # 1200 = 6셀 × 200px
    marker = cv2.aruco.generateImageMarker(d, marker_id, side)

    x, y = (W - side) // 2, (H - side) // 2
    card[y:y + side, x:x + side] = marker

    # 인쇄 배율 검증용 20mm 눈금자 — 자를 대보면 인쇄가 틀어졌는지 즉시 안다
    base = H - px(4.5)
    for i in range(11):
        gx = px(4.0 + i * 2.0)
        h = px(2.2) if i % 5 == 0 else px(1.2)
        cv2.line(card, (gx, base), (gx, base - h), 0, max(1, px(0.18)))
    cv2.line(card, (px(4.0), base), (px(24.0), base), 0, max(1, px(0.18)))

    f = cv2.FONT_HERSHEY_SIMPLEX
    sc, th = PPM / 30 * 0.9, max(1, int(PPM / 30 * 2))
    cv2.putText(card, "BFK-MARK-A", (px(4.0), px(6.5)), f, sc, 80, th, cv2.LINE_AA)
    cv2.putText(card, f"id {marker_id} / DICT_4X4_50", (px(4.0), px(11.0)), f, sc * 0.72, 140, th, cv2.LINE_AA)
    cv2.putText(card, f"{MARKER_MM:.0f}mm", (W - px(16.0), px(6.5)), f, sc * 0.8, 140, th, cv2.LINE_AA)
    cv2.putText(card, "0", (px(3.2), base + px(3.6)), f, sc * 0.62, 140, th, cv2.LINE_AA)
    cv2.putText(card, "20mm", (px(18.0), base + px(3.6)), f, sc * 0.62, 140, th, cv2.LINE_AA)
    cv2.putText(card, "PRINT AT 100%", (W - px(28.0), H - px(4.0)), f, sc * 0.62, 150, th, cv2.LINE_AA)

    cv2.rectangle(card, (0, 0), (W - 1, H - 1), 195, max(1, px(0.25)))
    return card


def verify(card: np.ndarray, marker_id: int) -> dict:
    """생성한 카드를 스스로 다시 읽어 검출과 치수를 검증한다.

    자기검증이 없는 생성기는 조용히 틀린 카드를 찍어낸다.
    """
    d = cv2.aruco.getPredefinedDictionary(DICT)
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(cv2.cvtColor(card, cv2.COLOR_GRAY2BGR))
    if ids is None or marker_id not in ids.flatten():
        return {"detected": False}

    quad = corners[list(ids.flatten()).index(marker_id)].reshape(4, 2)
    sides = [float(np.linalg.norm(quad[(i + 1) % 4] - quad[i])) for i in range(4)]
    raw = float(np.mean(sides))
    corrected = raw + DETECTOR_EDGE_BIAS_PX
    return {
        "detected": True,
        "side_px": round(raw, 1),
        "side_mm": round(raw / PPM, 4),
        "corrected_mm": round(corrected / PPM, 4),
        "skew": round(max(sides) / min(sides), 4),
    }


def sheet(cards: list[np.ndarray]) -> np.ndarray:
    """A4 인쇄 시트. 재단선 포함, 실제 크기 100% 인쇄용."""
    W, H = px(210.0), px(297.0)
    page = np.full((H, W), 255, np.uint8)
    cw, ch = px(CARD_W_MM), px(CARD_H_MM)
    mx, my, gap = px(20.0), px(24.0), px(10.0)

    for i, c in enumerate(cards[:4]):
        x, y = mx, my + i * (ch + gap)
        if y + ch > H - my:
            break
        page[y:y + ch, x:x + cw] = c
        for (a, b) in [((x - px(4), y), (x - px(1), y)), ((x + cw + px(1), y), (x + cw + px(4), y)),
                       ((x, y - px(4)), (x, y - px(1))), ((x, y + ch + px(1)), (x, y + ch + px(4)))]:
            cv2.line(page, a, b, 150, max(1, px(0.2)))

    f = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(page, "BFK-MARK-A  scale reference cards", (mx, my - px(9)),
                f, PPM / 30 * 1.0, 60, max(1, int(PPM / 30 * 2)), cv2.LINE_AA)
    cv2.putText(page, "PRINT AT 100% (do not 'fit to page').  Matte 200gsm or heavier.",
                (mx, my - px(4)), f, PPM / 30 * 0.7, 130, max(1, int(PPM / 30 * 2)), cv2.LINE_AA)
    cv2.putText(page, f"{DPI} dpi = {PPM} px/mm   marker {MARKER_MM:.0f}mm   card {CARD_W_MM} x {CARD_H_MM} mm",
                (mx, H - my), f, PPM / 30 * 0.7, 130, max(1, int(PPM / 30 * 2)), cv2.LINE_AA)
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description="BFK-MARK-A 마커 카드 생성")
    ap.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--out", default=None, help="저장 폴더 (기본: 실행 파일 옆 assets)")
    ap.add_argument("--sheet", action="store_true", help="A4 인쇄 시트도 생성")
    a = ap.parse_args()
    out = Path(a.out) if a.out else output_dir()
    out.mkdir(parents=True, exist_ok=True)
    a.out = str(out)

    print(f"BFK-MARK-A   카드 {CARD_W_MM}×{CARD_H_MM}mm   마커 {MARKER_MM}mm")
    print(f"             {DPI}dpi = {PPM} px/mm (정수 격자)\n")

    cards, ok = [], True
    for i in a.ids:
        card = build(i)
        cards.append(card)
        path = os.path.join(a.out, f"BFK-MARK-A_id{i}.png")
        cv2.imwrite(path, card)
        v = verify(card, i)
        good = v.get("detected") and abs(v["corrected_mm"] - MARKER_MM) <= TOLERANCE_MM
        ok = ok and good
        print(f"  [{'OK  ' if good else 'FAIL'}] {path}")
        print(f"          {card.shape[1]}×{card.shape[0]}px   검출 변 {v.get('side_mm')}mm"
              f"   보정 후 {v.get('corrected_mm')}mm   왜곡 {v.get('skew')}")

    if a.sheet:
        p = os.path.join(a.out, "BFK-MARK-A_A4_sheet.png")
        cv2.imwrite(p, sheet(cards))
        print(f"\n  [OK  ] {p}   A4 인쇄 시트")

    print("\n  인쇄 시 '실제 크기 100%'. 인쇄 후 카드의 20mm 눈금을 자로 검증하세요.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
