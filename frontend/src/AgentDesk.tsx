import {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, Check, RefreshCw, Pause, Play, Bell, Plus, ChevronLeft} from 'lucide-react';
import {
  Field,
  Panel,
  Feedback,
  Blank,
  RouteLink,
  useRecord,
  when,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import './agent-desk.css';
import {errorText} from './api';
const stateNames: Row = {
  queued: 'À prendre en charge',
  running: 'En cours',
  retry_at: 'Reprise programmée',
  waiting_external: 'Confirmation en attente',
  waiting_human: 'À vous',
  failed: 'Échec à rapprocher',
  uncertain: 'Résultat incertain',
  completed: 'Terminé',
  cancelled: 'Annulé',
};
export function AgentNotifications({
  api,
  navigate,
  revision,
}: {
  api: Api;
  navigate: Navigate;
  revision: unknown;
}) {
  const data = useRecord('/agent/notifications', api, revision),
    seen = useRef(new Set<string>());
  useEffect(() => {
    if (!data.record) return;
    for (const row of data.record.items) {
      if (seen.current.has(row.id)) continue;
      seen.current.add(row.id);
      if ('Notification' in window && Notification.permission === 'granted') {
        const notification = new Notification(row.title, {body: row.body, tag: row.id});
        notification.onclick = () => {
          window.focus();
          navigate(row.url);
          notification.close();
        };
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one notification per fetched payload; navigate is only called from the click handler
  }, [data.record]);
  const count = data.record?.items.length || 0;
  return count > 0 ? (
    <button className="agent-human-indicator" onClick={() => navigate('/agent?view=human')}>
      <Bell size={15} />
      {count} à vous
    </button>
  ) : null;
}
export function AgentDesk({
  api,
  data,
  url,
  navigate,
  onRefresh,
}: {
  api: Api;
  data: Row;
  url: string;
  navigate: Navigate;
  onRefresh: () => Promise<void>;
}) {
  const params = new URL(url, location.origin).searchParams,
    view = params.get('view') || 'human',
    id = params.get('action') || '';
  const [page, setPage] = useState(1),
    [creating, setCreating] = useState(false),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState('');
  const status = useRecord('/agent/status', api, data),
    queue = useRecord('/agent/actions?' + new URLSearchParams({view, page: String(page)}), api, data),
    client = status.record?.client,
    counts = status.record?.counts || {};
  const lastSeen = client?.last_seen_at ? new Date(client.last_seen_at).getTime() : 0,
    online = Date.now() - lastSeen < 20 * 60 * 1000;
  async function refresh() {
    status.refresh();
    queue.refresh();
    await onRefresh();
  }
  function changeView(value: string) {
    setPage(1);
    setCreating(false);
    navigate('/agent?view=' + value);
  }
  return (
    <article className="agent-desk">
      <header className="agent-heading">
        <div>
          <span className="eyebrow">PILOTAGE</span>
          <h1>La prochaine action, au bon endroit.</h1>
          <p>L’agent traite les opérations courantes. Vous gardez les décisions critiques.</p>
        </div>
        <button className="primary" onClick={() => setCreating(!creating)}>
          <Plus size={16} />
          Confier un lot
        </button>
      </header>
      <div className="agent-runtime">
        <span className={'agent-dot ' + (client?.enabled && online ? 'online' : '')} />
        <strong>Agent Lumelia</strong>
        <span>
          {!client
            ? 'À connecter'
            : !client.enabled
              ? 'En pause'
              : ['quota', 'budget'].includes(client.state.runtime_status)
                ? 'Reprise programmée'
                : online
                  ? client.state.runtime_status === 'running'
                    ? 'Travaille sur une action'
                    : 'Disponible'
                  : 'En attente du prochain passage'}
        </span>
        <small>{lastSeen ? 'Dernier passage ' + when(client.last_seen_at) : 'Premier passage à venir'}</small>
        {client && (
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await api('/agent/configure', 'POST', {enabled: !client.enabled});
                await refresh();
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {client.enabled ? <Pause size={13} /> : <Play size={13} />}{' '}
            {client.enabled ? 'Suspendre' : 'Reprendre'}
          </button>
        )}
        <button aria-label="Actualiser les actions" onClick={refresh}>
          <RefreshCw size={14} />
        </button>
      </div>
      <Feedback error={error || queue.error || status.error} notice={notice} />
      {creating ? (
        <BatchForm
          api={api}
          campaigns={data.campaigns}
          close={() => setCreating(false)}
          saved={async () => {
            setCreating(false);
            changeView('agent');
            await refresh();
          }}
        />
      ) : (
        <>
          <div className="agent-tabs" role="group" aria-label="Responsable des actions">
            {[
              ['human', 'À vous'],
              ['agent', 'Agent'],
              ['history', 'Historique'],
            ].map(([key, label]) => (
              <button key={key} aria-pressed={view === key} onClick={() => changeView(key)}>
                {label}
                <span>{counts[key] || 0}</span>
              </button>
            ))}
          </div>
          {id ? (
            <ActionDetail key={id} id={id} api={api} navigate={navigate} onSaved={refresh} view={view} />
          ) : (
            <section className="agent-list">
              <header>
                <h2>
                  {view === 'human'
                    ? 'Décisions et interventions attendues'
                    : view === 'agent'
                      ? 'Ce que l’agent prend en charge'
                      : 'Résultats et actions terminées'}
                </h2>
                <span>{queue.record?.total ?? '…'} action(s)</span>
              </header>
              {queue.record?.items.length ? (
                <ol>
                  {queue.record.items.map((row: Row) => (
                    <li key={row.id}>
                      <button
                        className="agent-row"
                        onClick={() => navigate('/agent?view=' + view + '&action=' + row.id)}
                      >
                        <span>
                          <strong>{row.title}</strong>
                          <small>
                            {row.contact_name ||
                              (row.kind === 'discover_leads' ? 'Recherche et qualification' : 'Exploitation')}
                            {row.company ? ' · ' + row.company : ''}
                          </small>
                          <p>
                            {view === 'human'
                              ? row.human_reason
                              : view === 'history'
                                ? row.result.summary
                                : row.last_error ||
                                  row.payload.brief ||
                                  'Contexte, preuves et prochaine action enregistrés à l’issue du traitement.'}
                          </p>
                        </span>
                        <span className={'agent-state ' + row.status}>
                          {stateNames[row.status]}
                          <small>
                            {row.status === 'retry_at'
                              ? when(row.next_attempt_at)
                              : row.due_at
                                ? when(row.due_at)
                                : ''}
                          </small>
                        </span>
                        <ArrowUpRight size={17} />
                      </button>
                    </li>
                  ))}
                </ol>
              ) : (
                <Blank
                  title={
                    queue.loading
                      ? 'Chargement…'
                      : view === 'human'
                        ? 'Aucune intervention attendue'
                        : view === 'agent'
                          ? 'L’agent a terminé sa file'
                          : 'Les résultats apparaîtront ici'
                  }
                >
                  {view === 'agent'
                    ? 'Confiez-lui un lot de contacts ou une recherche ciblée.'
                    : 'Les demandes utiles seront regroupées ici.'}
                </Blank>
              )}
              {(queue.record?.total || 0) > 6 && (
                <footer>
                  <button disabled={page === 1} onClick={() => setPage(page - 1)}>
                    Précédent
                  </button>
                  <span>
                    {page} / {Math.ceil(queue.record!.total / 6)}
                  </span>
                  <button disabled={page * 6 >= queue.record!.total} onClick={() => setPage(page + 1)}>
                    Suivant
                  </button>
                </footer>
              )}
            </section>
          )}
        </>
      )}
      <details className="agent-policy">
        <summary>Autonomie et notifications</summary>
        <p>
          L’agent recherche les prospects, qualifie les fiches et traite les réponses ordinaires dans le cadre
          autorisé. Il ne modifie pas les plafonds ni la pause des envois.{' '}
        </p>
        <p>
          L’agent travaille sur le serveur une fois par heure, même lorsque votre ordinateur est fermé. Les
          interventions nécessaires sont regroupées ici. Vous pouvez aussi recevoir une notification lorsque
          Lumelia est ouvert.
        </p>
        <button
          onClick={async () => {
            if (!('Notification' in window)) {
              setNotice(
                'Les notifications navigateur ne sont pas disponibles ici. Les demandes restent dans À vous.',
              );
              return;
            }
            const result = await Notification.requestPermission();
            setNotice(
              result === 'granted'
                ? 'Notifications navigateur autorisées.'
                : 'Les demandes restent dans À vous.',
            );
          }}
        >
          Activer les notifications navigateur
        </button>
      </details>
    </article>
  );
}
function BatchForm({
  api,
  campaigns,
  close,
  saved,
}: {
  api: Api;
  campaigns: Row[];
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [scope, setScope] = useState('existing'),
    [campaign, setCampaign] = useState(campaigns[0]?.id || ''),
    [count, setCount] = useState(25),
    [brief, setBrief] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const request = useRef(crypto.randomUUID());
  return (
    <Panel
      title="Confier un lot à l’agent"
      description="Une mission délimitée et des résultats enregistrés dans les fiches."
      action={<button onClick={close}>Annuler</button>}
    >
      <Feedback error={error} />
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          try {
            await api('/agent/batches', 'POST', {
              scope,
              campaign_id: campaign,
              count,
              brief,
              request_id: request.current,
            });
            await saved();
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="ws-form-grid">
          <Field label="Mission">
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="existing">Qualifier les contacts existants</option>
              <option value="discover">Rechercher de nouveaux décideurs</option>
            </select>
          </Field>
          <Field label="Sous-campagne">
            <select required value={campaign} onChange={(e) => setCampaign(e.target.value)}>
              {campaigns.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Nombre maximum de contacts">
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
          </Field>
        </div>
        {scope === 'discover' && (
          <Field label="Cible et objectif">
            <textarea
              rows={4}
              value={brief}
              minLength={20}
              maxLength={3000}
              required
              onChange={(e) => setBrief(e.target.value)}
              placeholder="Ex. 25 dirigeants de cabinets de conseil français, avec des propositions B2B à suivre. Sources officielles, un décideur par entreprise."
            />
          </Field>
        )}
        <button className="primary" disabled={busy || !campaign}>
          {busy ? 'Préparation…' : 'Confier à l’agent'}
        </button>
      </form>
    </Panel>
  );
}
function ActionDetail({
  id,
  api,
  navigate,
  onSaved,
  view,
}: {
  id: string;
  api: Api;
  navigate: Navigate;
  onSaved: () => Promise<void>;
  view: string;
}) {
  const record = useRecord('/agent/actions/' + id, api),
    row = record.record,
    [note, setNote] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  async function decide(action: string) {
    if (!row) return;
    setBusy(true);
    try {
      await api('/agent/actions/' + id + '/decision', 'POST', {
        action,
        note,
        expected_updated_at: row.updated_at,
      });
      await onSaved();
      navigate('/agent?view=' + view);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button className="agent-back" onClick={() => navigate('/agent?view=' + view)}>
        <ChevronLeft size={15} />
        Retour à la file
      </button>
      <Feedback error={error || record.error} />
      {row && (
        <Panel
          title={row.title}
          description={stateNames[row.status] + ' · ' + (row.company || 'Lumelia')}
          action={
            row.target_url && (
              <RouteLink url={row.target_url} navigate={navigate} className="ws-button-link">
                Ouvrir le dossier
                <ArrowUpRight size={15} />
              </RouteLink>
            )
          }
        >
          {row.human_reason && (
            <div className="agent-request">
              <strong>Votre intervention</strong>
              <p>{row.human_reason}</p>
            </div>
          )}
          {row.result.summary && <p className="agent-result-text">{row.result.summary}</p>}
          {row.result.next_action && (
            <p>
              <strong>Prochaine action : </strong>
              {row.result.next_action}
            </p>
          )}
          {row.last_error && <p className="ws-context">{row.last_error}</p>}
          {row.result.leads?.length > 0 && (
            <ul className="agent-leads">
              {row.result.leads.map((lead: Row) => (
                <li key={lead.id}>
                  <RouteLink url={'/contacts/' + lead.id} navigate={navigate}>
                    {lead.name} · {lead.company}
                    <ArrowUpRight size={13} />
                  </RouteLink>
                </li>
              ))}
            </ul>
          )}
          {['waiting_human', 'retry_at'].includes(row.status) && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                decide('resume');
              }}
            >
              <Field label="Votre réponse ou la résolution">
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  minLength={5}
                  maxLength={3000}
                  rows={3}
                  placeholder="Information manquante, choix retenu ou correction effectuée…"
                />
              </Field>
              <div className="actions">
                {[
                  'qualify_contact',
                  'qualify_account',
                  'discover_leads',
                  'reply_draft',
                  'review_task',
                  'operations_check',
                ].includes(row.kind) && (
                  <button className="primary" disabled={busy || note.trim().length < 5}>
                    <Play size={14} />
                    Rendre la main à l’agent
                  </button>
                )}
                {!row.task_id && (
                  <button
                    type="button"
                    disabled={busy || note.trim().length < 5}
                    onClick={() => decide('complete')}
                  >
                    <Check size={14} />
                    Marquer résolu
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy || note.trim().length < 5}
                  onClick={() => decide('cancel')}
                >
                  Annuler l’action
                </button>
              </div>
            </form>
          )}
          <details className="agent-policy">
            <summary>Détails du traitement</summary>
            <p>
              {row.attempts} exécution(s) · Créée le {when(row.created_at)}
              {row.completed_at ? ' · Terminée le ' + when(row.completed_at) : ''}
            </p>
            {row.payload.brief && <p>{row.payload.brief}</p>}
            <p>
              {row.result.evidence_ids?.length ||
                row.result.leads?.reduce((n: number, l: Row) => n + (l.evidence_ids?.length || 0), 0) ||
                0}{' '}
              preuve(s) rattachée(s) aux fiches.
            </p>
          </details>
        </Panel>
      )}
    </>
  );
}
