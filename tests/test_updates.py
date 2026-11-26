import hashlib
import io

import pytest

from notoryx_ops import __version__, updates

FEED_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/x/y/releases/tag/meshtastic-addon-v0.1.1"/>
    <title>Meshtastic add-on 0.1.1</title><content type="html"></content>
  </entry>
  <entry>
    <updated>2026-11-25T00:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/x/y/releases/tag/v{version}-test.1"/>
    <title>NOTORYX Ops {version}</title>
    <content type="html">&lt;p&gt;Intro&lt;/p&gt;&lt;h2&gt;New in {version}&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;&lt;strong&gt;Big thing.&lt;/strong&gt; It works.&lt;/li&gt;&lt;li&gt;Small thing&lt;/li&gt;&lt;/ul&gt;</content>
  </entry>
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/x/y/releases/tag/v0.0.1-test.1"/>
    <title>old</title><content type="html"></content>
  </entry>
</feed>"""


@pytest.fixture(autouse=True)
def no_cache():
    updates._cache.update(at=0.0, releases=None)
    yield
    updates._cache.update(at=0.0, releases=None)


def serve(monkeypatch, files, version="99.0.0", sums=None):
    """Answer the releases feed, SHA256SUMS and downloads from memory."""
    if sums is None:
        sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items())

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        url = req.full_url
        if url.endswith(".atom"):
            return Resp(FEED_TMPL.format(version=version).encode())
        name = url.rsplit("/", 1)[1]
        if name == "SHA256SUMS":
            return Resp(sums.encode())
        return Resp(files[name])

    monkeypatch.setattr(updates, "urlopen", urlopen)


def test_summary_finds_newer_version_and_addons(monkeypatch):
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"app", "notoryx_ops_meshtastic-9.9.9-py3-none-any.whl": b"w"})
    s = updates.summary()
    assert s["current"] == __version__ and s["newer"] and s["latest"]["version"] == "99.0.0"
    assert s["mac_asset"] == "notoryx-ops-99.0.0-mac.zip"
    (addon,) = s["addons"]
    assert addon["id"] == "notoryx_ops_meshtastic" and addon["available"] == "9.9.9" and addon["name"] == "Meshtastic"


def test_notes_come_back_as_text(monkeypatch):
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"app"})
    notes = updates.summary()["latest"]["notes"]
    assert "## New in 99.0.0" in notes and "- Big thing. It works." in notes and "<" not in notes


def test_download_checks_the_checksum(monkeypatch, tmp_path):
    good = b"the real app"
    sums = f"{hashlib.sha256(good).hexdigest()}  notoryx-ops-99.0.0-mac.zip\n"
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": good}, sums=sums)
    path = updates.download("notoryx-ops-99.0.0-mac.zip", tmp_path)
    assert path.read_bytes() == good

    updates._cache.update(at=0.0, releases=None)
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"tampered"}, sums=sums)
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.download("notoryx-ops-99.0.0-mac.zip", tmp_path / "again")
    assert not (tmp_path / "again" / "notoryx-ops-99.0.0-mac.zip").exists()


def test_download_refuses_files_not_in_the_release(monkeypatch, tmp_path):
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"x"})
    with pytest.raises(updates.UpdateError, match="no file"):
        updates.download("../../etc/passwd", tmp_path)
    with pytest.raises(updates.UpdateError, match="no file"):
        updates.download("something-else.zip", tmp_path)


def test_a_failed_check_keeps_the_last_answer(monkeypatch):
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": b"x"})
    assert updates.summary()["latest"]["version"] == "99.0.0"

    def down(req, timeout):
        raise OSError("HTTP Error 403: rate limit exceeded")

    monkeypatch.setattr(updates, "urlopen", down)
    assert updates.summary(force=True)["latest"]["version"] == "99.0.0"


def test_update_endpoints_need_admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from notoryx_ops import config
    from notoryx_ops.server import create_app

    serve(monkeypatch, {})
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


def test_download_reports_progress(monkeypatch, tmp_path):
    good = b"x" * 200_000
    serve(monkeypatch, {"notoryx-ops-99.0.0-mac.zip": good})
    seen = []
    updates.download("notoryx-ops-99.0.0-mac.zip", tmp_path, lambda done, total: seen.append(done))
    assert seen and seen[-1] == len(good) and seen == sorted(seen)
