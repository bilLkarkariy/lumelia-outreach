import {useEffect, useState} from 'react';
import {AlertTriangle, Clock3, Inbox, Mail, MailQuestion, Pause, Play, Send, ShieldCheck} from 'lucide-react';
import {WarmupProgress, type WarmupProgressData} from './WarmupProgress';
import './native-warmup.css';
import {errorText, type Api} from './api';

type Cadence = {
  daily_limit?: number;
  initial_daily?: number;
  max_daily?: number;
  increment?: number;
  healthy_days_per_step?: number;
  healthy_days?: number;
  healthy_days_until_increase?: number;
  next_daily_limit?: number;
  reply_delay_minutes?: number;
};
type Progress = WarmupProgressData & {accepted?: number; received?: number};
type Mailbox = {
  id: string;
  email: string;
  enabled: boolean;
  status: string;
  blockers: string[];
  progress?: Progress;
  daily_limit: number;
  attempted_24h: number;
  attempted_today?: number;
  accepted_today?: number;
  received_today?: number;
  replies_sent_24h?: number;
  originals_sent_24h?: number;
  last_sent_at?: string | null;
  last_received_at?: string | null;
  cadence?: Cadence;
  next_kind?: 'reply' | 'original' | null;
  accepted_7d: number;
  received_7d: number;
  replies_received_7d: number;
  replies_pending: number;
  failed_7d: number;
  next_at: string | null;
  history: Array<{
    id: string;
    at: string;
    recipient: string;
    status: string;
    is_reply: boolean;
    error: string;
    accepted: boolean;
    received_at: string | null;
    folder: string | null;
  }>;
};
const labels: Record<string, string> = {
  paused: 'En pause',
  blocked: 'À contrôler',
  awaiting_receipt: 'Réception à confirmer',
  scheduled: 'Planifié',
  queued: 'Réponse planifiée',
  completed: 'Réception confirmée',
  waiting: 'Accepté par SMTP',
  running: 'Transmission en cours',
  uncertain: 'Transmission incertaine',
  failed: 'Test en échec',
  cancelled: 'Annulé',
  no_partner: 'Aucun partenaire',
};
const date = (value?: string | null) =>
  value ? new Date(value).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'}) : '—';
const count = (value: unknown, fallback = 0) => (typeof value === 'number' ? value : fallback);

function mailboxState(row: Mailbox) {
  const blockers = row.blockers || [];
  const hasPartnerIssue =
    row.status === 'no_partner' || /partenaire|autre boîte|bo[iî]te disponible/i.test(blockers.join(' '));
  if (hasPartnerIssue)
    return {
      kind: 'empty',
      icon: <MailQuestion size={15} />,
      text: 'Aucun partenaire disponible pour un test réel',
    };
  if (!row.enabled) return {kind: 'paused', icon: <Pause size={15} />, text: 'Préparation en pause'};
  if (blockers.length || row.status === 'blocked')
    return {
      kind: 'incident',
      icon: <AlertTriangle size={15} />,
      text: 'Incident à contrôler avant le prochain test',
    };
  if (row.status === 'awaiting_receipt')
    return {kind: 'awaiting', icon: <Inbox size={15} />, text: 'En attente de réception confirmée'};
  if (row.next_kind === 'reply')
    return {kind: 'scheduled', icon: <Mail size={15} />, text: 'Réponse de contrôle en attente du créneau'};
  if (row.next_kind === 'original')
    return {kind: 'scheduled', icon: <Send size={15} />, text: 'Test initial en attente du créneau'};
  return {kind: 'scheduled', icon: <Clock3 size={15} />, text: 'Aucun prochain test planifié'};
}

function CadenceSummary({row}: {row: Mailbox}) {
  const cadence = row.cadence || {};
  const limit = count(cadence.daily_limit, row.daily_limit);
  const next = count(
    cadence.next_daily_limit,
    Math.min(limit + count(cadence.increment, 2), count(cadence.max_daily, 10)),
  );
  const healthy = count(cadence.healthy_days);
  const needed = count(cadence.healthy_days_per_step, 2);
  const remaining =
    typeof cadence.healthy_days_until_increase === 'number'
      ? cadence.healthy_days_until_increase
      : Math.max(needed - (healthy % needed), 0);
  const max = count(cadence.max_daily, 10);
  return (
    <div className="nw-cadence">
      <strong>
        Cadence : {limit} / {max} tests par jour
      </strong>
      {limit < max ? (
        <span>
          Prochain palier : {next} / j après {remaining} journée{remaining !== 1 ? 's' : ''} saine
          {remaining !== 1 ? 's' : ''} ({healthy} observée{healthy !== 1 ? 's' : ''}).
        </span>
      ) : (
        <span>Palier maximal atteint.</span>
      )}
    </div>
  );
}

