"""Stockage local du mot de passe de session, chiffré avec DPAPI.

Le mot de passe ne quitte jamais le poste : le serveur central envoie
seulement l'ordre « déverrouiller » et c'est l'agent qui fournit le mot de
passe au fournisseur d'identification (Credential Provider) local.
DPAPI lie le chiffrement au compte Windows de l'utilisateur : le fichier
copié sur une autre machine ou lu par un autre compte est inutilisable.
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

ENTROPY = b"BadgeLock credentials v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _crypt(func_name: str, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = getattr(crypt32, func_name)
    src, entropy, out = _blob(data), _blob(ENTROPY), _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not func(ctypes.byref(src), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _crypt("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    return _crypt("CryptUnprotectData", data)


@dataclass
class Credentials:
    domain: str
    username: str
    password: str


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "BadgeLock" / "credentials.bin"


def save(creds: Credentials, path: Path | None = None) -> Path:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(protect(json.dumps(asdict(creds)).encode("utf-8")))
    return path


def load(path: Path | None = None) -> Credentials | None:
    path = path or default_path()
    if not path.exists():
        return None
    return Credentials(**json.loads(unprotect(path.read_bytes())))
