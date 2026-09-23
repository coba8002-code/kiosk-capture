"""촬영 파일의 비판정 품질 검사.

여기서 만드는 값은 촬영 자료가 분석 가능한지 확인하는 품질 지표다.
휴대폰 마이크의 dBFS를 교정된 소음계의 dBA로 바꾸지 않는다.
"""

from .audio_quality import AudioQuality, from_manifest, inspect_wav

__all__ = ["AudioQuality", "from_manifest", "inspect_wav"]
