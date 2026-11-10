# Agent runner and MCP server

The agent does the research and drafting work a person would otherwise do between two decisions:
qualifying a company from public sources, finding decision-makers, drafting a reply, writing a
post-call report. It never acts on the outside world. Code: `agent/` (runner) and
`backend/app/agent_actions.py`, `backend/app/agent_budget.py` (server side).

The demo stack does not start the agent: it needs an engine login and network access.

## Shape

```
API  ── queue of agent actions (PostgreSQL)
 ▲
 │  claim / context / result   (bearer token + lease)
 │
runner (agent/server.py)  ── one CLI process per task ── MCP server (agent/mcp_server.py)
```

- The **server** owns the queue. A task is claimed with `SKIP LOCKED` under a 12-minute lease whose
  secret is stored hashed; the runner sends heartbeats. A second runner cannot take the same task.
- The **runner** polls two independent lanes, `post_call` then `research`, and starts one headless
  coding-agent CLI process per task. The process gets an allow-listed environment, a temporary
  working directory and the task context in its prompt.
- The **MCP server** is the only way for that process to write back. It is scoped to the claimed
  task through the lease.

## Tools exposed to the model

| Tool | Available | Effect |
| --- | --- | --- |
| `get_action_context` | When no task kind is set | Reads the claimed task, its target, the offer, the evidence and the constraints. |
| `check_mailbox_connection` | Operations checks only | Tests SMTP and IMAP with the stored credentials. Sends nothing, reveals no secret. |
| `save_action_result` | Always | Saves the result, validated against a JSON schema that depends on the task kind (`agent/result-schema.json`). |

There is no tool to send an email, place a call, spend budget, change a limit or approve anything.
A result that needs a decision is stored as waiting for a person
(`POST /api/agent/actions/{id}/decision`: resume, complete or cancel, with a note). Text read from
emails and web pages is passed as untrusted data, and the system prompt says so.

Task kinds: `qualify_account`, `qualify_contact`, `qualify_batch`, `discover_leads`, `reply_draft`,
`review_task`, `mission_step`, `post_call_report`, `operations_check`.

## Budget

| Limit | Default |
| --- | --- |
| Tokens per task | 200,000 |
| Tokens per day | 1,000,000 |
| Wall time per task | 600 s (1,800 s for a post-call report) |
| Turns per task | 6 (12 for a batch qualification) |
| Output tokens | 2,500 (8,000 for a batch qualification) |

The runner meters the token usage reported by the CLI and stops the process when a limit is
reached. A task stopped for budget keeps status `budget` and resumes the next day. Batches save
partial results so finished companies are not researched twice.

## Configuration

The engine is a deployment choice, so nothing about it is hard-coded.

| Variable | Purpose |
| --- | --- |
| `LUMELIA_URL` | Base URL of the API. |
| `LUMELIA_AGENT_TOKEN` | Bearer token created in Settings (stored hashed, revocable). Required. |
| `LUMELIA_AGENT_CLI` | Executable of the coding-agent CLI. Required. |
| `LUMELIA_AGENT_MODELS` | JSON map from tier (`fast`, `standard`, `deep`) to a model name the CLI accepts. |
| `LUMELIA_AGENT_CLI_ENV` | JSON map of extra variables for the CLI process. |
| `LUMELIA_AGENT_PASSTHROUGH_ENV` | Comma-separated variables forwarded to the CLI unchanged. |
| `LUMELIA_AGENT_OUTPUT_TOKENS_ENV` | Name of the variable through which the CLI accepts an output-token ceiling. |
| `LUMELIA_AGENT_FORBIDDEN_ENV` | Comma-separated variables that must be absent. The runner refuses to start if one is set, which keeps pay-per-use credentials out of a runner budgeted for a flat-rate login. |
| `LUMELIA_AGENT_CREDENTIALS` | Engine login as a JSON object, written once to `LUMELIA_AGENT_CREDENTIALS_FILE` on a private volume. |
| `LUMELIA_AGENT_RUNTIME` | Directory for per-task temporary files. |

Build the image with the CLI package pinned:

```bash
docker build -f agent/Dockerfile --build-arg AGENT_CLI_PACKAGE=<package>@<version> -t lumelia-agent .
```

`boot.py` writes the login, drops to an unprivileged user and starts the runner. The runner only
relies on the CLI's stream-json protocol and on MCP.
