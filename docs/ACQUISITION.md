# Acquisition and enrichment

How companies and contacts enter the workspace, and what protects the budget while they do.
Every paid step follows the same four rules:

1. **Exclusions first.** A company or contact that is paused, already a customer, suppressed or on a
   protected domain is dropped before any credit is spent, and the check is repeated right before
   the network call.
2. **Idempotent jobs.** Each purchase has a deterministic key. Asking twice returns the existing job.
3. **Intent before the call.** The job is committed as `running` before the request leaves, so a
   crash cannot hide a purchase.
4. **Uncertain is terminal for the machine.** A timeout, a missing provider identifier or a job
   found `running` after three minutes becomes `uncertain`. It is never retried; a person reconciles
   it (`POST /api/jobs/{id}/reconcile`).

## Sources

| Source | Module | What it brings | Limits in code |
| --- | --- | --- | --- |
| CSV import | `domain.import_csv` | Companies and contacts from a file | 1,000 rows per file; duplicates are counted, not created |
| Google Places (Apify actor) | `app/acquisition.py` | Companies and their switchboard number, never named people | $0.50 per run, $5 per month, 100 places per batch, one run per hour |
| Hunter | `app/autopilot.py` | Companies, then decision-makers, then address verification | Free plan only, 20 credits per campaign and overall by default |
| Apollo search | `app/discovery.py` | Candidate contacts a person accepts one by one | Approved budget and live mode required |
| FullEnrich | `app/prospect_enrichment.py`, `app/enrichment.py` | Contact details for profiles that already passed qualification | 20 credits per run, score of 55 or more |

Provider API keys are entered in Settings and stored encrypted. `providers.verify_connection` keeps
only the quota figures the provider returns.

## The Hunter cascade (`app/autopilot.py`)

Three stages, each one a job:

| Stage | Call | Cost | Output |
| --- | --- | --- | --- |
| `discover` | `POST /v2/discover` with the campaign filters | 0 | Company domains. One free round per ISO week and filter set. |
| `domain` | `GET /v2/domain-search` (personal addresses, executive and sales departments, executive seniority, full name and position required) | 1 credit | At most two contacts per domain, filtered by `relevant_role` (decision-maker titles in French, English, German and Dutch). |
| `verify` | `GET /v2/email-verifier` | 0.5 credit | `valid`, `risky` or `invalid`. |

Guards, in the order they run:

- `domain_excluded` drops protected sender domains, companies that are not at the `prospect` stage
  or have outreach paused, and suppressed domains. It runs when the job is picked and again just
  before the request.
- Before `verify`, a contact that is paused or suppressed, or whose company changed stage, cancels
  the job.
- The campaign and the workspace each have a credit ceiling (`free_credit_limit`, default 20) and a
  daily contact ceiling (`contacts_per_day`, default 200). A domain search starts only if the
  ceiling also leaves room to verify the contacts it will return. The balance check refuses to
  continue unless the Hunter plan is the free one.
- The job key is `auto:<campaign>:<stage>:<sha256 of the input>`. A second request for the same
  input returns the first job.
- If the provider configuration or credential changed between intent and execution, the job is
  cancelled rather than run with settings nobody approved.
- An HTTP 202 puts the job in `waiting` for a minute; after 20 polls it becomes `uncertain`.

## Provider adapters (`app/providers.py`)

- **Hunter**: verification of an address the workspace already holds.
- **Apollo**: `people/match` with personal emails and phone numbers explicitly not revealed. A match
  goes to human review unless the provider reports high confidence. Phone enrichment is refused.
- **FullEnrich**: asynchronous bulk request carrying the local job identifier in `custom.job_id`.
  A response without a provider identifier is `uncertain`. The poller waits for `FINISHED`; the
  result is accepted only if exactly one returned row carries the job identifier, otherwise the job
  is `uncertain`.

## Selective enrichment (`app/prospect_enrichment.py`)

Paid enrichment is spent last and on few records: only prospect profiles in status `ready` with a
score of 55 or more, highest score first, at most 20 credits per run, and only when the integration
is authenticated and the pipeline has `auto_enrich` enabled. The job key is
`prospect-enrich:<contact>:<fields>`.

## Manual enrichment jobs (`app/enrichment.py`)

Jobs requested from a contact page reserve budget when they are created. When the worker picks one
it re-checks the contact and the company; a paused, suppressed or no-longer-prospect record cancels
the job and releases the reservation. The same checks run again before the request, together with
the campaign's approved budget and the live-actions switch. Polling stops after 40 attempts.

## Place search (`app/acquisition.py`)

The actor is called with contact, social and review scraping turned off. A run is keyed by
`apify:<request id>`; replaying a key with a different payload is rejected with HTTP 409. Import
creates companies and company phone numbers only. Duplicate domains inside a batch are skipped, and
so are companies that already exist and are paused or past the prospect stage.

## Public addresses (`app/public_email.py`)

A campaign can allow addresses published by the company itself. Each one needs a human review
recorded within the last 90 days. Publication and DNS records prove provenance and a mail route,
not that a mailbox accepts mail.
