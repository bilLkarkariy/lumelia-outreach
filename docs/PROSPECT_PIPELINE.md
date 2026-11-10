# Prospect pipeline

The pipeline decides which companies deserve a contact lookup. It reads public information, scores
the fit against the campaign's criteria and spends paid enrichment only on the companies that pass.
Code: `app/prospect_pipeline.py`, `app/campaign_qualification.py`, `app/prospect_sources.py`,
`app/prospect_enrichment.py`.

## Statuses

Each company has one `ProspectProfile`.

| Status | Meaning |
| --- | --- |
| `pending` | Waiting to be checked. |
| `checking` | Claimed by the worker. Returned to `pending` if the claim is older than 5 minutes. |
| `review` | Checked, but a criterion could not be confirmed. A person decides. |
| `qualified` | Checked and every criterion matches. No contact yet. |
| `contact_found` | At least one contact is attached. |
| `ready` | A phone number exists and every criterion matches. |
| `not_fit` | A criterion contradicts the campaign, or the reviewer said so. |
| `excluded` | The company must not be contacted (see below). |

`excluded` wins over everything: `pipeline.allowed` returns false when the company is past the
prospect stage, has outreach paused, or has any contact that is paused or suppressed.

## What is read

The worker handles one profile per pass.

- **The company's own site** (`prospect_sources.collect_site`): the home page and at most two pages
  whose link looks like legal notice, contact, team or about. Each hop is checked to stay on the
  company's domain and on a public IP address, with at most 4 redirects, 500 kB per page, HTML only,
  4 s to connect and 8 s to read.
- **The French company register** (`recherche-entreprises.api.gouv.fr`): legal status, headcount
  band and up to three directors.

Every stored fact keeps its source. Excerpts are capped (800 characters per excerpt, 18,000 in
total).

## Score

`campaign_qualification.assess` returns a score out of 100:

- **Fit, 60 points**, shared between the criteria that match in proportion to their weight: country
  10, size 15, B2B 15, industry 15, CRM 20 (only when the campaign targets a CRM). An inactive
  company is a mismatch.
- **Reachability, 25 points**: direct line 15 (another phone number 8), email 10.
- **Freshness, 15 points**: checked within the last 90 days with at least one recorded source.

`not_fit` and `excluded` profiles score 0.

## Enrichment gate

`prospect_enrichment` buys contact details only for profiles in status `ready` with a score of 55 or
more, highest score first, within 20 credits per run. It requires an authenticated integration and
the pipeline's `auto_enrich` setting. See [ACQUISITION.md](ACQUISITION.md) for the purchase rules.

## Configuration and routes

`POST /api/prospect-pipeline/config` (admin) stores `campaign_id`, `enabled` and `auto_enrich`.
A missing setting means off.

| Route | Purpose |
| --- | --- |
| `GET /api/lists/prospects` | Paginated pipeline view (25 per page, 100 at most). |
| `GET /api/prospect-pipeline/accounts/{account_id}` | Profile, evidence and score detail for one company. |
| `POST /api/prospect-pipeline/accounts/{account_id}/calls` | Log a call made to the company. |
