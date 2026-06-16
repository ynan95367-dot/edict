import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import Settings


def test_cors_default_includes_dashboard():
    s = Settings()
    assert "http://127.0.0.1:7891" in s.cors_origins
    assert "http://localhost:5173" in s.cors_origins


def test_cors_comma_split(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a, http://b ,http://c")
    s = Settings()
    assert s.cors_origins == ["http://a", "http://b", "http://c"]


def test_cors_wildcard_preserved(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    s = Settings()
    assert s.cors_origins == ["*"]
