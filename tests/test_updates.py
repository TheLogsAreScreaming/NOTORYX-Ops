import hashlib
import io

import pytest

from notoryx_ops import __version__, updates


def fake_release(version="99.0.0", files=None):
    files = files or {}
    assets = [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in files]
    return [{"tag_name": f"v{version}-test.1", "name": f"NOTORYX Ops {version}", "html_url": "https://example.invalid/rel",
             "body": "Intro\n\n## New in 99.0.0\n- **Big thing.** It works.\n- Small thing\n\n## Downloads\n- not this",
             "published_at": "2026-11-25T00:00:00Z", "draft": False, "assets": assets},
            {"tag_name": "meshtastic-addon-v0.1.1", "assets": [], "draft": False},
            {"tag_name": "v0.0.1-test.1", "assets": [], "draft": False}]


@pytest.fixture(autouse=True)
def no_cache():
    updates._cache.update(at=0.0, releases=None)
    yield
    updates._cache.update(at=0.0, releases=None)


def serve(monkeypatch, files):
    """Answer GitHub and download requests from memory."""
    monkeypatch.setattr(updates, "_get_json", lambda url: fake_release(files=files))

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        name = req.full_url.rsplit("/", 1)[1]
        return Resp(files[name])

    monkeypatch.setattr(updates, "urlopen", urlopen)


def test_summary_finds_newer_version_and_addons(monkeypatch):
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"app", "notoryx_ops_meshtastic-9.9.9-py3-none-any.whl": b"w",
                        "SHA256SUMS": b""})
    s = updates.summary()
    assert s["current"] == __version__ and s["newer"] and s["latest"]["version"] == "99.0.0"
    assert s["mac_asset"] == "notoryx-ops-99.0.0-mac.zip"
    (addon,) = s["addons"]
    assert addon["id"] == "notoryx_ops_meshtastic" and addon["available"] == "9.9.9" and addon["name"] == "Meshtastic"


def test_download_checks_the_checksum(monkeypatch, tmp_path):
    good = b"the real app"
    sums = f"{hashlib.sha256(good).hexdigest()}  notoryx-ops-99.0.0-mac.zip\n".encode()
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": good, "SHA256SUMS": sums})
    path = updates.download("notoryx-ops-99.0.0-mac.zip", tmp_path)
    assert path.read_bytes() == good

    updates._cache.update(at=0.0, releases=None)
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"tampered", "SHA256SUMS": sums})
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.download("notoryx-ops-99.0.0-mac.zip", tmp_path / "again")
    assert not (tmp_path / "again" / "notoryx-ops-99.0.0-mac.zip").exists()


def test_download_refuses_unknown_or_unlisted_files(monkeypatch, tmp_path):
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"x", "SHA256SUMS": b""})
    with pytest.raises(updates.UpdateError, match="no file"):
        updates.download("../../etc/passwd", tmp_path)
    with pytest.raises(updates.UpdateError, match="isn't listed"):
        updates.download("notoryx-ops-99.0.0-mac.zip", tmp_path)


def test_update_endpoints_need_admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from notoryx_ops import config
    from notoryx_ops.server import create_app

    serve(monkeypatch, {"SHA256SUMS": b""})
    cfg = config.load(tmp_path / "config.toml")
    cfg.demo = True
    cfg.admin_token = "a-long-token-123"
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/updates").status_code == 401
        assert c.post("/api/station/restart").status_code == 401
        ok = c.get("/api/updates", headers={"Authorization": "Bearer a-long-token-123"})
        assert ok.status_code == 200 and ok.json()["newer"]
        nothing = c.post("/api/updates/download", headers={"Authorization": "Bearer a-long-token-123"})
        assert nothing.status_code == 409  # no Mac app in that release
