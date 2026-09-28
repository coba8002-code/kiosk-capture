import json
import math
import struct
import wave
from pathlib import Path

from engine import pipeline, quality, rules
from engine.media import audio_quality
from engine.l2.gateway_provider import GatewayProvider, _judgment
from engine.verdict import Verdict
from tools import panel


def _wav(path, samples, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", x) for x in samples))


def test_wav_quality_distinguishes_signal_and_silence(tmp_path):
    rate = 8000
    tone = [int(9000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate * 3)]
    _wav(tmp_path / "tone.wav", tone, rate)
    _wav(tmp_path / "silence.wav", [0] * rate * 3, rate)
    good = audio_quality.inspect_wav(tmp_path / "tone.wav")
    silent = audio_quality.inspect_wav(tmp_path / "silence.wav")
    assert good.readable and good.status == "분석 가능"
    assert silent.readable and silent.status == "재수집 권장"
    assert any("신호가 너무 작음" in x for x in silent.issues)
    assert good.to_dict()["legal_measurement"] is False


def test_manifest_audio_metrics_are_validated():
    q = audio_quality.from_manifest({
        "duration_s": 3.2, "sample_rate": 48000, "channels": 1,
        "peak_dbfs": -2.0, "rms_dbfs": -18.0,
        "silence_ratio": 0.1, "clipping_ratio": 0.0,
    })
    assert q.status == "분석 가능"
    assert not audio_quality.from_manifest({"duration_s": "bad"}).readable


def test_field_quality_reports_missing_sets_and_audio(tmp_path):
    (tmp_path / "voice.m4a").write_bytes(b"not decoded on PC")
    manifest = {
        "schema": pipeline.SCHEMA,
        "device": {"id": "QA-1", "product_type": "중대형", "scopes": [], "exemptions": []},
        "shots": [{"set": "S5", "shot": "S5-01", "file": "voice.m4a",
                   "masked": False, "audio_metrics": {
                       "duration_s": 4, "sample_rate": 48000, "channels": 1,
                       "peak_dbfs": -3, "rms_dbfs": -20,
                       "silence_ratio": 0.15, "clipping_ratio": 0,
                   }}],
        "owner_answers": {}, "measurements": {},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = pipeline.load_bundle(tmp_path)
    report = quality.audit(bundle, rules.load_protocol())
    assert report.audio_analyzed == 1
    assert report.status == "보완 후 진단"
    assert any(x.code == "missing-set" for x in report.issues)
    assert "법정 적합성 인증이 아닙니다" in quality.render(report)


def test_panel_ai_key_is_session_only_and_never_in_page():
    key = "test-api-key-never-save"
    env = panel._child_env({"ANTHROPIC_API_KEY": key, "UNSAFE": "no"})
    assert env["ANTHROPIC_API_KEY"] == key
    assert "UNSAFE" not in env
    page = panel.page("token")
    assert "공급자 API 키는 서버에만" in page
    assert key not in page


def test_panel_rejects_ai_without_consent(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    job, why = panel.start_job("ingest", [str(tmp_path)], {
        "enabled": True, "api_key": "sk-ant-test", "consent": False})
    assert job is None
    assert "확인이 필요" in why


def test_gateway_requires_https_and_token(monkeypatch):
    monkeypatch.delenv("KFA_GATEWAY_ALLOW_HTTP", raising=False)
    p = GatewayProvider("http://10.0.0.8:8787", "x" * 32)
    assert not p.available()[0]
    p = GatewayProvider("https://kfa-ai.company.internal", "x" * 32)
    assert p.available()[0]
    assert not GatewayProvider("https://kfa-ai.company.internal", "").available()[0]


def test_gateway_response_is_strict():
    good = _judgment({"verdict": "위반", "confidence": .8,
                      "rationale": "초점 표시가 보이지 않습니다.",
                      "cited": ["one.jpg"], "provider": "internal/test"},
                     ["one.jpg"])
    assert good and good.verdict is Verdict.FAIL
    assert _judgment({"verdict": "부적합", "confidence": .8,
                      "rationale": "x", "cited": ["one.jpg"]}, ["one.jpg"]) is None
    assert _judgment({"verdict": "위반", "confidence": .8,
                      "rationale": "x", "cited": ["not-sent.jpg"]}, ["one.jpg"]) is None


def test_panel_gateway_uses_server_config_not_provider_key(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KFA_GATEWAY_URL", "https://kfa-ai.company.internal")
    monkeypatch.setenv("KFA_GATEWAY_TOKEN", "g" * 64)
    class NoThread:
        def start(self):
            pass
    monkeypatch.setattr(panel.threading, "Thread", lambda **kwargs: NoThread())
    panel.STATE["job"] = None
    job, why = panel.start_job("ingest", [str(tmp_path)], {
        "enabled": True, "mode": "gateway", "consent": True,
        "api_key": "", "model": ""})
    assert not why and job is not None
    assert job.steps[0][-3:] == ["--l2", "gateway", "--yes"]
    assert "ANTHROPIC_API_KEY" not in job.env_overrides
    assert job.env_overrides["KFA_GATEWAY_TOKEN"] == "g" * 64
    panel.STATE["job"] = None


def test_bundle_root_is_absolute_even_when_input_is_relative(tmp_path, monkeypatch):
    work = tmp_path / "work"
    bundle_dir = work / "bundle"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "REL", "product_type": "중대형"},
        "shots": [], "owner_answers": {}, "measurements": {},
    }), encoding="utf-8")
    monkeypatch.chdir(work)
    bundle = pipeline.load_bundle(Path("bundle"))
    assert bundle.root.is_absolute()
    assert bundle.root == bundle_dir.resolve()
