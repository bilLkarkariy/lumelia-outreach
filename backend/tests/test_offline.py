from app.worker import NETWORKED, background_groups

GROUPS = dict.fromkeys(["maintenance", "calls", "sourcing", "mailboxes", "warmup"], lambda: None)


def test_offline_worker_keeps_only_database_groups():
    assert set(background_groups(GROUPS, offline=True)) == {"maintenance"}
    assert set(background_groups(GROUPS, offline=False)) == set(GROUPS)
    assert set(GROUPS) >= NETWORKED
