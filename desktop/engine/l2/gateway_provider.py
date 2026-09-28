"""사내 KFA AI 중계 서버 공급자.

Anthropic API 키는 진단 PC로 내려오지 않는다. 진단 PC는 사내 서버 주소와
회전 가능한 접근 토큰만 알고, 사내 서버가 비밀 저장소의 공급자 키를 사용한다.
응답은 여전히 :mod:`engine.l2.judge`의 fail-only 안전장치를 통과하므로
서버나 모델이 어떤 값을 보내도 AI가 ``부적합``을 확정할 수 없다.
"""

from __future__ import annotations

import base64
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..verdict import Verdict
from .provider import ALLOWED, Judgment, JudgeRequest, Provider

MAX_MEDIA = 6
MAX_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 18 * 1024 * 1024
IMAGE_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


class GatewayCallFailed(RuntimeError):
    """중계 서버 호출 실패. 판정으로 바꾸지 않고 미판정으로 남긴다."""


class GatewayProvider(Provider):
    name = "internal-gateway"
    # 사내 서버 뒤에서 Anthropic으로 전달되므로 외부 전송 확인은 계속 필요하다.
    sends_media_externally = True

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = (url or os.environ.get("KFA_GATEWAY_URL") or "").strip().rstrip("/")
        self.token = (token or os.environ.get("KFA_GATEWAY_TOKEN") or "").strip()
        self.timeout = float(os.environ.get("KFA_GATEWAY_TIMEOUT") or 120)

    def available(self) -> tuple[bool, str]:
        if not self.url:
            return False, "KFA_GATEWAY_URL이 설정되지 않았습니다."
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False, "사내 AI 서버 주소가 올바르지 않습니다."
        if parsed.scheme != "https" and not _allow_http(parsed.hostname or ""):
            return False, "사내 AI 서버는 HTTPS여야 합니다. 개발용 localhost만 HTTP를 허용합니다."
        if not self.token:
            return False, "KFA_GATEWAY_TOKEN이 설정되지 않았습니다."
        if len(self.token) > 512 or any(c in self.token for c in "\r\n"):
            return False, "사내 AI 서버 접근 토큰 형식이 올바르지 않습니다."
        return True, f"사내 AI 서버 {parsed.hostname}"

    def judge(self, req: JudgeRequest) -> Judgment | None:
        ok, _ = self.available()
        if not ok:
            return None

        media: list[dict[str, str]] = []
        total = 0
        used: list[str] = []
        for path in req.media:
            mime = IMAGE_SUFFIX.get(path.suffix.lower())
            if mime is None:
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if not raw or len(raw) > MAX_BYTES or total + len(raw) > MAX_TOTAL_BYTES:
                continue
            total += len(raw)
            used.append(path.name)
            media.append({"name": path.name, "mime": mime,
                          "data": base64.b64encode(raw).decode("ascii")})
            if len(media) >= MAX_MEDIA:
                break
        if not media:
            return None

        body = json.dumps({"rule_id": req.rule_id, "media": media},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/v1/judge", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "KFA-desktop/0.5"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise GatewayCallFailed(f"사내 AI 서버 HTTP {response.status}")
                raw_response = response.read(1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise GatewayCallFailed(f"사내 AI 서버 HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GatewayCallFailed(f"사내 AI 서버 연결 실패: {type(exc).__name__}") from exc
        if len(raw_response) > 1024 * 1024:
            raise GatewayCallFailed("사내 AI 서버 응답이 너무 큽니다.")
        try:
            data = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayCallFailed("사내 AI 서버 응답 형식이 잘못되었습니다.") from exc
        return _judgment(data, used)


def _allow_http(host: str) -> bool:
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    return os.environ.get("KFA_GATEWAY_ALLOW_HTTP") == "1"


def _judgment(data: object, used: list[str]) -> Judgment | None:
    """중계 서버 응답을 엄격히 검증한다. 모르는 값은 추측하지 않는다."""
    if not isinstance(data, dict):
        return None
    raw = str(data.get("verdict", "")).strip()
    if raw not in ALLOWED or ALLOWED[raw] is None:
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return None
    rationale = " ".join(str(data.get("rationale", "")).split())
    cited = data.get("cited")
    if not rationale or not isinstance(cited, list) or not cited:
        return None
    if any(not isinstance(name, str) or name not in used for name in cited):
        return None
    provider = " ".join(str(data.get("provider", "internal-gateway")).split())[:120]
    return Judgment(verdict=ALLOWED[raw], confidence=confidence,
                    rationale=rationale, cited=list(dict.fromkeys(cited)),
                    provider=provider or "internal-gateway")

