"""Décision : que faire quand un badge est passé sur un lecteur ?

Logique pure (sans réseau) pour pouvoir la tester facilement.

Règle « bascule » :
  * Le badge pilote un ensemble de postes (ses postes + ceux de ses groupes).
  * Si le badge est passé sur un poste piloté, c'est l'état de CE poste qui
    décide : déverrouillé -> on verrouille tous les postes, verrouillé -> on
    les déverrouille tous.
  * Si le badge est passé sur un lecteur qui n'est pas dans ses postes
    (ex. lecteur d'accueil), on verrouille si au moins un poste est
    déverrouillé, sinon on déverrouille.
  * Seuls les postes connectés au serveur reçoivent l'ordre.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, normalize_uid

LOCK = "lock"
UNLOCK = "unlock"


@dataclass
class Decision:
    allowed: bool
    reason: str
    badge_name: str | None = None
    action: str | None = None
    # Postes qui doivent recevoir l'ordre.
    commands: list[str] = field(default_factory=list)


def decide(config: Config, uid: str, reader_pc: str, locked: dict[str, bool]) -> Decision:
    """Calcule l'action pour un passage de badge.

    `locked` donne l'état des postes connectés : nom -> True si verrouillé.
    """
    uid = normalize_uid(uid)
    badge = config.badges.get(uid)
    if badge is None:
        return Decision(False, f"badge inconnu {uid}")
    if not badge.enabled:
        return Decision(False, "badge désactivé", badge.name)

    targets = config.targets_of(badge)
    online = [pc for pc in targets if pc in locked]
    if not online:
        return Decision(False, "aucun poste piloté par ce badge n'est connecté", badge.name)

    if reader_pc in online:
        should_lock = not locked[reader_pc]
    else:
        should_lock = any(not locked[pc] for pc in online)

    if should_lock:
        if not badge.can_lock:
            return Decision(False, "ce badge n'a pas le droit de verrouiller", badge.name, LOCK)
        commands = [pc for pc in online if not locked[pc]]
        return Decision(True, "verrouillage", badge.name, LOCK, commands)

    if not badge.can_unlock:
        return Decision(False, "ce badge n'a pas le droit de déverrouiller", badge.name, UNLOCK)
    commands = [pc for pc in online if locked[pc]]
    return Decision(True, "déverrouillage", badge.name, UNLOCK, commands)
