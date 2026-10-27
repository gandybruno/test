from badgelock_server.config import Badge, Config, normalize_uid
from badgelock_server.logic import LOCK, UNLOCK, decide


def make_config(**badge_kwargs):
    badge = Badge(name="Jean", pcs=["PC1"], groups=["compta"], **badge_kwargs)
    return Config(badges={"04A1B2": badge}, groups={"compta": ["PC2", "PC3", "PC1"]})


def test_normalize_uid():
    assert normalize_uid(" 04:a1-b2 c3 ") == "04A1B2C3"


def test_targets_are_deduplicated():
    config = make_config()
    assert config.targets_of(config.badges["04A1B2"]) == ["PC1", "PC2", "PC3"]


def test_unknown_badge():
    d = decide(make_config(), "FFFF", "PC1", {"PC1": False})
    assert not d.allowed and d.badge_name is None


def test_disabled_badge():
    d = decide(make_config(enabled=False), "04a1b2", "PC1", {"PC1": False})
    assert not d.allowed


def test_tap_on_unlocked_pc_locks_all_unlocked_targets():
    d = decide(make_config(), "04A1B2", "PC1", {"PC1": False, "PC2": False, "PC3": True, "OTHER": False})
    assert d.allowed and d.action == LOCK
    assert d.commands == ["PC1", "PC2"]


def test_tap_on_locked_pc_unlocks_all_locked_targets():
    d = decide(make_config(), "04A1B2", "PC1", {"PC1": True, "PC2": True, "PC3": False})
    assert d.allowed and d.action == UNLOCK
    assert d.commands == ["PC1", "PC2"]


def test_offline_pcs_are_ignored():
    d = decide(make_config(), "04A1B2", "PC1", {"PC1": True})
    assert d.commands == ["PC1"]


def test_tap_on_foreign_reader_locks_if_any_unlocked():
    d = decide(make_config(), "04A1B2", "ACCUEIL", {"ACCUEIL": False, "PC1": True, "PC2": False})
    assert d.action == LOCK and d.commands == ["PC2"]
    d = decide(make_config(), "04A1B2", "ACCUEIL", {"ACCUEIL": False, "PC1": True, "PC2": True})
    assert d.action == UNLOCK and d.commands == ["PC1", "PC2"]


def test_rights_are_enforced():
    d = decide(make_config(can_unlock=False), "04A1B2", "PC1", {"PC1": True})
    assert not d.allowed and d.action == UNLOCK
    d = decide(make_config(can_lock=False), "04A1B2", "PC1", {"PC1": False})
    assert not d.allowed and d.action == LOCK


def test_no_target_online():
    d = decide(make_config(), "04A1B2", "ACCUEIL", {"ACCUEIL": False})
    assert not d.allowed
