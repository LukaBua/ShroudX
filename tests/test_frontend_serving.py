# AI-assisted: built React files and the legacy fallback are both served locally.
from fastapi.testclient import TestClient

import app as backend


def test_frontend_build_and_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ROOT", tmp_path)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<title>Legacy ShroudX</title>")
    application = backend.create_app(mode="synthetic", cache_root=tmp_path / "cache")
    with TestClient(application) as client:
        assert "Legacy ShroudX" in client.get("/").text
        built = tmp_path / "frontend" / "dist"
        (built / "assets").mkdir(parents=True)
        (built / "index.html").write_text('<title>React ShroudX</title><script src="/assets/app.js"></script>')
        (built / "assets" / "app.js").write_text("console.log('local asset');")
        assert "React ShroudX" in client.get("/").text
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        assert "local asset" in response.text
        assert "Legacy ShroudX" in client.get("/legacy").text
        assert "Legacy ShroudX" in client.get("/static/index.html").text
