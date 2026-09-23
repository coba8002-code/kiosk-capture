import json
import math
import struct
import wave
from pathlib import Path

from engine import pipeline, quality, rules
from engine.media import audio_quality
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
    key = "sk-ant-secret-test"
    env = panel._child_env({"ANTHROPIC_API_KEY": key, "UNSAFE": "no"})
    assert env["ANTHROPIC_API_KEY"] == key
    assert "UNSAFE" not in env
    page = panel.page("token")
    assert "API 키는 이 화면을 닫을 때 사라지며" in page
    assert key not in page


def test_panel_rejects_ai_without_consent(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    job, why = panel.start_job("ingest", [str(tmp_path)], {
        "enabled": True, "api_key": "sk-ant-test", "consent": False})
    assert job is None
    assert "확인이 필요" in why


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
