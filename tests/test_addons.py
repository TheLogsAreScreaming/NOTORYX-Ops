from fastapi import APIRouter
from fastapi.testclient import TestClient

from notoryx_ops import config, settings
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.station import Station


class Dummy:
    id, name, version, license = "dummy", "Dummy", "1.0", "MIT"

    def __init__(self, ctx, page_dir=None):
        self.ctx = ctx
        self.started = self.stopped = False
        self.page_dir = page_dir
        self.router = APIRouter()

        @self.router.get("/hello")
        def hello():
            return {"hi": ctx.settings.get("greeting", "hello")}

        @self.router.post("/greeting")
        def set_greeting(body: dict):
            ctx.settings["greeting"] = body["greeting"]
            ctx.save_settings()
            return {"ok": True}

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def info(self):
        return {"running": self.started}


def test_addon_routes_settings_and_lifecycle(tmp_path):
    page = tmp_path / "page"
    page.mkdir()
    (page / "index.html").write_text("<h1>dummy page</h1>")
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    st = Station(cfg, addon_factories=[("dummy", lambda ctx: Dummy(ctx, page))])
    with TestClient(create_app(cfg, st)) as c:
        assert st.addons[0].started
        lst = c.get("/api/addons").json()
        assert lst == [{"id": "dummy", "name": "Dummy", "version": "1.0", "license": "MIT", "page": "/addons/dummy/",
                        "info": {"running": True}}]
        assert c.get("/api/addons/dummy/hello").json() == {"hi": "hello"}
        assert c.post("/api/addons/dummy/greeting", json={"greeting": "howdy"}).json()["ok"]
        assert "dummy page" in c.get("/addons/dummy/").text
        assert c.get("/api/station").json()["addons"][0]["license"] == "MIT"
    assert st.addons[0].stopped
    back = config.load(cfg.path)
    assert back.addons["dummy"]["greeting"] == "howdy"


def test_disabled_and_broken_addons_dont_stop_the_station(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, addons_disabled=["off"])

    def broken(ctx):
        raise RuntimeError("boom")

    st = Station(cfg, addon_factories=[("off", lambda ctx: Dummy(ctx)), ("broken", broken)])
    assert st.addons == []
    with TestClient(create_app(cfg, st)) as c:
        assert c.get("/api/addons").json() == []


def test_addon_routes_need_sign_in_when_accounts_on(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    st = Station(cfg, addon_factories=[("dummy", lambda ctx: Dummy(ctx))])
    with TestClient(create_app(cfg, st)) as c:
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        c.cookies.clear()
        assert c.get("/api/addons/dummy/hello").status_code == 401


def test_addon_settings_toml_skips_odd_values(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
    cfg.addons = {"x": {"a": 1, "b": "two", "c": True, "d": ["e", 3], "nested": {"no": 1}, "bad key": 1}}
    cfg.addons_disabled = ["y"]
    settings.save(cfg)
    back = config.load(cfg.path)
    assert back.addons["x"] == {"a": 1, "b": "two", "c": True, "d": ["e", 3]}
    assert back.addons_disabled == ["y"]
