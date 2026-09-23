from pathlib import Path
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
