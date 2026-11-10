# Security model

The tool is a single-workspace application: one shared password, one administrator session type.
It is meant to run behind a TLS-terminating proxy. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## Authentication and sessions (`app/security.py`, `app/main.py`)

- The workspace password is stored as an Argon2 hash (12 to 200 characters), set with
  `scripts/set_access_password.py`. The API refuses to start without it.
- Login is throttled: 8 failures from one address within 15 minutes block further attempts.
- A session token is 48 random bytes. Only its SHA-256 digest is stored, so a database leak does not
  yield usable sessions. Sessions expire after `LUMELIA_SESSION_HOURS` (12 by default).
- The cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` when `LUMELIA_COOKIE_SECURE` is true.
  Production refuses to start without secure cookies and an encryption key.

## Request forgery

- Every state-changing request must carry the session's CSRF token in `X-CSRF-Token`.
- State-changing requests with an `Origin` outside `LUMELIA_ALLOWED_ORIGINS` are rejected. Provider
  webhooks and the unsubscribe form are exempt and authenticated differently (signature, encrypted
  token).
- Responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.

## Secrets at rest

- Provider API keys, mailbox credentials and call audio are encrypted with Fernet under
  `LUMELIA_ENCRYPTION_KEY`. Serialisation drops encrypted configuration, password and token hashes
  from every API response.
- Agent tokens are stored as SHA-256 digests and can be revoked.
- Backups are encrypted with the same key (see [BACKUP.md](BACKUP.md)).

## Outbound requests

- Company sites are fetched with a guard against server-side request forgery: each redirect hop must
  stay on the company's domain and resolve to a public IP address, with size and time limits
  (`app/prospect_sources.py`).
- `LUMELIA_LIVE_ACTIONS_ENABLED=false` blocks SMTP sends, paid enrichment and calls.
  `LUMELIA_OFFLINE=true` additionally stops every background job that opens a connection.

## Webhooks and public endpoints

- Telephony webhooks are verified against the provider signature with a constant-time comparison.
- The unsubscribe link carries an encrypted token; `GET` never changes state.

## The agent

The agent runner authenticates with a bearer token, works under a 12-minute lease and can only read
the context of the task it claimed and save a result for it. It has no tool to send, spend or
approve (see [AGENT.md](AGENT.md)).

## Personal data

- Sources are recorded for every contact field; a contact without a documented source is not
  eligible for sending.
- Contacts expire after 365 days (`app/privacy.py`), call recordings after 30.
- An opt-out suppresses the contact and the address on every channel.
- Protected sender domains and addresses can never be used for cold email.
