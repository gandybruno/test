"""Verrouillage de la session Windows et détection de son état."""

from __future__ import annotations

import ctypes
import sys

DESKTOP_SWITCHDESKTOP = 0x0100


def _user32():
    if sys.platform != "win32":
        raise OSError("fonction disponible uniquement sous Windows")
    return ctypes.WinDLL("user32", use_last_error=True)


def lock() -> None:
    """Verrouille la session (équivalent de Win+L)."""
    if not _user32().LockWorkStation():
        raise ctypes.WinError(ctypes.get_last_error())


def is_locked() -> bool:
    """Vrai si la session est verrouillée.

    Quand la session est verrouillée, le bureau actif est celui de LogonUI et
    un processus de l'utilisateur ne peut plus l'ouvrir ni basculer dessus.
    """
    user32 = _user32()
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop:
        return True
    try:
        return not user32.SwitchDesktop(ctypes.c_void_p(desktop))
    finally:
        user32.CloseDesktop(ctypes.c_void_p(desktop))
