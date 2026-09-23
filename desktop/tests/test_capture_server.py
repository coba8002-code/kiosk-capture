from pathlib import Path
import json
from tools import serve_app


def test_server_guidance_panel(monkeypatch):
    monkeypatch.setenv('KFA_UI', 'panel')
    assert '중지' in serve_app._tail()
    assert 'Ctrl+C' not in serve_app._tail()


def test_server_guidance_cli(monkeypatch):
    monkeypatch.delenv('KFA_UI', raising=False)
    assert 'Ctrl+C' in serve_app._tail()


def test_release_sources_compile():
    root = Path(__file__).resolve().parents[1]
    sources = list(root.glob('*.py'))
    for folder in ('engine', 'tools'):
        sources.extend((root / folder).rglob('*.py'))
    for source in sources:
        compile(source.read_bytes(), str(source), 'exec')


def test_device_id_enables_capture_without_rerender():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / 'app' / 'app.js').read_text(encoding='utf-8')
    assert "id: 'start-capture'" in app_js
    assert 'if (start) start.disabled = !ready' in app_js
    assert "id: 'start-note'" in app_js


def test_protocol_uses_shot_level_marker_and_gallery_import():
    root = Path(__file__).resolve().parents[1]
    protocol = json.loads((root / 'app' / 'protocol.json').read_text(encoding='utf-8'))
    sets = {s['id']: s for s in protocol['sets']}
    s1 = {s['id']: s for s in sets['S1']['shots']}
    s4 = {s['id']: s for s in sets['S4']['shots']}

    assert s1['S1-01']['marker_required'] is True
    assert s1['S1-04']['marker_required'] is False
    assert s1['S1-04']['input_mode'] == 'gallery'
    assert s1['S1-04']['multiple'] is True
    assert [sid for sid, shot in s4.items() if shot['marker_required']] == ['S4-01']
    assert {f['key'] for f in s4['S4-04']['metadata']} == {
        'captured_at', 'lighting', 'reflection', 'illuminance_lux', 'note',
    }


def test_capture_app_has_guidance_feedback_and_retake_flow():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / 'app' / 'app.js').read_text(encoding='utf-8')
    css = (root / 'app' / 'style.css').read_text(encoding='utf-8')
    assert 'openMarkerGuide(set, sh)' in app_js
    assert '원본 이미지 선택' in app_js
    assert 'renderShotMetadata(sh)' in app_js
    assert 'retakeRecommendations()' in app_js
    assert '선택한 답:' in app_js
    assert 'button:active' in css
