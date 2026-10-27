import pytest
from fastapi.testclient import TestClient

from badgelock_server.app import create_app

AUTH = ("admin", "secret")


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "config.json"), "admin", "secret")
    with TestClient(app) as c:
        yield c


def connect(client, pc, token, locked=False):
    ws = client.websocket_connect("/ws/agent").__enter__()
    ws.send_json({"type": "hello", "pc": pc, "token": token, "locked": locked})
    assert ws.receive_json() == {"type": "welcome"}
    return ws


def test_admin_requires_auth(client):
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config", auth=("admin", "bad")).status_code == 401
    assert client.get("/api/config", auth=AUTH).status_code == 200


def test_bad_token_is_rejected(client):
    client.put("/api/pcs/PC1", json={}, auth=AUTH)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json({"type": "hello", "pc": "PC1", "token": "wrong"})
        with pytest.raises(Exception):
            ws.receive_json()


def test_one_tap_locks_then_unlocks_several_pcs(client):
    t1 = client.put("/api/pcs/PC1", json={}, auth=AUTH).json()["token"]
    t2 = client.put("/api/pcs/PC2", json={}, auth=AUTH).json()["token"]
    client.put("/api/groups/bureau", json={"members": ["PC1", "PC2"]}, auth=AUTH)
    client.put("/api/badges/04:a1:b2", json={"name": "Jean", "groups": ["bureau"]}, auth=AUTH)

    ws1, ws2 = connect(client, "PC1", t1), connect(client, "PC2", t2)

    ws1.send_json({"type": "badge", "uid": "04A1B2"})
    assert ws1.receive_json() == {"type": "command", "action": "lock", "badge": "Jean"}
    assert ws1.receive_json()["type"] == "feedback"
    assert ws2.receive_json() == {"type": "command", "action": "lock", "badge": "Jean"}

    ws1.send_json({"type": "state", "locked": True})
    ws2.send_json({"type": "state", "locked": True})
    ws1.send_json({"type": "badge", "uid": "04a1b2"})
    assert ws1.receive_json()["action"] == "unlock"
    assert ws2.receive_json()["action"] == "unlock"

    ws1.__exit__(None, None, None)
    ws2.__exit__(None, None, None)


def test_unknown_badge_is_listed(client):
    t1 = client.put("/api/pcs/PC1", json={}, auth=AUTH).json()["token"]
    ws = connect(client, "PC1", t1)
    ws.send_json({"type": "badge", "uid": "DEADBEEF"})
    feedback = ws.receive_json()
    assert feedback["type"] == "feedback" and not feedback["allowed"]
    status = client.get("/api/status", auth=AUTH).json()
    assert status["unknown_badges"][0]["uid"] == "DEADBEEF"
    assert status["online"] == {"PC1": {"locked": False}}
    ws.__exit__(None, None, None)
    client.put("/api/badges/DEADBEEF", json={"name": "Marie"}, auth=AUTH)
    assert client.get("/api/status", auth=AUTH).json()["unknown_badges"] == []


def test_config_is_persisted(tmp_path):
    path = str(tmp_path / "config.json")
    with TestClient(create_app(path, "admin", "secret")) as c:
        c.put("/api/badges/AB", json={"name": "Jean", "pcs": ["PC1"]}, auth=AUTH)
    with TestClient(create_app(path, "admin", "secret")) as c:
        assert c.get("/api/config", auth=AUTH).json()["badges"]["AB"]["pcs"] == ["PC1"]
