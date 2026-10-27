"""Stockage de la configuration (badges, postes, groupes) dans un fichier JSON."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

_UID_SEPARATORS = re.compile(r"[\s:\-]")


def normalize_uid(uid: str) -> str:
    """Normalise un identifiant de badge : majuscules, sans séparateurs.

    "04:a1:b2-c3 d4" -> "04A1B2C3D4". Les identifiants non hexadécimaux
    (certains lecteurs renvoient un numéro décimal) sont conservés tels quels
    en majuscules.
    """
    cleaned = _UID_SEPARATORS.sub("", uid.strip()).upper()
    if not cleaned:
        raise ValueError("identifiant de badge vide")
    return cleaned


@dataclass
class Badge:
    name: str
    enabled: bool = True
    can_lock: bool = True
    can_unlock: bool = True
    # Postes pilotés par ce badge, directement ou via des groupes.
    pcs: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)


@dataclass
class Pc:
    token: str
    description: str = ""


@dataclass
class Config:
    badges: dict[str, Badge] = field(default_factory=dict)
    pcs: dict[str, Pc] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)

    def targets_of(self, badge: Badge) -> list[str]:
        """Liste ordonnée et dédoublonnée des postes pilotés par un badge."""
        seen: dict[str, None] = {}
        for pc in badge.pcs:
            seen[pc] = None
        for group in badge.groups:
            for pc in self.groups.get(group, []):
                seen[pc] = None
        return list(seen)

    def to_dict(self) -> dict:
        return {
            "badges": {uid: asdict(b) for uid, b in self.badges.items()},
            "pcs": {name: asdict(p) for name, p in self.pcs.items()},
            "groups": {name: list(m) for name, m in self.groups.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        return cls(
            badges={normalize_uid(uid): Badge(**b) for uid, b in data.get("badges", {}).items()},
            pcs={name: Pc(**p) for name, p in data.get("pcs", {}).items()},
            groups={name: list(m) for name, m in data.get("groups", {}).items()},
        )


class ConfigStore:
    """Configuration persistée sur disque, protégée par un verrou."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.RLock()
        if self.path.exists():
            self.config = Config.from_dict(json.loads(self.path.read_text("utf-8")))
        else:
            self.config = Config()
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False)
            # Écriture atomique : on ne laisse jamais un fichier à moitié écrit.
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.path)

    # --- Badges -----------------------------------------------------------

    def put_badge(self, uid: str, badge: Badge) -> str:
        uid = normalize_uid(uid)
        with self._lock:
            self.config.badges[uid] = badge
            self.save()
        return uid

    def delete_badge(self, uid: str) -> bool:
        with self._lock:
            removed = self.config.badges.pop(normalize_uid(uid), None) is not None
            if removed:
                self.save()
            return removed

    # --- Postes -----------------------------------------------------------

    def put_pc(self, name: str, description: str = "", token: str | None = None) -> Pc:
        with self._lock:
            existing = self.config.pcs.get(name)
            if token is None:
                token = existing.token if existing else secrets.token_urlsafe(24)
            pc = Pc(token=token, description=description)
            self.config.pcs[name] = pc
            self.save()
            return pc

    def delete_pc(self, name: str) -> bool:
        with self._lock:
            removed = self.config.pcs.pop(name, None) is not None
            if removed:
                self.save()
            return removed

    def check_pc_token(self, name: str, token: str) -> bool:
        pc = self.config.pcs.get(name)
        return pc is not None and secrets.compare_digest(pc.token, token)

    # --- Groupes ----------------------------------------------------------

    def put_group(self, name: str, members: list[str]) -> None:
        with self._lock:
            self.config.groups[name] = list(dict.fromkeys(members))
            self.save()

    def delete_group(self, name: str) -> bool:
        with self._lock:
            removed = self.config.groups.pop(name, None) is not None
            if removed:
                self.save()
            return removed
