"""OCR 공급자 — 화면 문구를 읽는 계층의 경계.

별표5 3.g '모든 필수적인 문자는 문자 높이 7.25mm 이상'을 자동 판정하려면
두 가지가 필요하다.

    ① 문구가 화면 어디에 있는가  → engine/detect/text_regions.py (MSER, 이미 있음)
    ② 그게 무슨 글자인가          → 여기

②가 왜 필요한가. 문자 높이는 상자 높이가 아니다.
'x' 만 있는 단어와 'Hjq' 가 섞인 단어는 상자 높이가 같아도 문자 높이가 다르다.
한글은 네모틀에 꽉 차고 라틴 소문자는 그렇지 않다.
그래서 **자종을 알아야 상자 높이를 문자 높이로 바꿀 수 있다**
(engine/calc/text_height.CAP_RATIO — 실측 교정 완료).

무거운 것을 실행 파일에 넣지 않는다
    PaddleOCR 은 PyTorch 를 끌고 오고 그것만 2GB 다. 74MB 실행 파일에 넣을 수 없다.
    그래서 OCR 은 **런타임에 있는 것을 찾아 쓴다.** 없으면 없다고 말하고
    3.g 를 실측으로 넘긴다. 조용히 추측하지 않는다.

    권장
        pip install rapidocr        # 한글 지원 (PP-OCRv5 korean). torch 불필요

    한글을 읽는지는 엔진마다 다르다 — 이름만 보고 믿으면 안 된다.
    실제로 겪은 일이다: rapidocr-**onnxruntime** (구 패키지)은 설치하면 자동으로
    잡히지만 딸려오는 모델이 ch_PP-OCRv3(중국어·영문) 뿐이라
    **한글을 한 글자도 읽지 못했다.** 엔진 쪽에서 보면 '문구를 못 찾았다'와
    구별되지 않아, 한글 키오스크의 3.g 가 조용히 '위반 없음'이 될 뻔했다.

    그래서 두 가지를 한다.
      · 자동 탐지는 **한글을 읽는 엔진부터** 고른다
      · Provider.probe_korean() 으로 실제로 읽어 보고, 못 읽으면
        그 사실을 교정 기록과 리포트에 남긴다

엔진마다 상자 규약이 다르다
    어떤 엔진은 잉크 기준, 어떤 엔진은 행 높이 기준으로 상자를 준다.
    이 차이를 모르면 문자 높이가 통째로 틀어진다. 그래서 엔진을 붙인 뒤에는
    반드시 `KFA.exe calibrate-ocr` 로 상자 계수를 교정한다.
    교정 전에는 확신도에 상한이 걸려 자동으로 검토자에게 넘어간다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# 이 확신도 미만의 인식 결과는 자종 판별을 믿을 수 없으므로 버린다.
MIN_TEXT_CONFIDENCE = 0.5

# 한 화면에서 처리할 최대 줄 수. 현장 사진 한 장에 이보다 많으면 문구가 아니라 잡음이다.
MAX_LINES = 120

# 한글을 읽는지 확인하는 표본. 국내 키오스크 화면은 대부분 한글이다.
#
# 이 검사가 왜 필요한가 — 실제로 겪은 일이다.
# rapidocr-onnxruntime 을 설치하면 자동으로 잡히지만, 딸려오는 모델은
# ch_PP-OCRv3 (중국어·영문) 뿐이라 **한글을 한 글자도 읽지 못한다.**
# 그런데 엔진 쪽에서 보면 '문구를 못 찾았다'와 구별되지 않아,
# 한글 키오스크를 진단하면 3.g 가 조용히 '위반 없음'이 되어 버린다.
# 못 읽는 엔진을 못 읽는다고 말하게 하는 것이 이 상수의 일이다.
KOREAN_PROBE = ["주문하기", "결제", "확인", "취소"]


@dataclass
class TextLine:
    """인식된 문구 한 줄."""

    text: str
    box_px: list[tuple[float, float]]      # 4점 이상. 이미지 좌표
    confidence: float = 1.0

    def height_px(self) -> float:
        ys = [p[1] for p in self.box_px]
        return max(ys) - min(ys)


@dataclass
class OcrResult:
    lines: list[TextLine] = field(default_factory=list)
    engine: str = ""
    note: str = ""

    def __len__(self) -> int:
        return len(self.lines)


class Provider:
    """OCR 공급자 인터페이스."""

    name = "base"
    #: 이 엔진의 상자가 무엇을 감싸는가. 교정 전에는 "미교정".
    box_convention = "미교정"

    @property
    def calibration_key(self) -> str:
        """상자 계수를 저장·조회할 키.

        이름만 쓰면 안 된다. 같은 'rapidocr' 이라도 인식 모델이 바뀌면
        상자 규약이 달라지는데, 이름이 같다는 이유로 **예전 계수를 조용히 재사용**하면
        문자 높이가 통째로 틀어진다. 모델과 언어를 키에 넣어,
        조합이 바뀌면 교정이 없는 상태로 되돌아가게 한다.
        """
        return self.name

    def available(self) -> tuple[bool, str]:
        """(쓸 수 있는가, 못 쓰면 그 이유). 이유는 리포트에 그대로 인쇄된다."""
        return False, "구현되지 않은 공급자입니다"

    def read(self, image_bgr) -> OcrResult:
        raise NotImplementedError

    def probe_korean(self, font_path=None, size_px: int = 64) -> tuple[int, int]:
        """한글 표본을 렌더해 몇 개나 읽는지 센다.

        Returns:
            (읽은 수, 시도한 수). 0/N 이면 이 엔진은 한글을 못 읽는 것이다.
        """
        try:
            import numpy as np
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return (0, 0)
        if font_path is None:
            from tools.calibrate_text_height import find_font
            font_path = find_font()
        if font_path is None:
            return (0, 0)

        font = ImageFont.truetype(str(font_path), size_px)
        hit = 0
        for text in KOREAN_PROBE:
            img = Image.new("L", (size_px * (len(text) + 2), size_px * 3), 255)
            ImageDraw.Draw(img).text((size_px // 2, size_px // 2), text, font=font, fill=0)
            gray = np.array(img, dtype=np.uint8)
            try:
                res = self.read(np.stack([gray, gray, gray], axis=-1))
            except Exception:                                # noqa: BLE001
                continue
            if any(any("가" <= ch <= "힣" for ch in (l.text or ""))
                   for l in res.lines):
                hit += 1
        return (hit, len(KOREAN_PROBE))


class NullProvider(Provider):
    """OCR 이 없을 때의 기본값 — 아무것도 읽지 않는다.

    '읽지 못했음'을 '문구가 없음'으로 바꾸지 않는 것이 이 클래스의 일이다.
    """

    name = "없음"

    def available(self) -> tuple[bool, str]:
        import sys
        if getattr(sys, "frozen", False):
            # 실행 파일 안에서는 pip 로 깔아도 소용없다. site-packages 를 보지 않는다.
            return False, (
                "실행 파일에는 OCR 엔진이 없습니다. 3.g(문자 높이)는 실측으로 넘어갑니다.  "
                "OCR 로 자동화하려면 소스로 실행하세요:  python kfa.py ingest ..."
            )
        return False, (
            "OCR 엔진이 없습니다. 3.g(문자 높이)는 실측으로 넘어갑니다.  "
            "pip install rapidocr-onnxruntime"
        )

    def read(self, image_bgr) -> OcrResult:
        return OcrResult(engine=self.name, note="OCR 엔진 없음")


def _detect() -> str:
    """설치된 엔진을 찾는다. **한글을 읽는 것부터** 본다.

    국내 키오스크 화면은 대부분 한글이다. 가벼운 것부터 고르면
    한글을 못 읽는 엔진이 먼저 잡혀 3.g 가 통째로 무의미해진다.
    """
    import importlib.util as u
    if u.find_spec("rapidocr"):                 # 한글 모델 있음 (PP-OCRv5)
        return "rapidocr"
    if u.find_spec("paddleocr"):                # 한글 모델 있음
        return "paddleocr"
    if u.find_spec("rapidocr_onnxruntime"):     # 중/영문 전용 — 한글 못 읽음
        return "rapidocr-legacy"
    if u.find_spec("pytesseract"):
        return "tesseract"
    return ""


def resolve(name: str | None = None) -> Provider:
    """쓸 OCR 공급자를 고른다.

    L2 와 달리 **자동 탐지가 기본이다.** OCR 은 전부 로컬에서 돌고
    사진이 밖으로 나가지 않으므로, 설치돼 있으면 그냥 쓰는 게 맞다.
    (클라우드 OCR 은 이 자동 경로에 넣지 않는다 — 붙이려면 명시해야 한다.)
    """
    key = (name or os.environ.get("KFA_OCR_PROVIDER") or _detect()).strip().lower()
    if not key or key in ("none", "없음"):
        return NullProvider()
    if key in ("rapidocr", "rapid"):
        from .engines import RapidOcrProvider
        return RapidOcrProvider()
    if key in ("rapidocr-legacy", "rapidocr_onnxruntime", "legacy"):
        from .engines import RapidOcrLegacyProvider
        return RapidOcrLegacyProvider()
    if key in ("paddleocr", "paddle"):
        from .engines import PaddleOcrProvider
        return PaddleOcrProvider()
    if key in ("tesseract", "pytesseract"):
        from .engines import TesseractProvider
        return TesseractProvider()
    if key in ("stub", "test", "echo"):
        from .stub import StubProvider
        return StubProvider()
    raise ValueError(
        f"모르는 OCR 공급자입니다: {key!r} "
        "(rapidocr | rapidocr-legacy | paddleocr | tesseract)")
