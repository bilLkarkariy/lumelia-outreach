# Demo: a two-minute walkthrough

The walkthrough runs on the seeded demo stack: invented companies and people under `example.com`,
phone numbers in the range reserved for fiction, campaigns in simulation. The application containers
have no route to the outside, so nothing is sent and no provider is called.

- Video: [demo/walkthrough.mp4](demo/walkthrough.mp4) (about two minutes, no sound, English captions
  over the French interface)
- Preview: [demo/walkthrough.gif](demo/walkthrough.gif)

## What the demo cannot show

- Mailbox and integration connection checks need the network and fail by design; mailboxes stay
  "to verify".
- No email leaves the machine, so no reply can come back over IMAP. The reply in scene 4 is logged
  through the same endpoint the interface uses to record an answer by hand; from there on the code
  path is the one an IMAP reply takes.
- The agent runner is not part of the stack.
- Amounts and counts are placeholders, not results.

## Script

| # | Time | Screen | Action | Caption |
| --- | --- | --- | --- | --- |
| 1 | 0:00 | Pipeline (`/`) | Show the company list with status and score. | Each company gets a prospect profile: the public site is read, the fit is scored, and only then is a contact looked up. |
| 1 | 0:07 | Company page, Belmorin Ingénierie | Scroll to the evidence. | The company page keeps the evidence behind the score and the source of every field. |
| 2 | 0:20 | Contact page, Antoine, Qualification tab | Click "Valider le contact". | A contact is eligible only with a documented role, a documented source and a verified address. A person validates it. One active contact per company. |
| 3 | 0:36 | Sequence editor of Antoine's campaign | Show the ordered steps. | Each campaign has an ordered sequence of email, call and LinkedIn steps. Saving it creates a new version. |
| 3 | 0:43 | Contact page, Antoine, Emails tab | Click "Préparer", open the first draft, click "Valider cet email". | Preparing renders the steps for this contact as drafts. A person validates each email. Sending is simulated. |
| 4 | 1:02 | Contact page, Marc, Emails tab | Show the simulated first email and the queued follow-up. Click "Ajouter un échange", choose a reply, type a note, save. | In production the IMAP collector records replies. Here the same event is logged by hand. |
| 4 | 1:17 | Same page, reloaded | Show the cancelled follow-up. | The queued follow-up is cancelled, the company is paused and a review task is created. |
| 4 | 1:24 | Inbox (`/inbox`) | Show the list. | Replies, out-of-office notices, bounces and opt-outs land in one inbox. |
| 5 | 1:31 | Mailboxes (`/?view=senders`) | Open "Plafonds partagés". | Mailboxes have their own daily cap; domains and mail providers have theirs. |
| 5 | 1:38 | Settings, Warm-up | — | A new mailbox starts at 5 emails a day and earns each increase with healthy days and a human approval. |
| 5 | 1:45 | Settings, Budget | — | Spend is reserved against the campaign, monthly and provider ceilings before any paid call. |
| 5 | 1:51 | Settings, Journal | Show the uncertain enrichment job. | A purchase whose outcome is unknown stays "uncertain": it is reconciled by a person, never retried. |

Times are approximate.

## Recording it again

```bash
docker compose down -v && docker compose up --build -d   # fresh demo data
cd frontend
npm ci && npx playwright install chromium
node scripts/capture-demo.mjs            # screenshots only, read-only
node scripts/capture-demo.mjs --video    # screenshots, then the scenario above
```

The script writes `docs/screenshots/*.png` and `docs/demo/walkthrough.webm`. The video run changes
the demo data, so recreate the stack before recording again. The committed files are converted with
ffmpeg:

```bash
cd docs/demo
ffmpeg -i walkthrough.webm -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p -movflags +faststart -an walkthrough.mp4
ffmpeg -i walkthrough.webm -vf "fps=3,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" walkthrough.gif
```

## Demo data

`backend/scripts/seed_demo.py` creates the workspace through the HTTP API of the running
application: 12 companies, 10 contacts, 2 campaigns with their sequences, 2 mailboxes, budgets,
simulated sends, answers in the inbox and one enrichment job left `uncertain` with its reserved
budget. It does nothing on a workspace that already has data.
