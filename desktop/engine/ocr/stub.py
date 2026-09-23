"""시험용 OCR 공급자 — 엔진 설치 없이 3.g 배선을 끝까지 돌려 보기 위한 것.

엔진이 없다는 이유로 배선을 검사하지 못하면, '설치하면 되겠지' 상태로 코드가 썩는다.
그래서 **글자를 읽지 않고 문자 영역 검출 결과를 그대로 문구로 돌려주는** 공급자를 둔다.

이 공급자는 자종을 모른다. 그래서 가장 보수적인 자종(계수가 가장 작은 것)을 붙인다 —
문자 높이를 과소평가해 검토자에게 넘길지언정, 과대평가해 위반을 놓치지 않기 위해서다.
진단에 쓰면 안 되고, 그래서 리포트에 공급자 이름이 그대로 찍힌다.
"""

from __future__ import annotations

from ..calc.text_height import CAP_RATIO
from ..detect import text_regions
from .provider import MAX_LINES, OcrResult, Provider, TextLine

# 계수가 가장 작은 자종을 대표로 쓴다 (문자 높이를 낮게 잡는 쪽).
_SAFEST_SCRIPT = min(CAP_RATIO, key=CAP_RATIO.get)

# classify_script 가 이 자종으로 분류하도록 만드는 대표 문자열
_SCRIPT_SAMPLE = {
    "latin_caps": "ABC", "latin_mixed": "Ability", "digits": "1234",
    "hangul": "가나다", "mixed": "가A",
}


class StubProvider(Provider):
    """문자 영역만 알고 내용은 모르는 공급자."""

    name = "stub(시험용)"
    box_convention = f"MSER 문자 영역 · 자종 미상({_SAFEST_SCRIPT} 로 간주)"

    def available(self) -> tuple[bool, str]:
        return True, (
            "시험용 공급자입니다. 글자를 읽지 않으므로 진단에 쓰지 마십시오. "
            f"자종을 모른 채 가장 보수적인 계수({_SAFEST_SCRIPT})를 씁니다."
        )

    def read(self, image_bgr) -> OcrResult:
        det = text_regions.detect(image_bgr, max_blocks=MAX_LINES)
        sample = _SCRIPT_SAMPLE[_SAFEST_SCRIPT]
        lines = [
            TextLine(
                text=sample,
                box_px=[(b.x, b.y), (b.x + b.w, b.y),
                        (b.x + b.w, b.y + b.h), (b.x, b.y + b.h)],
                confidence=0.6,
            )
            for b in det.blocks
        ]
        return OcrResult(lines=lines, engine=self.name,
                         note="글자를 읽지 않았습니다 — 자종은 추정입니다")
