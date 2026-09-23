#!/usr/bin/env python
"""문자 높이 보정 계수 교정기 — 별표5 3.g.

    python tools/calibrate_text_height.py
    python tools/calibrate_text_height.py --emit    # engine/calc/text_height.py 에 반영할 값 출력

왜 계수가 필요한가
────────────────────────────────────────────────────────────────────
    OCR 이 돌려주는 것은 '글자 상자'이지 '문자 높이'가 아니다.
    별표5 3.g 는 "문자 높이 7.25mm 이상"을 요구하고,
    KS §5.2.3 비고 3 은 그 근거를 이렇게 밝힌다 —
    "문자 높이 기준은 시력 검사에 사용되는 란돌트 고리(Landolt ring)의 직경을
     토대로 ... 계산한 것이다."

    란돌트 고리의 직경은 고리 그 자체의 크기다. 여백이 아니다.
    따라서 여기서 말하는 '문자 높이'는 **글자 잉크의 실제 높이**이지
    행간·어센더·디센더 여백을 포함한 상자 높이가 아니다.

측정 방법
    실제 폰트를 알려진 픽셀 크기로 렌더해, 자종별 대표 문자열의
    **잉크 바운딩 박스 높이**를 폰트의 **타이포그래픽 행 높이(ascent+descent)** 로
    나눈다. 이 비율이 곧 보정 계수다. 추정이 아니라 계측이다.

남는 불확실성 — 정직하게 분리한다
    이 계수는 '폰트 메트릭' 성분만 교정한다. 실제 파이프라인에서 상자를 주는 것은
    OCR 엔진이고, 엔진마다 상자 규약이 다르다(행 높이 기준 / 잉크 기준 / 그 중간).
    그래서 text_height.py 는 계수를 두 개로 쪼개 둔다.

        CAP_RATIO       폰트 메트릭 성분 — 이 스크립트가 교정한다 (측정값)
        OCR_BOX_FACTOR  OCR 엔진 상자 규약 성분 — 엔진 확정 후 현장 표본으로 교정

    전자만 교정된 상태에서는 확신도 상한이 여전히 눌려 검토자 큐로 간다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir  # noqa: E402

# 자종별 대표 문자열 — 각 자종에서 '가장 큰 잉크 높이'를 만드는 조합
SAMPLES: dict[str, list[str]] = {
    "hangul":      ["주문하기", "결제 수단을 선택하세요", "확인", "영수증 출력", "취소"],
    "latin_caps":  ["ORDER", "CONFIRM", "PAY", "RECEIPT", "CANCEL"],
    "latin_mixed": ["Order now", "Confirm payment", "Tap to begin", "Print receipt"],
    "digits":      ["0123456789", "4,500", "1,220", "7.25"],
    "mixed":       ["주문 Order", "결제 PAY 4,500원", "확인 OK"],
}

# 후보 폰트 — 키오스크에서 실제로 흔한 계열부터
FONT_CANDIDATES = [
    "malgun.ttf",        # 맑은 고딕 (Windows 기본, 국내 키오스크 다수)
    "malgunbd.ttf",
    "NotoSansKR-Regular.ttf",
    "NanumGothic.ttf",
    "AppleSDGothicNeo.ttc",
]
FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"),
    Path("/System/Library/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
]

RENDER_PX = 200      # 충분히 크게 렌더해 래스터 오차를 줄인다


def find_font() -> Path | None:
    for d in FONT_DIRS:
        if not d.exists():
            continue
        for name in FONT_CANDIDATES:
            p = d / name
            if p.exists():
                return p
        for p in d.rglob("*.ttf"):
            if "malgun" in p.name.lower() or "notosanskr" in p.name.lower().replace("-", ""):
                return p
    return None


def measure(font_path: Path, size_px: int = RENDER_PX) -> dict[str, dict[str, float]]:
    """자종별 잉크 높이 / 행 높이 비율을 측정한다."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), size_px)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent

    out: dict[str, dict[str, float]] = {}
    for script, samples in SAMPLES.items():
        ratios, ink_heights = [], []
        for text in samples:
            # 잉크 바운딩 박스 — 실제로 칠해진 픽셀의 범위
            img = Image.new("L", (size_px * len(text) + size_px, size_px * 3), 255)
            d = ImageDraw.Draw(img)
            d.text((size_px // 2, size_px // 2), text, font=font, fill=0)
            bbox = img.point(lambda v: 255 if v < 128 else 0).getbbox()
            if bbox is None:
                continue
            ink_h = bbox[3] - bbox[1]
            ink_heights.append(ink_h)
            ratios.append(ink_h / line_h)

        if not ratios:
            continue
        out[script] = {
            "ratio_mean": sum(ratios) / len(ratios),
            "ratio_max": max(ratios),
            "ratio_min": min(ratios),
            "ink_px_mean": sum(ink_heights) / len(ink_heights),
            "line_px": float(line_h),
            "n": len(ratios),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="문자 높이 보정 계수 교정")
    ap.add_argument("--font", help="폰트 파일 경로 (미지정 시 자동 탐색)")
    ap.add_argument("--emit", action="store_true", help="text_height.py 에 넣을 dict 출력")
    a = ap.parse_args()

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow 가 필요합니다:  pip install Pillow")
        return 2

    font_path = Path(a.font) if a.font else find_font()
    if not font_path or not font_path.exists():
        print("한글 폰트를 찾지 못했습니다. --font 로 경로를 지정하세요.")
        return 2

    print("=" * 74)
    print(" 문자 높이 보정 계수 교정 — 별표5 3.g")
    print("=" * 74)
    print(f"\n  폰트      {font_path.name}")
    print(f"  렌더 크기  {RENDER_PX}px")
    print("  정의      문자 높이 = 잉크 실제 높이 (KS §5.2.3 비고 3, 란돌트 고리 직경 기준)")
    print("  비율      잉크 높이 ÷ 타이포그래픽 행 높이(ascent+descent)\n")

    res = measure(font_path)

    print(f"  {'자종':<14}{'표본':>5}{'평균 비율':>11}{'최소':>9}{'최대':>9}   비고")
    print("  " + "-" * 70)
    labels = {
        "hangul": "한글", "latin_caps": "라틴 대문자", "latin_mixed": "라틴 혼용",
        "digits": "숫자", "mixed": "한글+라틴",
    }
    for k, v in res.items():
        spread = v["ratio_max"] - v["ratio_min"]
        note = "안정" if spread < 0.03 else f"산포 {spread:.3f}"
        print(f"  {labels[k]:<14}{v['n']:>5}{v['ratio_mean']:>11.4f}"
              f"{v['ratio_min']:>9.4f}{v['ratio_max']:>9.4f}   {note}")

    print("\n  판정에는 **최대 비율**을 쓴다 — 계수가 크면 문자 높이를 크게 추정해")
    print("  위반을 놓치므로, 보수적으로 가려면 오히려 작은 값이 안전하다.")
    print("  그러나 3.g 는 ai_verdict_scope: fail_only 라 AI가 적합을 확정하지 않으므로,")
    print("  놓침(false pass)을 막는 쪽 = **최소 비율**을 채택한다.\n")

    if a.emit:
        print("  # engine/calc/text_height.py 에 반영할 값")
        print(f"  # 교정 폰트: {font_path.name}, 렌더 {RENDER_PX}px")
        print("  CAP_RATIO: dict[str, float] = {")
        for k, v in res.items():
            print(f'      "{k}": {v["ratio_min"]:.3f},'
                  f'   # 평균 {v["ratio_mean"]:.3f}, 최대 {v["ratio_max"]:.3f}')
        print("  }")
        print("  CALIBRATED = True")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
