# Sending: mailboxes, ramp-up, cadence and replies

Email leaves through the workspace's own mailboxes over SMTP, and replies come back over IMAP.
Code: `app/senders.py`, `app/ramp.py`, `app/native_warmup.py`, `app/warmup_policy.py`,
`app/worker.py`, `app/mail.py`, `app/domain.py`.

## Mailbox readiness

`senders.readiness` returns the reasons a mailbox may not send. All must be cleared:

- the mailbox is not paused;
- its preparation is validated, or ramp-up is enabled;
- the SMTP and IMAP connection check passed;
- neither the sender nor the reply-to address is protected (`LUMELIA_PROTECTED_SENDER_DOMAINS`,
  `LUMELIA_PROTECTED_SENDER_ADDRESSES`), which keeps cold email off the company's primary domain;
- SPF, DKIM and DMARC were confirmed;
- the legal footer and an `https` privacy page are filled in;
- for live sending, reply collection ran within the last 10 minutes.

Credentials are stored encrypted. A contact keeps the same mailbox once anything was attempted for
them; changing the mailbox earlier sends approved messages back to draft for a new review.

## Ramp-up (`app/ramp.py`)

| Rule | Value |
| --- | --- |
| Starting cap | 5 emails per day |
| Increase | +5 after 3 healthy days |
| Healthy day | accepted sends ≥ max(3, half the cap), no error, no bounce |
| Error or bounce day | cap halved, never below 5 |
| More than 7 idle days | back to 5 |

The effective cap is the lowest of the target, the computed cap and the cap a person last approved.
Approval requires an external placement check recorded by that person; without it the mailbox stays
at 5. SMTP acceptance is not treated as an inbox-placement signal, and the tool generates no
synthetic opens or replies.

## Native warm-up (`app/warmup_policy.py`, `app/native_warmup.py`)

Technical test messages between the workspace's own mailboxes, over the same SMTP and IMAP path as
real mail: 2 per day at first, +2 after every 2 healthy days, 10 at most, reply after 15 minutes.
The default plan targets 14 active days and 50 received messages. It checks that the path works and
makes no promise about reputation or placement.

## Cadence

Before each send `senders.limits_reasons` checks, in this order:

1. ramp-up blockers;
2. the mailbox's cap over the last 24 hours;
3. the minimum interval since the mailbox's last send or warm-up test;
4. the daily cap of the sending domain, then of the mail provider, shared by their mailboxes;
5. the workspace cap over 24 hours (a fixed number, or the sum of the caps of ready mailboxes);
6. the campaign cap over 24 hours.

A campaign also has sending weekdays and hours. A blocked send is postponed by a minute, not
cancelled.

## Send path (`worker.tick`)

1. Jobs found `running` for more than 3 minutes become `uncertain`.
2. One queued job is claimed with `SKIP LOCKED`, so several workers can run.
3. `send_reasons` re-checks contact eligibility, the workspace pause, the campaign status and the
   message status. Any reason cancels the job and the message.
4. **Simulation**: the job completes, the message becomes `simulated`, an `email_simulated` event is
   written, and no SMTP connection is opened.
5. **Live**: requires `LUMELIA_LIVE_ACTIONS_ENABLED`, an approved budget and an `https` public URL,
   then mailbox readiness, cadence and the sending window.
6. The intent is committed first: message `sending` with its `Message-ID`, job `running`.
7. Every check runs a second time in a new transaction, then the message is sent.
8. Accepted: `sent`. Refused before transmission: `failed`. Anything after transmission started
   without a clear answer: `uncertain`. The worker only picks `queued` jobs, so an uncertain send is
   never repeated.

## Eligibility (`domain.eligibility`)

A contact can be sent to only if every condition holds: retention date not passed, company at the
prospect stage and not paused, contact not paused or suppressed, contact approved by a person,
first approved contact of the company (one active contact per company), language and offer match,
role and relevance documented, source documented, email verified within the last 90 days.

## Replies (`mail.parse_reply`, `domain.receive_event`)

An incoming message is matched to a sent one through `In-Reply-To` and `References`, falling back to
the sender's address. It is deduplicated on its `Message-ID`.

| Event | How it is detected | Effect |
| --- | --- | --- |
| Reply | Default | Contact and company paused, every pending message, job and task of the company cancelled on all channels, review task created |
| Out of office | `Auto-Submitted` header | Same as a reply |
| Bounce | Delivery status report with a failed action | Address marked invalid, pending emails cancelled |
| Opt-out | Unsubscribe link, or a reply classified as an opt-out by a person in the inbox | Contact and address suppressed for every channel, pending messages cancelled |

Every email carries a `List-Unsubscribe` link with an encrypted token. `GET /api/unsubscribe/{token}`
shows a confirmation form and changes nothing, so link scanners cannot unsubscribe anyone;
`POST` records the opt-out.

## Sequences (`app/sequence_editor.py`)

A sequence has at most 10 steps on day 0 to 180, each an email, a call or a LinkedIn action. Email
steps are refused if they contain a price. Saving uses optimistic concurrency and an idempotent
request identifier. A campaign that already has contacts, messages or tasks is never edited in
place: saving creates a new version whose budget approval is reset, and existing messages stay
attached to the version they were written for.

## Retention (`app/privacy.py`)

A contact gets a retention date 365 days after creation, extended when they reply to an automatic
campaign. Past that date
the contact cannot be sent to; for automatic campaigns the worker erases expired contacts with
their events, calls and tasks, 100 per pass.
