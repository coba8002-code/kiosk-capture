"""Anthropic 멀티모달 공급자.

없으면 없다고 말한다
    0.4 배포본은 anthropic SDK를 포함하지만 API 키는 절대 넣지 않는다.
    SDK나 키 중 하나라도 없으면 available() 이 False와 함께 **그 이유**를
    돌려주고, 해당 항목들은 검토자에게 넘어간다. 조용히 빈 판정이 생기지 않는다.

    켜는 법:
        KFA.exe 실행 → AI 설정 → API 키 입력 → 이번 실행에만 사용

무엇이 밖으로 나가는가
    이 공급자는 촬영물을 Anthropic API 로 보낸다. 현장 사진에는 개인정보가
    남아 있을 수 있다. 앱이 기기에서 얼굴·카드번호를 마스킹하지만 그것으로
    충분하다고 가정하지 않는다. 그래서 sends_media_externally = True 이고,
    ingest 가 실행 전에 무엇이 전송되는지 화면에 찍고 동의를 받는다.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
from dataclasses import replace
from pathlib import Path

from ..verdict import Verdict
from .provider import ALLOWED, Judgment, JudgeRequest, Provider
from .prompt import SYSTEM, build

DEFAULT_MODEL = "claude-sonnet-5"
MAX_MEDIA = 6                       # 항목 하나에 붙이는 이미지 상한
MAX_BYTES = 4 * 1024 * 1024         # 파일 하나 상한
IMAGE_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


class AnthropicProvider(Provider):
    name = "anthropic"
    sends_media_externally = True

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("KFA_L2_MODEL") or DEFAULT_MODEL
        self._client = None

    # ── 가용성 ──────────────────────────────────────────────────────────
    def available(self) -> tuple[bool, str]:
        try:
            import anthropic                                     # noqa: F401
        except ImportError:
            return False, "anthropic 패키지가 없습니다.  pip install anthropic"
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY 환경변수가 없습니다."
        return True, f"모델 {self.model}"

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    # ── 판단 ────────────────────────────────────────────────────────────
    def judge(self, req: JudgeRequest) -> Judgment | None:
        ok, why = self.available()
        if not ok:
            return None

        blocks: list[dict] = []
        used: list[str] = []
        used_paths: list[Path] = []
        for path in req.media:
            block = _image_block(path)
            if block is None:
                continue
            blocks.append({"type": "text", "text": f"[촬영 파일명] {path.name}"})
            blocks.append(block)
            used.append(path.name)
            used_paths.append(path)
            if len(used) >= MAX_MEDIA:
                break
        if not blocks:
            return None          # 볼 것이 없으면 판단하지 않는다

        blocks.append({"type": "text", "text": build(replace(req, media=used_paths))})

        try:
            resp = self._get_client().messages.create(
                model=self.model,
                max_tokens=600,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
            )
        except Exception as exc:                                 # noqa: BLE001
            # 네트워크·인증 실패를 판정으로 바꾸지 않는다. 못 했으면 못 한 것이다.
            raise L2CallFailed(f"{type(exc).__name__}: {exc}") from exc

        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return parse(text, provider=f"{self.name}/{self.model}", fallback_cited=used)


class L2CallFailed(RuntimeError):
    """공급자 호출이 실패했다. 미판정으로 남기고 이유를 리포트에 적는다."""


def _image_block(path: Path) -> dict | None:
    mime = IMAGE_SUFFIX.get(path.suffix.lower())
    if mime is None:
        return None                      # 영상·음성은 이 공급자가 다루지 않는다
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_BYTES:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime,
                   "data": base64.b64encode(raw).decode("ascii")},
    }


def parse(text: str, *, provider: str, fallback_cited: list[str]) -> Judgment | None:
    """모델 응답을 Judgment 로 바꾼다.

    파싱에 실패하면 None 이다. **추측해서 채우지 않는다** —
    형식이 깨진 응답을 억지로 해석하면 그 순간 리포트가 거짓말을 시작한다.
    """
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    raw = str(data.get("verdict", "")).strip()
    if raw not in ALLOWED:
        return None
    verdict = ALLOWED[raw]
    if verdict is None:                  # 판단불가 — 모델이 정직하게 물러선 경우
        return None

    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(conf) or not 0 <= conf <= 1:
        return None

    rationale = " ".join(str(data.get("rationale", "")).split())
    if not rationale:
        return None

    supplied = data.get("cited")
    if not isinstance(supplied, list) or not supplied:
        return None
    if any(not isinstance(c, str) or c not in fallback_cited for c in supplied):
        return None
    cited = list(dict.fromkeys(supplied))
    return Judgment(verdict=verdict, confidence=conf, rationale=rationale,
                    cited=cited, provider=provider)
