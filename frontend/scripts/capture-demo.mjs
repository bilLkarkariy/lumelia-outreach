/*
 * Captures the README screenshots and the walkthrough video from the seeded demo stack.
 *
 *   docker compose up --build -d           # from the repository root, fresh demo data
 *   node scripts/capture-demo.mjs          # screenshots only (read-only)
 *   node scripts/capture-demo.mjs --video  # screenshots, then the scenario of docs/DEMO.md
 *
 * The video run changes the demo data (it validates a contact and logs a reply): recreate the stack
 * with `docker compose down -v` before recording again. Records are looked up by the names the seed
 * script gives them, never by identifier.
 */
import {mkdir, readdir, rename, rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const BASE = process.env.DEMO_URL ?? 'http://localhost:8080';
const PASSWORD = process.env.LUMELIA_DEMO_PASSWORD ?? 'demo-password-1234';
const DOCS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../docs');
const VIEWPORT = {width: 1440, height: 900};
const READ = 6000;

async function login(page) {
  await page.goto(BASE);
  await page.fill('input[type=password]', PASSWORD);
  await page.keyboard.press('Enter');
  await page.waitForLoadState('networkidle');
}

async function records(page) {
  const get = async (route) => (await page.request.get(BASE + '/api' + route)).json();
  const [contacts, campaigns, accounts, inbox] = await Promise.all([
    get('/contacts'),
    get('/campaigns'),
    get('/accounts'),
    get('/inbox'),
  ]);
  const contact = (name) => contacts.items.find((row) => row.first_name === name).id;
  return {
    antoine: contact('Antoine'),
    antoineSequence: contacts.items.find((row) => row.first_name === 'Antoine').campaign_id,
    marc: contact('Marc'),
    ines: contact('Inès'),
    company: accounts.find((row) => row.name === 'Belmorin Ingénierie').id,
    sequence: campaigns.find((row) => row.name.startsWith('Cabinets')).id,
    reply: inbox.items.find((row) => row.kind === 'positive_reply').id,
  };
}

async function open(page, route) {
  await page.goto(BASE + route);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(500);
}

const settings = (page, label) =>
  page.getByRole('group', {name: 'Rubriques des réglages'}).getByRole('button', {name: label}).click();

/** Mailbox table with its ramp-up caps, and the caps shared by domain and mail provider. */
async function mailboxes(page) {
  await open(page, '/?view=senders');
  await page.getByText('Plafonds partagés', {exact: true}).click();
  await page
    .getByRole('heading', {name: 'Boîtes d’envoi'})
    .evaluate((heading) => heading.scrollIntoView({block: 'start'}));
  await page.waitForTimeout(500);
}

async function screenshots(page, ids) {
  const out = path.join(DOCS, 'screenshots');
  await mkdir(out, {recursive: true});
  const shot = (name) => page.screenshot({path: path.join(out, name + '.png')});
  await open(page, '/');
  await shot('pipeline');
  await open(page, '/contacts/' + ids.ines + '?tab=emails');
  await shot('contact');
  await open(page, '/sequences/' + ids.sequence);
  await shot('sequence');
  await open(page, '/inbox/' + ids.reply);
  await shot('inbox');
  await mailboxes(page);
  await shot('mailboxes');
  await open(page, '/settings');
  await settings(page, 'Journal');
  await page.waitForTimeout(800);
  await shot('journal');
}

/** English caption over the French interface; survives in-app navigation, not page loads. */
async function caption(page, title, text) {
  await page.evaluate(
    ([heading, body]) => {
      const box = document.getElementById('demo-caption') ?? document.createElement('div');
      box.id = 'demo-caption';
      box.style.cssText =
        'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:2147483647;' +
        'max-width:880px;padding:14px 22px;border-radius:12px;background:rgba(17,24,39,.94);' +
        'color:#fff;font:500 17px/1.45 system-ui,sans-serif;box-shadow:0 12px 32px rgba(0,0,0,.3)';
      box.innerHTML = '';
      const strong = document.createElement('strong');
      strong.textContent = heading + ' ';
      box.append(strong, body);
      document.body.append(box);
    },
    [title, text],
  );
  await page.waitForTimeout(READ);
}

async function scenario(page, ids) {
  const scene = async (route, title, text) => {
    await open(page, route);
    await caption(page, title, text);
  };

  await scene(
    '/',
    '1 · Discovery and enrichment.',
    'Each company gets a prospect profile: the public site is read, the fit is scored, and only then is a contact looked up.',
  );
  await scene(
    '/companies/' + ids.company,
    '1 · Discovery and enrichment.',
    'The company page keeps the evidence behind the score and the source of every field.',
  );
  await page.mouse.wheel(0, 500);
  await page.waitForTimeout(READ);

  await scene(
    '/contacts/' + ids.antoine + '?tab=qualification',
    '2 · Qualifying a contact.',
    'A contact is eligible only with a documented role, a documented source and a verified address.',
  );
  await page.getByRole('button', {name: 'Valider le contact'}).scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500);
  await page.getByRole('button', {name: 'Valider le contact'}).click();
  await caption(page, '2 · Qualifying a contact.', 'A person validates it. One active contact per company.');

  await scene(
    '/sequences/' + ids.antoineSequence,
    '3 · Preparing a sequence.',
    'Each campaign has an ordered sequence of email, call and LinkedIn steps. Saving it creates a new version.',
  );
  await scene(
    '/contacts/' + ids.antoine + '?tab=emails',
    '3 · Preparing a sequence.',
    'Preparing renders the steps for this contact as drafts.',
  );
  await page.getByRole('button', {name: 'Préparer'}).click();
  await page.waitForTimeout(2500);
  await page.locator('.cp-email-summary').first().click();
  await page.waitForTimeout(2500);
  await page.getByRole('button', {name: 'Valider cet email'}).click();
  await caption(
    page,
    '3 · Preparing a sequence.',
    'A person validates each email. In this demo sending is simulated: nothing leaves the machine.',
  );

  await scene(
    '/contacts/' + ids.marc + '?tab=emails',
    '4 · A reply stops the follow-ups.',
    'This contact received the first email (simulated) and has a follow-up queued.',
  );
  await page.getByRole('button', {name: 'Ajouter un échange'}).first().click();
  await page.getByLabel('Type d’échange').selectOption('reply');
  await page.getByLabel('Compte rendu').fill('Asked to be called back next week about the pipeline review.');
  await caption(
    page,
    '4 · A reply stops the follow-ups.',
    'In production the IMAP collector records replies. Here the same event is logged by hand.',
  );
  await page.getByRole('button', {name: 'Ajouter à l’historique'}).click();
  await page.waitForLoadState('networkidle');
  await scene(
    '/contacts/' + ids.marc + '?tab=emails',
    '4 · A reply stops the follow-ups.',
    'The queued follow-up is cancelled, the company is paused and a review task is created.',
  );
  await scene(
    '/inbox',
    '4 · A reply stops the follow-ups.',
    'Replies, out-of-office notices, bounces and opt-outs land in one inbox.',
  );

  await mailboxes(page);
  await caption(
    page,
    '5 · Budgets and cadence.',
    'Mailboxes have their own daily cap; domains and mail providers have theirs.',
  );
  await open(page, '/settings');
  await settings(page, 'Warm-up');
  await caption(
    page,
    '5 · Budgets and cadence.',
    'A new mailbox starts at 5 emails a day and earns each increase with healthy days and a human approval.',
  );
  await settings(page, 'Budget');
  await caption(
    page,
    '5 · Budgets and cadence.',
    'Spend is reserved against the campaign, monthly and provider ceilings before any paid call.',
  );
  await settings(page, 'Journal');
  await caption(
    page,
    '5 · Budgets and cadence.',
    'A purchase whose outcome is unknown stays “uncertain”: it is reconciled by a person, never retried.',
  );
}

const browser = await chromium.launch();
const still = await browser.newContext({viewport: VIEWPORT, locale: 'fr-FR'});
const page = await still.newPage();
await login(page);
const ids = await records(page);
await screenshots(page, ids);
await still.close();

if (process.argv.includes('--video')) {
  const dir = path.join(DOCS, 'demo');
  const raw = path.join(dir, 'raw');
  await mkdir(raw, {recursive: true});
  const context = await browser.newContext({
    viewport: VIEWPORT,
    locale: 'fr-FR',
    recordVideo: {dir: raw, size: VIEWPORT},
  });
  const recorded = await context.newPage();
  await login(recorded);
  await scenario(recorded, ids);
  await context.close();
  const [file] = await readdir(raw);
  await rename(path.join(raw, file), path.join(dir, 'walkthrough.webm'));
  await rm(raw, {recursive: true});
}
await browser.close();
