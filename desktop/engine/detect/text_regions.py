"""문자 영역 검출 — 명도 대비(3.i·3.j)를 자동 판정하기 위한 전처리.

왜 필요한가
────────────────────────────────────────────────────────────────────
    3.i 는 "필수적인 문자와 배경 간 4.5:1" 을 요구한다.
    대비를 계산하려면 **어디가 문자인지** 먼저 알아야 한다.
    사람이 화면마다 문구를 손으로 지정하게 하면 진단이 자동화되지 않는다.

    OCR 이 있으면 글자 상자를 그대로 받으면 되지만, 지금 파이프라인에 OCR 이
    없다. 그런데 대비 계산에는 **글자를 읽을 필요가 없다** — 어디가 글자인지만
    알면 된다. 그래서 MSER(Maximally Stable Extremal Regions)로 글자처럼 생긴
    덩어리를 찾고, 같은 줄에 놓인 것들을 묶어 문구 블록을 만든다.

무엇을 보장하고 무엇을 보장하지 않는가
    보장    검출된 블록 안의 전경·배경 대비는 정확히 계산된다.
    비보장  화면의 **모든** 문구를 빠짐없이 찾는다는 보장은 없다.
            따라서 "검출된 것 중 최악"만 말할 수 있고,
            '적합'을 확정하려면 놓친 문구가 없어야 한다.

    이 비대칭이 판정에 그대로 반영된다 — 위반을 찾으면 확실하지만,
    못 찾았다고 적합인 것은 아니다. coverage 를 함께 돌려주는 이유다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 글자 덩어리로 인정할 조건 — 화면 해상도에 무관하도록 상대값으로 둔다
MIN_AREA_RATIO = 2e-6        # 프레임 대비 최소 면적
MAX_AREA_RATIO = 0.02        # 이보다 크면 글자가 아니라 배경 판/사진이다
MIN_ASPECT, MAX_ASPECT = 0.08, 12.0
MAX_FILL_RATIO = 0.92        # 꽉 찬 사각형은 글자가 아니라 버튼·아이콘일 확률이 높다

# 같은 줄로 묶는 기준
LINE_Y_TOLERANCE = 0.6       # 문자 높이 대비 세로 중심 차이
LINE_X_GAP = 2.2             # 문자 높이 대비 가로 간격
MIN_GLYPHS_PER_LINE = 2      # 한 덩어리만 있으면 글자인지 확신할 수 없다


@dataclass
class TextBlock:
    """한 줄로 묶인 문구 블록 (이미지 픽셀 좌표)."""

    x: int
    y: int
    w: int
    h: int
    glyphs: int
    def as_box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


@dataclass
class DetectionResult:
    blocks: list[TextBlock] = field(default_factory=list)
    glyph_candidates: int = 0
    coverage_note: str = ""
    #: 이 사진에서 나온 결과를 판정 근거로 써도 되는가.
    #: 잡음이 심해 검출을 포기한 경우 False 이고, 그때는 블록이 비어 있다.
    reliable: bool = True

    def __len__(self) -> int:
        return len(self.blocks)


def detect(image_bgr, *, max_blocks: int = 40, exclude=None) -> DetectionResult:
    """화면 이미지에서 문구 블록을 찾는다.

    Args:
        image_bgr: OpenCV BGR 이미지
        max_blocks: 큰 것부터 이 개수까지만 돌려준다 (판정 시간 제한)
        exclude: 제외할 영역 목록 [(x, y, w, h), ...].
                 **기준 마커 영역을 반드시 넣어야 한다.** 마커 카드에는
                 'BFK-MARK-A' 같은 회색 작은 글씨가 인쇄돼 있어서, 빼지 않으면
                 우리가 붙인 카드 때문에 1.8:1 짜리 '위반'이 잡힌다.
                 진단 대상은 키오스크 화면이지 우리 카드가 아니다.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError("문자 영역 검출에는 opencv-python 이 필요합니다") from exc

    if image_bgr is None or image_bgr.size == 0:
        return DetectionResult(coverage_note="이미지 없음")
    # MSER 은 3x3 미만에서 예외를 던진다. 잘린 사진 한 장에 진단 전체가
    # 죽으면 안 되므로, 여기서 '못 읽었다'로 돌려보낸다.
    if min(image_bgr.shape[:2]) < MIN_IMAGE_PX:
        return DetectionResult(
            coverage_note=f"이미지가 너무 작습니다 ({image_bgr.shape[1]}x{image_bgr.shape[0]}px)")

    h, w = image_bgr.shape[:2]
    frame_area = float(h * w)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    mser = cv2.MSER_create()
    mser.setMinArea(max(8, int(frame_area * MIN_AREA_RATIO)))
    mser.setMaxArea(int(frame_area * MAX_AREA_RATIO))
    regions, _ = mser.detectRegions(gray)

    # 밝은 글자(어두운 배경)도 잡으려면 반전 이미지도 본다
    regions_inv, _ = mser.detectRegions(255 - gray)

    raw = list(regions) + list(regions_inv)
    if len(raw) > MAX_GLYPH_CANDIDATES:
        return DetectionResult(
            glyph_candidates=len(raw),
            reliable=False,
            coverage_note=(
                f"글자 후보가 {len(raw):,}개로 지나치게 많습니다. "
                "잡음·질감이 많은 사진이라 문구를 신뢰성 있게 찾을 수 없습니다. "
                "조명을 밝게 하고 초점을 맞춰 다시 촬영하세요."
            ),
        )

    boxes: list[tuple[int, int, int, int]] = []
    for pts in raw:
        x, y, bw, bh = cv2.boundingRect(pts.reshape(-1, 1, 2))
        if bh == 0 or bw == 0:
            continue
        aspect = bw / bh
        if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
            continue
        if len(pts) / float(bw * bh) > MAX_FILL_RATIO:
            continue
        if exclude and any(_overlaps((x, y, bw, bh), e) for e in exclude):
            continue
        boxes.append((x, y, bw, bh))

    boxes = _dedupe(boxes)
    lines = _group_lines(boxes)

    blocks = [b for b in lines if b.glyphs >= MIN_GLYPHS_PER_LINE]
    blocks.sort(key=lambda b: b.w * b.h, reverse=True)

    note = (
        "MSER 로 찾은 문구 블록입니다. 화면의 모든 문구를 빠짐없이 찾는다는 "
        "보장은 없으므로, 위반 검출에는 쓰되 '적합' 확정 근거로는 쓰지 않습니다."
    )
    return DetectionResult(
        blocks=blocks[:max_blocks],
        glyph_candidates=len(boxes),
        coverage_note=note,
    )