export function NativeWarmup({
  api,
  admin,
  refresh,
}: {
  api: Api;
  admin: boolean;
  refresh: () => Promise<void>;
}) {
  const [rows, setRows] = useState<Mailbox[]>([]),
    [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string[]>([]),
    [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [loadError, setLoadError] = useState(''),
    [notice, setNotice] = useState('');
  async function load() {
    const data = await api('/mailbox-warmup');
    setRows(data.mailboxes);
    setLoading(false);
    setLoadError('');
  }
  useEffect(() => {
    let active = true;
    const update = () => {
      if (active)
        load().catch((e) => {
          setLoadError('Actualisation temporairement indisponible : ' + errorText(e));
          setLoading(false);
        });
    };
    update();
    const timer = setInterval(() => {
      if (!document.hidden) update();
    }, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- polling starts once on mount; load is recreated on every render
  }, []);
  async function command(action: 'start' | 'pause') {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const data = await api(
        '/mailbox-warmup/' + action,
        'POST',
        action === 'start' ? {mailbox_ids: selected, confirm_owned: confirmed} : undefined,
      );
      setRows(data.mailboxes);
      await refresh();
      setNotice(
        action === 'start'
          ? 'Préparation activée. Le premier test sera transmis selon la cadence ; la réception reste à confirmer.'
          : 'Préparation en pause. Aucun nouveau test ne sera transmis ; une transmission déjà engagée peut se terminer.',
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const enabled = rows.filter((row) => row.enabled).length;
  return (
    <section className="panel native-warmup" aria-labelledby="native-warmup-title">
      <header>
        <div>
          <h2 id="native-warmup-title">Warm-up progressif</h2>
          <p>Tests contrôlés entre vos boîtes · sans service payant</p>
        </div>
        <span className="badge draft">
          <ShieldCheck size={14} /> SMTP + IMAP
        </span>
      </header>
      <div className="padded">
        <p className="nw-explanation">
          De petits envois techniques entre vos boîtes contrôlées, même lorsque les campagnes sont en pause.
          Les réponses de contrôle attendent au moins 15 minutes après une réception confirmée et comptent
          dans le même quota. Cela ne promet pas une réputation ni un placement en boîte de réception.
        </p>
        {(error || loadError) && (
          <div className="alert error" role="alert">
            {error || loadError}
          </div>
        )}
        {notice && (
          <div className="alert success" role="status">
            {notice}
          </div>
        )}
        {loading ? (
          <p role="status">Chargement du warm-up…</p>
        ) : !rows.length ? (
          <p>
            Ajoutez au moins deux boîtes email et vérifiez leurs connexions pour réaliser un test entre boîtes
            contrôlées.
          </p>
        ) : (
          <>
            <div className="nw-mailboxes">
              {rows.map((row) => {
                const state = mailboxState(row);
                const progress = row.progress;
                const sentToday = count(row.attempted_today, row.attempted_24h);
                const acceptedTotal = count(progress?.accepted, row.accepted_7d);
                const receivedTotal = count(progress?.received, row.received_7d);
                const replyDelay = count(row.cadence?.reply_delay_minutes, 15);
                return (
                  <article className="nw-mailbox" key={row.id}>
                    <div className="nw-mailbox-heading">
                      <strong>{row.email}</strong>
                      <span
                        className={
                          'badge ' +
                          (state.kind === 'incident' ? 'failed' : row.enabled ? 'active' : 'paused')
                        }
                      >
                        {labels[row.status] || row.status}
                      </span>
                    </div>
                    <p className={'nw-state ' + state.kind}>
                      {state.icon}
                      {state.text}
                    </p>
                    <div className="nw-counts" aria-label={'Compteurs du warm-up pour ' + row.email}>
                      <div>
                        <span>Aujourd’hui (Paris)</span>
                        <strong>{sentToday}</strong>
                        <small>
                          tentative{sentToday !== 1 ? 's' : ''} · {count(row.accepted_today)} acceptée
                          {count(row.accepted_today) !== 1 ? 's' : ''} · {count(row.received_today)} reçue
                          {count(row.received_today) !== 1 ? 's' : ''}
                        </small>
                      </div>
                      <div>
                        <span>Dernières 24 h</span>
                        <strong>
                          {count(row.replies_sent_24h)} + {count(row.originals_sent_24h)}
                        </strong>
                        <small>réponses + tests initiaux transmis</small>
                      </div>
                      <div>
                        <span>Cumul du programme</span>
                        <strong>
                          {acceptedTotal} / {receivedTotal}
                        </strong>
                        <small>acceptés / reçus confirmés</small>
                      </div>
                    </div>
                    <CadenceSummary row={row} />
                    <p className="nw-schedule">
                      <Clock3 size={15} />
                      {row.next_at ? (
                        <>
                          Prochain {row.next_kind === 'reply' ? 'envoi de réponse' : 'test'} :{' '}
                          {date(row.next_at)}
                        </>
                      ) : (
                        <>Aucun créneau fixé</>
                      )}
                      {row.next_kind === 'reply' && <> · délai minimal : {replyDelay} min après réception</>}
                      {row.last_sent_at && <> · dernier envoi : {date(row.last_sent_at)}</>}
                      {row.last_received_at && <> · dernière réception : {date(row.last_received_at)}</>}
                    </p>
                    {row.blockers.length > 0 && (
                      <p className={row.enabled ? 'error-text' : 'nw-muted'}>{row.blockers.join(' · ')}</p>
                    )}
                    <WarmupProgress
                      rowId={row.id}
                      email={row.email}
                      progress={progress}
                      admin={admin}
                      api={api}
                      onChanged={async () => {
                        await load();
                        await refresh();
                      }}
                    />
                    {row.history.length > 0 && (
                      <details>
                        <summary>Journal des tests ({row.history.length} derniers)</summary>
                        <div className="nw-history">
                          {row.history.map((job) => (
                            <div key={job.id}>
                              <div>
                                <strong>
                                  {job.is_reply ? 'Réponse de contrôle · ' : ''}
                                  {labels[job.status] || job.status}
                                </strong>
                                <small>
                                  {date(job.at)} → {job.recipient}
                                </small>
                                {job.received_at && (
                                  <small>
                                    Observé le {date(job.received_at)} dans {job.folder}
                                  </small>
                                )}
                                {job.error && <small className="error-text">{job.error}</small>}
                              </div>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </article>
                );
              })}
            </div>
            <details className="nw-method">
              <summary>Comment progresse la cadence</summary>
              <p>
                Le warm-up démarre à 2 tests par boîte et par jour. Après 2 journées saines avec réception
                confirmée, la limite augmente de 2, jusqu’à 10 par jour. Les réponses font partie de ce
                plafond de 24 heures ; elles sont envoyées au plus tôt 15 minutes après la réception
                confirmée.
              </p>
              <p>
                Les campagnes restent indépendamment en pause ou actives. Une erreur ou une transmission
                incertaine est signalée pour contrôle. Les tests n’augmentent ni les statistiques, ni les
                autorisations des campagnes, et ne garantissent pas la réputation de l’expéditeur.
              </p>
            </details>
            {admin && (
              <div className="nw-controls">
                <details>
                  <summary>
                    {enabled
                      ? 'Ajouter des boîtes ou reprendre après contrôle'
                      : 'Activer le warm-up gratuit'}
                  </summary>
                  <fieldset disabled={busy}>
                    <legend>Boîtes vous appartenant, autorisées à recevoir ces tests</legend>
                    {rows.map((row) => (
                      <label className="checkbox" key={row.id}>
                        <input
                          type="checkbox"
                          checked={selected.includes(row.id)}
                          onChange={(e) =>
                            setSelected((values) =>
                              e.target.checked ? [...values, row.id] : values.filter((id) => id !== row.id),
                            )
                          }
                        />
                        {row.email}
                      </label>
                    ))}
                    <label className="checkbox nw-confirm">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                      />
                      Je contrôle ces boîtes et j’autorise les tests automatiques entre elles, indépendamment
                      de la pause des campagnes.
                    </label>
                    <button
                      className="primary"
                      disabled={busy || selected.length < 2 || !confirmed}
                      onClick={() => command('start')}
                    >
                      <Play size={15} />
                      Activer les tests sélectionnés
                    </button>
                  </fieldset>
                </details>
                {enabled > 0 && (
                  <button disabled={busy} onClick={() => command('pause')}>
                    <Pause size={15} />
                    Mettre le warm-up en pause
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
