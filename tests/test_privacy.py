import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from notoryx_ops import privacy
from notoryx_ops.config import Config
from notoryx_ops.server import create_app

TOOL = Path(__file__).resolve().parent.parent / "tools" / "privacy_check.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("privacy_check", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["privacy_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_station_code_passes():
    assert load_tool().check() == []


def test_undeclared_network_module_fails(tmp_path, monkeypatch):
    tool = load_tool()
    pkg = tmp_path / "notoryx_ops"
    pkg.mkdir()
    (pkg / "sneaky.py").write_text("import urllib.request\n")
    (pkg / "quiet.py").write_text("import json\n")
    (pkg / "helper.py").write_text("from .net import urlopen\n")
    monkeypatch.setattr(tool, "PKG", pkg)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    problems = tool.check()
    assert any("sneaky.py" in p for p in problems)
    assert any("helper.py" in p for p in problems)
    assert not any("quiet.py" in p for p in problems)


def test_radio_data_flows_start_off():
    fresh = Config(path=Path("x.toml"))
    assert all(not f.on(fresh) for f in privacy.FLOWS if f.radio_data)


def test_privacy_route_shows_what_is_on(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, public_map=True)
    with TestClient(create_app(cfg)) as c:
        flows = {f["id"]: f for f in c.get("/api/privacy").json()["flows"]}
        assert flows["public_map"]["on"] and flows["public_map"]["radio_data"]
        assert not flows["stats_push"]["on"]