def _dedupe(boxes, iou_thresh: float = 0.6):
    """겹치는 상자를 하나로 — MSER 은 같은 글자를 여러 번 낸다.

    남은 상자 전체와 비교하면 O(n²) 이고, 후보가 수천 개가 되는 순간
    진단이 몇 분씩 멈춘다(실측 4만 개 → 108초). 겹치려면 가까이 있어야 하므로
    공간 격자에 담아 **이웃 칸만** 본다.
    """
    if not boxes:
        return []

    ordered = sorted(boxes, key=lambda t: t[2] * t[3], reverse=True)
    cell = max(8, int(sum(max(b[2], b[3]) for b in ordered) / len(ordered)))

    grid: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
    out: list[tuple[int, int, int, int]] = []
    for b in ordered:
        gx, gy = b[0] // cell, b[1] // cell
        clash = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in grid.get((gx + dx, gy + dy), ()):
                    if _iou(b, o) >= iou_thresh:
                        clash = True
                        break
                if clash:
                    break
            if clash:
                break
        if not clash:
            out.append(b)
            grid.setdefault((gx, gy), []).append(b)
    return out


def _overlaps(box, region, thresh: float = 0.25) -> bool:
    """상자가 제외 영역과 이만큼 이상 겹치면 버린다."""
    bx, by, bw, bh = box
    rx, ry, rw, rh = region
    x0, y0 = max(bx, rx), max(by, ry)
    x1, y1 = min(bx + bw, rx + rw), min(by + bh, ry + rh)
    if x1 <= x0 or y1 <= y0:
        return False
    return ((x1 - x0) * (y1 - y0)) / float(bw * bh) >= thresh


# BFK-MARK-A 카드 실측 치수 — 제외 범위를 여기서 계산한다
CARD_W_MM, CARD_H_MM, MARKER_MM = 85.6, 54.0, 40.0

# MSER 이 처리할 수 있는 최소 크기. 이보다 작으면 OpenCV 가 예외를 던진다.
MIN_IMAGE_PX = 3

