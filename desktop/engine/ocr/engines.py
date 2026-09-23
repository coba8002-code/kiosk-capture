"""실제 OCR 엔진 어댑터.

엔진마다 결과 형식이 다르다. 여기서 전부 TextLine 으로 통일한다.
**설치돼 있지 않으면 available() 이 False 와 이유를 돌려주고 끝난다** —
import 실패를 빈 결과로 바꾸지 않는다.

상자 규약(box_convention)을 엔진마다 적어 둔 이유
    문자 높이 판정은 상자가 무엇을 감싸느냐에 통째로 달려 있다.
    같은 화면을 읽어도 잉크 기준 엔진과 행 기준 엔진은 높이가 20~30% 다르다.
    그래서 규약을 기록해 두고, tools/calibrate_ocr.py 가 실제 계수를 잰다.
"""

from __future__ import annotations

import os
import sys

from .provider import MAX_LINES, MIN_TEXT_CONFIDENCE, OcrResult, Provider, TextLine

# 실행 파일 안에서는 OCR 엔진을 쓸 수 없다.
#
# PyInstaller 가 rapidocr 을 코드만 끌어가고 모델·config.yaml 은 두고 온다.
# 그러면 available() 은 '사용 가능'이라 하고 read() 는
#   FileExistsError: rapidocr_onnxruntime/config.yaml does not exist!
# 로 죽는다 — 실제로 그렇게 진단이 통째로 멈췄다.
#
# 반만 들어간 엔진을 들고 있느니 없다고 말하는 편이 낫다.
# OCR 이 필요하면 소스로 실행한다:  python kfa.py ingest ...
FROZEN = getattr(sys, "frozen", False)
FROZEN_NOTE = (
    "실행 파일에는 OCR 엔진을 넣지 않습니다(모델 파일이 함께 들어가지 않아 "
    "실행 중에 깨집니다). OCR 이 필요하면 소스로 실행하세요:  python kfa.py ingest ..."
)


def _quad(box) -> list[tuple[float, float]]:
    """엔진이 준 상자를 4점 좌표로 맞춘다."""
    pts = [(float(p[0]), float(p[1])) for p in box]
    if len(pts) >= 4:
        return pts[:4]
    if len(pts) == 2:                      # (x0,y0),(x1,y1) 형식
        (x0, y0), (x1, y1) = pts
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    raise ValueError("상자 좌표를 해석할 수 없습니다")


class RapidOcrLegacyProvider(Provider):
    """구 rapidocr-onnxruntime 패키지.

    딸려오는 모델이 ch_PP-OCRv3(중국어·영문)뿐이라 **한글을 읽지 못한다.**
    새로 쓸 이유는 없고, 이미 깔려 있는 환경을 위해 남겨 둔다.
    """

    name = "rapidocr-legacy"
    box_convention = "잉크 근사 · 중/영문 전용 (한글 불가)"

    def __init__(self) -> None:
        self._engine = None

    def available(self) -> tuple[bool, str]:
        if FROZEN:
            return False, FROZEN_NOTE
        try:
            import rapidocr_onnxruntime                        # noqa: F401
        except ImportError:
            return False, "rapidocr-onnxruntime 가 없습니다.  pip install rapidocr-onnxruntime"
        # import 만으로는 부족하다 — 모델을 실제로 올려 봐야 안다.
        try:
            self._get_engine()
        except Exception as exc:                               # noqa: BLE001
            return False, f"RapidOCR 을 올리지 못했습니다: {type(exc).__name__}"
        return True, "RapidOCR (ONNX)"

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        return self._engine

    def read(self, image_bgr) -> OcrResult:
        ok, why = self.available()
        if not ok:
            return OcrResult(engine=self.name, note=why)
        raw, _ = self._get_engine()(image_bgr)
        lines: list[TextLine] = []
        for item in (raw or [])[:MAX_LINES]:
            box, text, conf = item[0], item[1], float(item[2])
            if conf < MIN_TEXT_CONFIDENCE or not str(text).strip():
                continue
            lines.append(TextLine(text=str(text), box_px=_quad(box), confidence=conf))
        return OcrResult(lines=lines, engine=self.name)


