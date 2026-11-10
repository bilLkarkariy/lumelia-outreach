# Phone routing

Calls are optional and off unless the Twilio variables of `backend/.env.example` are set.
Code: `app/calls.py`, `app/phone_routing.py`, `app/call_processing.py`, `app/call_storage.py`.

## Three ways to call

| Mode | Entry point | Flow |
| --- | --- | --- |
| Browser | `POST /api/calls`, then `POST /api/calls/{id}/token` | The operator calls from the contact page with a five-minute access token. |
| Operator's mobile | `POST /api/calls/mobile` | The provider rings the operator's verified mobile. The operator presses 1 (recorded) or 2 (not recorded) and is bridged to the destination. |
| Inbound | `POST /api/calls/provider/incoming` | The caller hears the recording notice and can press 2 to refuse recording. The operator's mobile rings and the operator presses 1 to accept. |

A recording made outside the tool can be attached with `POST /api/calls/upload`.

## Rules

- **Intent first.** The call row is committed before the provider is asked to dial, and a call that
  can no longer be placed (contact paused or suppressed in the meantime) is refused when the
  operator confirms.
- **Signed webhooks.** Every provider callback is verified against its `X-Twilio-Signature` with a
  constant-time comparison.
- **The caller ID and the operator number must differ.**
- **Unknown callers stay unlinked.** An inbound call from an unknown number is stored without a
  contact until a person links it (`POST /api/calls/{id}/link`).
- **A missed inbound call creates one follow-up**, keyed by the call so it cannot be duplicated.
- **Inbound routing can be switched off** in the workspace (`POST /api/calls/routing`) or by
  `TWILIO_INBOUND_ENABLED`.

## Recordings

Audio is encrypted with the workspace key before it is written, to a private local directory or to
an S3-compatible bucket. A recording is kept 30 days; the worker then deletes the stored objects.
Recording can be stopped during a call (`POST /api/calls/{id}/stop-recording`).

Transcription uses Deepgram when `DEEPGRAM_API_KEY` is set. The agent can then draft a post-call
report, which a person reviews (see [AGENT.md](AGENT.md)).