# 글자 후보 상한. 넘으면 이 사진은 '문구가 많은 화면'이 아니라 '잡음이 많은 사진'이다.
#
# 실측: 잡음 600x800 사진 하나가 후보 4만 개를 만들었고, 겹침 제거가
# O(n²) 라 **108초** 가 걸렸다. 현장 사진은 12MP 이고 실내 저조도면 훨씬 심하다.
# 진단 한 건이 사진 몇 장 때문에 멈추는 것이 실제 위험이었다.
#
# 넘겼을 때 잘라내지 않고 '못 읽었다'로 돌려보내는 이유:
# 4만 개 중 앞 5천 개만 쓰면 그 결과가 화면을 대표한다는 보장이 없는데,
# 리포트에는 정상 판정처럼 실린다. 조용한 오답보다 정직한 미판정이 낫다.
MAX_GLYPH_CANDIDATES = 5000
_SAFETY = 0.15               # 회전·왜곡을 감안한 여유


def marker_exclusion(corners, *, card_w_mm: float = CARD_W_MM,
                     card_h_mm: float = CARD_H_MM, marker_mm: float = MARKER_MM):
    """마커 4점에서 **카드 전체**를 덮는 제외 영역(x, y, w, h)을 만든다.

    마커 정사각형만 빼면 안 된다 — 카드에는 마커 바깥에
    'BFK-MARK-A', 'DICT_4X4_50', '20mm' 눈금 같은 **회색 작은 글씨**가 인쇄돼 있고,
    그게 1.7~1.9:1 짜리 대비 위반으로 잡힌다. 우리가 붙인 카드 때문에
    키오스크가 부적합 판정을 받는 셈이다.

    카드는 85.6×54mm, 마커는 그 중앙의 40mm 정사각이므로
    가로로 (85.6−40)/2 = 22.8mm, 세로로 (54−40)/2 = 7mm 만큼 더 넓다.
    이 비율을 마커 크기에 곱해 카드 범위를 복원한다.
    """
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    side = max(x1 - x0, y1 - y0)          # 마커 한 변의 이미지상 길이

    mx = side * ((card_w_mm - marker_mm) / 2 / marker_mm + _SAFETY)
    my = side * ((card_h_mm - marker_mm) / 2 / marker_mm + _SAFETY)
    return (int(x0 - mx), int(y0 - my), int(x1 - x0 + 2 * mx), int(y1 - y0 + 2 * my))


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / float(aw * ah + bw * bh - inter)


def _group_lines(boxes) -> list[TextBlock]:
    """세로 중심이 비슷하고 가로로 가까운 것들을 한 줄로 묶는다."""
    if not boxes:
        return []

    items = sorted(boxes, key=lambda b: (b[1] + b[3] / 2, b[0]))
    lines: list[list[tuple[int, int, int, int]]] = []

    for b in items:
        bx, by, bw, bh = b
        cy = by + bh / 2
        placed = False
        for ln in lines:
            lx, ly, lw, lh = ln[-1]
            lcy = ly + lh / 2
            ref = max(bh, lh)
            if abs(cy - lcy) <= ref * LINE_Y_TOLERANCE and (bx - (lx + lw)) <= ref * LINE_X_GAP:
                ln.append(b)
                placed = True
                break
        if not placed:
            lines.append([b])

    out: list[TextBlock] = []
    for ln in lines:
        xs0 = min(b[0] for b in ln)
        ys0 = min(b[1] for b in ln)
        xs1 = max(b[0] + b[2] for b in ln)
        ys1 = max(b[1] + b[3] for b in ln)
        out.append(TextBlock(x=xs0, y=ys0, w=xs1 - xs0, h=ys1 - ys0, glyphs=len(ln)))
    return out


def crop(image_bgr, block: TextBlock, *, pad_ratio: float = 0.35):
    """블록 주변을 조금 넓혀 잘라낸다.

    배경 픽셀이 함께 들어와야 Otsu 가 전경·배경을 가를 수 있다.
    글자에 딱 맞게 자르면 배경 표본이 부족해 임계값이 엉뚱하게 잡힌다.
    """
    h, w = image_bgr.shape[:2]
    pad_y = max(2, int(block.h * pad_ratio))
    pad_x = max(2, int(block.h * pad_ratio))
    x0 = max(0, block.x - pad_x)
    y0 = max(0, block.y - pad_y)
    x1 = min(w, block.x + block.w + pad_x)
    y1 = min(h, block.y + block.h + pad_y)
    return image_bgr[y0:y1, x0:x1]
