# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/bilLkarkariy/lumelia-outreach/security/advisories/new),
not in a public issue. Include the affected file or endpoint and the steps to reproduce.

## Scope

This repository is the public copy of an internal tool that is no longer deployed. Reports are
welcome on the code as published; there is no hosted instance to test against.

## Notes for anyone running it

- The values in `compose.yaml` (encryption key, password hash, database password) are public demo
  values. Generate your own from `backend/.env.example` for anything else.
- Keep `LUMELIA_LIVE_ACTIONS_ENABLED=false` until mailboxes, budgets and legal notices are reviewed.
- The design is described in [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).