class RapidOcrProvider(Provider):
    """RapidOCR 3.x — **한글을 읽는다.**

    인식 모델을 PP-OCRv5 korean 으로 잡는다. 검출 모델은 기본값(PP-OCRv6 small)을
    그대로 쓴다 — v5 검출기로 바꿔 봤더니 한글 문구를 글자 단위로 조각내
    '주문하기'가 '우' 하나로 남았다. 조합을 함부로 맞추면 오히려 나빠진다.

    모델은 최초 1회 ModelScope 에서 내려받아 패키지 폴더에 저장된다(약 18MB).
    그 뒤로는 네트워크 없이 돈다.
    """

    name = "rapidocr"
    box_convention = "잉크보다 큰 상자 — 교정 계수 0.8 대"

    #: 인식 모델을 정하는 값들. 상자 규약이 여기에 달려 있어 교정 키에 넣는다.
    REC_VERSION = "PP-OCRv5"
    DEFAULT_LANG = "korean"

    def __init__(self, lang: str | None = None) -> None:
        self.lang = (lang or os.environ.get("KFA_OCR_LANG") or self.DEFAULT_LANG).lower()
        self._engine = None

    @property
    def calibration_key(self) -> str:
        return f"{self.name}:{self.REC_VERSION}:{self.lang}"

    def available(self) -> tuple[bool, str]:
        if FROZEN:
            return False, FROZEN_NOTE
        try:
            import rapidocr                                    # noqa: F401
        except ImportError:
            return False, "rapidocr 가 없습니다.  pip install rapidocr"
        # import 만으로는 부족하다 — 모델을 실제로 올려 봐야 안다.
        # 최초 1회는 모델을 내려받으므로 시간이 걸린다.
        try:
            self._get_engine()
        except Exception as exc:                               # noqa: BLE001
            return False, (f"RapidOCR 을 올리지 못했습니다({type(exc).__name__}). "
                           "최초 1회는 모델 내려받기에 네트워크가 필요합니다.")
        return True, f"RapidOCR 3.x · {self.REC_VERSION} {self.lang}"

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
            try:
                lang = LangRec(self.lang)
            except ValueError:
                lang = LangRec.KOREAN
            self._engine = RapidOCR(params={
                "Rec.lang_type": lang,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            })
        return self._engine

    def read(self, image_bgr) -> OcrResult:
        ok, why = self.available()
        if not ok:
            return OcrResult(engine=self.name, note=why)

        out = self._get_engine()(image_bgr)
        if out is None or out.boxes is None or out.txts is None:
            return OcrResult(engine=self.name)

        lines: list[TextLine] = []
        scores = out.scores or ()
        for i, (box, text) in enumerate(zip(out.boxes, out.txts)):
            conf = float(scores[i]) if i < len(scores) else 1.0
            if conf < MIN_TEXT_CONFIDENCE or not str(text).strip():
                continue
            lines.append(TextLine(text=str(text), box_px=_quad(box), confidence=conf))
            if len(lines) >= MAX_LINES:
                break
        return OcrResult(lines=lines, engine=self.name)


class PaddleOcrProvider(Provider):
    """PaddleOCR. 한글 정확도가 높지만 설치가 무겁다."""

    name = "paddleocr"
    box_convention = "잉크 근사 (교정 필요)"

    def __init__(self) -> None:
        self._engine = None

    def available(self) -> tuple[bool, str]:
        if FROZEN:
            return False, FROZEN_NOTE
        try:
            import paddleocr                                   # noqa: F401
        except ImportError:
            return False, "paddleocr 가 없습니다.  pip install paddleocr paddlepaddle"
        return True, "PaddleOCR (korean)"

    def read(self, image_bgr) -> OcrResult:
        ok, why = self.available()
        if not ok:
            return OcrResult(engine=self.name, note=why)
        if self._engine is None:
            from paddleocr import PaddleOCR
            self._engine = PaddleOCR(use_angle_cls=True, lang="korean", show_log=False)

        raw = self._engine.ocr(image_bgr, cls=True)
        lines: list[TextLine] = []
        for page in raw or []:
            for box, (text, conf) in (page or [])[:MAX_LINES]:
                if float(conf) < MIN_TEXT_CONFIDENCE or not str(text).strip():
                    continue
                lines.append(TextLine(text=str(text), box_px=_quad(box),
                                      confidence=float(conf)))
        return OcrResult(lines=lines, engine=self.name)


class TesseractProvider(Provider):
    """Tesseract. 한글 정확도가 낮아 마지막 선택지다.

    상자 규약이 다른 엔진과 확연히 다르다 — 행 높이 기준이라 잉크보다 크게 나온다.
    교정 없이 쓰면 문자 높이를 과대평가해 **위반을 놓친다.**
    """

    name = "tesseract"
    box_convention = "행 높이 기준 (잉크보다 큼 — 교정 필수)"

    def available(self) -> tuple[bool, str]:
        if FROZEN:
            return False, FROZEN_NOTE
        try:
            import pytesseract
        except ImportError:
            return False, "pytesseract 가 없습니다.  pip install pytesseract"
        try:
            pytesseract.get_tesseract_version()
        except Exception:                                      # noqa: BLE001
            return False, "tesseract 실행파일을 찾을 수 없습니다 (별도 설치 필요)"
        return True, "Tesseract"

    def read(self, image_bgr) -> OcrResult:
        ok, why = self.available()
        if not ok:
            return OcrResult(engine=self.name, note=why)
        import pytesseract

        data = pytesseract.image_to_data(
            image_bgr, lang="kor+eng", output_type=pytesseract.Output.DICT)
        lines: list[TextLine] = []
        for i, text in enumerate(data["text"]):
            if not str(text).strip():
                continue
            try:
                conf = float(data["conf"][i]) / 100.0
            except (TypeError, ValueError):
                continue
            if conf < MIN_TEXT_CONFIDENCE:
                continue
            x, y = data["left"][i], data["top"][i]
            w, h = data["width"][i], data["height"][i]
            lines.append(TextLine(
                text=str(text),
                box_px=[(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                confidence=conf,
            ))
            if len(lines) >= MAX_LINES:
                break
        return OcrResult(lines=lines, engine=self.name)
