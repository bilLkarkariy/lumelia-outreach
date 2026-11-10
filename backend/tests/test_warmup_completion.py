from datetime import timedelta

from sqlalchemy import select

from app import domain as d
from app import mail
from app import models as m
from app import warmup_completion as wc
from app.db import SessionLocal
from app.warmup_policy import config
from tests.test_native_warmup import setup
from tests.test_warmup_progress import seed_receipts


def test_group_completion_notifies_once_only_after_all_goals_and_preserves_cold_pause(client, monkeypatch):
    ids = setup(client)
    with SessionLocal() as db:
        for index, key in enumerate(ids):
            row = seed_receipts(db, key, days=1 if index == 2 else 14)
            row.min_interval_seconds = 60
        db.commit()
    # Use the actual administrator endpoint; the unfinished third box must prevent an early email.
    configured = client.put(
        "/api/mailbox-warmup/completion",
        json={
            "recipient": "recipient@example.com",
            "mailbox_ids": ids,
            "sender_id": ids[0],
            "enabled": True,
        },
    )
    assert configured.status_code == 200, configured.text
    sent = []

    class SMTP:
        def send_message(self, message, **kwargs):
            sent.append((message, kwargs))
            return {}

        def quit(self):
            pass

    monkeypatch.setattr(mail, "connect_smtp", lambda _: SMTP())
    wc.tick()
    assert sent == []
    with SessionLocal() as db:
        assert all(config(db.get(m.SendingMailbox, key))["enabled"] for key in ids)
        jobs = list(
            db.scalars(
                select(m.Job).where(
                    m.Job.kind == "mailbox_warmup", m.Job.payload["sender_id"].as_string() == ids[2]
                )
            )
        )
        for day, job in enumerate(jobs[:14]):
            job.created_at = m.now() - timedelta(days=day, minutes=5)
            job.result = {**job.result, "received_at": job.created_at.isoformat()}
        state = d.state(db)
        cfg = state.settings[wc.SETTING]
        state.settings = {
            **state.settings,
            wc.SETTING: {**cfg, "checked_at": (m.now() - timedelta(minutes=2)).isoformat()},
        }
        db.commit()
    wc.tick()
    wc.tick()  # Re-entry simulates the next worker cycle: no second delivery intent.
    assert len(sent) == 1
    message, transport = sent[0]
    assert transport["to_addrs"] == ["recipient@example.com"]
    assert "warm-up terminé" in message["Subject"]
    assert "ne certifie pas" in message.get_content()
    with SessionLocal() as db:
        notices = list(db.scalars(select(m.Job).where(m.Job.kind == wc.NOTICE_KIND)))
        assert len(notices) == 1 and notices[0].status == "completed"
        assert notices[0].result["smtp_accepted"] is True
        assert notices[0].result["delivery_confirmed"] is False
        assert all(not config(db.get(m.SendingMailbox, key))["enabled"] for key in ids)
        assert all(db.get(m.SendingMailbox, key).paused for key in ids)
        assert d.state(db).paused
        assert d.state(db).settings[wc.SETTING]["completed_at"]
        assert all(not config(db.get(m.SendingMailbox, key)).get("validation") for key in ids)
