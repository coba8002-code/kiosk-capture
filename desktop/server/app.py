"""KFA 사내 AI 중계 서버.

공급자 API 키는 이 서버의 Docker secret으로만 읽는다. 요청 이미지와 모델 응답은
디스크나 로그에 기록하지 않는다. 이 서버의 응답도 KFA 데스크톱의 fail-only
안전장치를 다시 통과하므로 AI가 최종 부적합을 확정할 수 없다.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine import rules
from engine.l2.anthropic_provider import parse
from engine.l2.prompt import SYSTEM, request_from_rule, build

MAX_MEDIA = 6
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 18 * 1024 * 1024
MAX_BODY_BYTES = 26 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
RULE_ID = re.compile(r"^(?:[1-9]|10)\.[a-z]$")
LOG = logging.getLogger("kfa.gateway")


def _secret(name: str) -> str:
    """Docker secret 파일을 우선하고, 운영체제 환경변수는 개발용으로 허용한다."""
    file_name = os.environ.get(f"{name}_FILE", "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"{name} secret 파일을 읽을 수 없습니다.") from exc
    return os.environ.get(name, "").strip()


class Media(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=180)
    mime: str
    data: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if Path(value).name != value or any(c in value for c in "\r\n\x00"):
            raise ValueError("파일명 형식이 올바르지 않습니다.")
        return value

    @field_validator("mime")
    @classmethod
    def known_mime(cls, value: str) -> str:
        if value not in ALLOWED_MIME:
            raise ValueError("지원하지 않는 이미지 형식입니다.")
        return value


class JudgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    media: list[Media] = Field(min_length=1, max_length=MAX_MEDIA)

    @field_validator("rule_id")
    @classmethod
    def known_id_shape(cls, value: str) -> str:
        if not RULE_ID.fullmatch(value):
            raise ValueError("항목 ID 형식이 올바르지 않습니다.")
        return value


class JudgeOutput(BaseModel):
    verdict: str
    confidence: float
    rationale: str
    cited: list[str]
    provider: str
    request_id: str


class RateWindow:
    """우발적인 비용 폭주 방지. 다중 서버에서는 앞단 프록시 제한도 함께 둔다."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.items: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self.lock:
            q = self.items[key]
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


ACCESS_TOKEN = _secret("KFA_GATEWAY_TOKEN")
API_KEY = _secret("ANTHROPIC_API_KEY")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5").strip()
MAX_RPM = int(os.environ.get("KFA_MAX_REQUESTS_PER_MINUTE", "30"))
CONCURRENCY = max(1, int(os.environ.get("KFA_MAX_CONCURRENCY", "4")))
RATE = RateWindow(MAX_RPM)
SLOTS = asyncio.Semaphore(CONCURRENCY)
RULES = rules.load()
AREAS = {item["id"]: cat["name"] for cat in RULES.categories for item in cat["items"]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(ACCESS_TOKEN) < 32:
        raise RuntimeError("KFA_GATEWAY_TOKEN은 32자 이상의 무작위 값이어야 합니다.")
    if not API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY secret이 없습니다.")
    app.state.client = anthropic.AsyncAnthropic(api_key=API_KEY)
    yield
    await app.state.client.close()


app = FastAPI(title="KFA Internal AI Gateway", version="0.5.0",
              docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                return _error(413, "요청이 너무 큽니다.")
        except ValueError:
            return _error(400, "Content-Length가 올바르지 않습니다.")
    # 이 API는 데스크톱 프로그램 전용이다. 브라우저 호출을 허용하지 않는다.
    if request.headers.get("origin"):
        return _error(403, "브라우저 호출은 허용하지 않습니다.")
    return await call_next(request)


def _error(status: int, message: str):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": message}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def authorized(authorization: str = Header(default="")) -> None:
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization.startswith(prefix) else ""
    if not supplied or not hmac.compare_digest(supplied, ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="인증 실패")


@app.get("/health")
async def health():
    return {"ok": True, "service": "kfa-ai-gateway", "version": "0.5.0"}


@app.post("/v1/judge", response_model=JudgeOutput, dependencies=[Depends(authorized)])
async def judge(payload: JudgeInput, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not await RATE.allow(client_ip):
        raise HTTPException(status_code=429, detail="요청이 너무 많습니다.")
    try:
        rule = RULES[payload.rule_id]
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="알 수 없는 항목입니다.") from None

    blocks: list[dict] = []
    names: list[str] = []
    total = 0
    for item in payload.media:
        try:
            raw = base64.b64decode(item.data, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="이미지 인코딩이 잘못되었습니다.") from None
        if not raw or len(raw) > MAX_IMAGE_BYTES or total + len(raw) > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="이미지 용량 제한을 초과했습니다.")
        total += len(raw)
        names.append(item.name)
        blocks += [
            {"type": "text", "text": f"[촬영 파일명] {item.name}"},
            {"type": "image", "source": {"type": "base64",
                                           "media_type": item.mime,
                                           "data": item.data}},
        ]

    req = request_from_rule(rule, AREAS.get(rule["id"], ""),
                            [Path(name) for name in names])
    blocks.append({"type": "text", "text": build(req)})
    request_id = str(uuid.uuid4())
    try:
        async with SLOTS:
            response = await request.app.state.client.messages.create(
                model=MODEL, max_tokens=600, system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
            )
    except anthropic.APIError as exc:
        LOG.warning(json.dumps({"event": "provider_error", "request_id": request_id,
                                "rule_id": payload.rule_id,
                                "type": type(exc).__name__}))
        raise HTTPException(status_code=502, detail="AI 공급자 호출에 실패했습니다.") from None

    text = "".join(part.text for part in response.content
                   if getattr(part, "type", "") == "text")
    result = parse(text, provider=f"internal-gateway/{MODEL}", fallback_cited=names)
    if result is None:
        raise HTTPException(status_code=422, detail="AI가 검증 가능한 응답을 주지 않았습니다.")
    LOG.info(json.dumps({"event": "judged", "request_id": request_id,
                         "rule_id": payload.rule_id, "media_count": len(names)}))
    return JudgeOutput(verdict=result.verdict.value, confidence=result.confidence,
                       rationale=result.rationale, cited=result.cited,
                       provider=result.provider, request_id=request_id)

