"""Serveur central BadgeLock.

* /ws/agent      : WebSocket sur lequel chaque poste (agent) se connecte.
* /api/...       : API d'administration (authentification HTTP Basic).
* /              : page d'administration web.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .config import Badge, ConfigStore, normalize_uid
from .logic import decide

log = logging.getLogger("badgelock")

STATIC_DIR = Path(__file__).parent / "static"


class AgentHub:
    """Postes connectés et leur état de verrouillage."""

    def __init__(self) -> None:
        self.sockets: dict[str, WebSocket] = {}
        self.locked: dict[str, bool] = {}
        self.events: deque[dict] = deque(maxlen=500)
        # Badges inconnus récemment passés, pour les ajouter facilement.
        self.unknown: dict[str, dict] = {}

    def event(self, **data) -> None:
        data["time"] = time.time()
        self.events.appendleft(data)
        log.info("%s", data)

    async def send(self, pc: str, message: dict) -> bool:
        ws = self.sockets.get(pc)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:  # connexion morte : elle sera nettoyée par sa boucle
            return False


class BadgeIn(BaseModel):
    name: str
    enabled: bool = True
    can_lock: bool = True
    can_unlock: bool = True
    pcs: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


class PcIn(BaseModel):
    description: str = ""
    regenerate_token: bool = False


class GroupIn(BaseModel):
    members: list[str]


class CommandIn(BaseModel):
    action: str = Field(pattern="^(lock|unlock)$")


def create_app(config_path: str | None = None, admin_user: str | None = None,
               admin_password: str | None = None) -> FastAPI:
    config_path = config_path or os.environ.get("BADGELOCK_CONFIG", "data/config.json")
    admin_user = admin_user or os.environ.get("BADGELOCK_ADMIN_USER", "admin")
    admin_password = admin_password or os.environ.get("BADGELOCK_ADMIN_PASSWORD")
    if not admin_password:
        admin_password = secrets.token_urlsafe(12)
        log.warning("BADGELOCK_ADMIN_PASSWORD non défini, mot de passe généré : %s", admin_password)

    store = ConfigStore(config_path)
    hub = AgentHub()
    app = FastAPI(title="BadgeLock")
    app.state.store = store
    app.state.hub = hub
    security = HTTPBasic()

    def admin(creds: HTTPBasicCredentials = Depends(security)) -> None:
        ok_user = secrets.compare_digest(creds.username.encode(), admin_user.encode())
        ok_pass = secrets.compare_digest(creds.password.encode(), admin_password.encode())
        if not (ok_user and ok_pass):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    async def handle_badge(reader_pc: str, uid: str) -> None:
        try:
            uid = normalize_uid(uid)
        except ValueError:
            return
        decision = decide(store.config, uid, reader_pc, dict(hub.locked))
        if decision.badge_name is None:
            hub.unknown[uid] = {"uid": uid, "reader": reader_pc, "time": time.time()}
        hub.event(kind="badge", uid=uid, reader=reader_pc, badge=decision.badge_name,
                  allowed=decision.allowed, reason=decision.reason, action=decision.action,
                  targets=decision.commands)
        if decision.allowed:
            for pc in decision.commands:
                await hub.send(pc, {"type": "command", "action": decision.action,
                                    "badge": decision.badge_name})
        await hub.send(reader_pc, {"type": "feedback", "allowed": decision.allowed,
                                   "message": decision.reason})

    @app.websocket("/ws/agent")
    async def agent_ws(ws: WebSocket) -> None:
        await ws.accept()
        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
        except Exception:
            await ws.close(code=4400)
            return
        pc = str(hello.get("pc", ""))
        if hello.get("type") != "hello" or not store.check_pc_token(pc, str(hello.get("token", ""))):
            hub.event(kind="auth_failed", pc=pc)
            await ws.close(code=4401)
            return

        previous = hub.sockets.get(pc)
        if previous is not None:
            await previous.close(code=4409)
        hub.sockets[pc] = ws
        hub.locked[pc] = bool(hello.get("locked", False))
        hub.event(kind="connected", pc=pc, locked=hub.locked[pc])
        await ws.send_json({"type": "welcome"})
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "state":
                    hub.locked[pc] = bool(msg.get("locked"))
                elif kind == "badge":
                    await handle_badge(pc, str(msg.get("uid", "")))
                elif kind == "result":
                    hub.event(kind="result", pc=pc, action=msg.get("action"),
                              ok=msg.get("ok"), message=msg.get("message"))
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            if hub.sockets.get(pc) is ws:
                del hub.sockets[pc]
                hub.locked.pop(pc, None)
                hub.event(kind="disconnected", pc=pc)

    # ------------------------------------------------------------------
    # Administration
    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False, dependencies=[Depends(admin)])
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/api/config", dependencies=[Depends(admin)])
    def get_config() -> dict:
        return store.config.to_dict()

    @app.get("/api/status", dependencies=[Depends(admin)])
    def get_status() -> dict:
        return {
            "online": {pc: {"locked": locked} for pc, locked in hub.locked.items()},
            "unknown_badges": sorted(hub.unknown.values(), key=lambda b: -b["time"]),
        }

    @app.get("/api/events", dependencies=[Depends(admin)])
    def get_events(limit: int = 100) -> list[dict]:
        return list(hub.events)[:limit]

    @app.put("/api/badges/{uid}", dependencies=[Depends(admin)])
    def put_badge(uid: str, body: BadgeIn) -> dict:
        try:
            uid = store.put_badge(uid, Badge(**body.model_dump()))
        except ValueError as e:
            raise HTTPException(400, str(e))
        hub.unknown.pop(uid, None)
        return {"uid": uid}

    @app.delete("/api/badges/{uid}", dependencies=[Depends(admin)])
    def delete_badge(uid: str) -> dict:
        if not store.delete_badge(uid):
            raise HTTPException(404, "badge introuvable")
        return {"ok": True}

    @app.put("/api/pcs/{name}", dependencies=[Depends(admin)])
    def put_pc(name: str, body: PcIn) -> dict:
        token = secrets.token_urlsafe(24) if body.regenerate_token else None
        pc = store.put_pc(name, body.description, token)
        return {"name": name, "token": pc.token}

    @app.delete("/api/pcs/{name}", dependencies=[Depends(admin)])
    async def delete_pc(name: str) -> dict:
        if not store.delete_pc(name):
            raise HTTPException(404, "poste introuvable")
        ws = hub.sockets.get(name)
        if ws is not None:
            await ws.close(code=4401)
        return {"ok": True}

    @app.put("/api/groups/{name}", dependencies=[Depends(admin)])
    def put_group(name: str, body: GroupIn) -> dict:
        store.put_group(name, body.members)
        return {"ok": True}

    @app.delete("/api/groups/{name}", dependencies=[Depends(admin)])
    def delete_group(name: str) -> dict:
        if not store.delete_group(name):
            raise HTTPException(404, "groupe introuvable")
        return {"ok": True}

    @app.post("/api/pcs/{name}/command", dependencies=[Depends(admin)])
    async def command_pc(name: str, body: CommandIn) -> dict:
        """Verrouillage / déverrouillage manuel depuis la page d'administration."""
        if not await hub.send(name, {"type": "command", "action": body.action, "badge": "admin"}):
            raise HTTPException(409, "poste non connecté")
        hub.event(kind="admin_command", pc=name, action=body.action)
        return {"ok": True}

    return app


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Serveur central BadgeLock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=None, help="fichier de configuration JSON")
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(create_app(args.config), host=args.host, port=args.port,
                ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)
