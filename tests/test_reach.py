from fastapi.testclient import TestClient

from notoryx_ops.config import Config
from notoryx_ops.server import create_app


def make(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    return TestClient(create_app(cfg))


def test_trace_round_trip_and_names(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        rep = st.radio.net.repeaters[2]["key"][:1].hex()
        r = c.post("/api/reach/trace", json={"path": [rep]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sent_path"] == [rep]
        assert body["hops"][0]["hash"] == rep and "snr" in body["hops"][0]


def test_trace_checks_input_and_airtime(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/api/reach/trace", json={"path": ["xyz"]}).status_code == 400
        assert c.post("/api/reach/trace", json={"path": []}).status_code == 422
        st = c.app.state.station
        a, b = (n["key"][:1].hex() for n in st.radio.net.repeaters[1:3])
        first = c.post("/api/reach/trace", json={"path": [a, b]})
        assert first.json()["sent_path"] == [a, b, a]
        assert c.post("/api/reach/trace", json={"path": [a]}).status_code == 429


def test_unknown_hop_is_a_radio_error(tmp_path):
    with make(tmp_path) as c:
        known = {n["key"][:1].hex() for n in c.app.state.station.radio.net.repeaters}
        missing = next(f"{i:02x}" for i in range(256) if f"{i:02x}" not in known)
        r = c.post("/api/reach/trace", json={"path": [missing]})
        assert r.status_code == 503 and "didn't come back" in r.json()["detail"]


def test_route(tmp_path):
    with make(tmp_path) as c:
        key = c.app.state.station.radio.net.nodes[7]["key"].hex()
        r = c.post("/api/reach/route", json={"public_key": key})
        assert r.status_code == 200, r.text
        assert len(r.json()["out"]) == 2 and len(r.json()["back"]) == 2
        assert c.post("/api/reach/route", json={"public_key": "nothex"}).status_code == 422


def test_who_hears_me(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/reach/who-hears-me")
        assert r.status_code == 200, r.text
        heard = r.json()["heard"]
        assert len(heard) == 4
        assert heard[0]["snr_them"] >= heard[-1]["snr_them"]


def test_contacts_only_lists_what_the_radio_knows(tmp_path):
    with make(tmp_path) as c:
        net = c.app.state.station.radio.net
        r = c.get("/api/reach/contacts")
        assert r.status_code == 200, r.text
        keys = [x["public_key"] for x in r.json()]
        assert all(n["key"].hex() in keys for n in net.repeaters)
        far = [n for n in net.nodes if n["type"] == 1][5]
        assert far["key"].hex() not in keys
        names = [x["name"].lower() for x in r.json()]
        assert names == sorted(names)
        # Listing contacts sends nothing, so it doesn't use the airtime budget.
        assert c.post("/api/reach/who-hears-me").status_code == 200


def test_route_to_a_node_the_radio_doesnt_know(tmp_path):
    with make(tmp_path) as c:
        far = [n for n in c.app.state.station.radio.net.nodes if n["type"] == 1][5]
        r = c.post("/api/reach/route", json={"public_key": far["key"].hex()})
        assert r.status_code == 503 and "contacts" in r.json()["detail"]
