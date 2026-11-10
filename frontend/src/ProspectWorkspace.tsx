import {CallWorkspace} from './CallWorkspace';
import {callBlockedReason} from './callPolicy';
import {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, Mail, Phone, X} from 'lucide-react';
import {type Api, type Row} from './ListTable';
import {personName, when} from './WorkspaceUI';
import {errorText, type Upload} from './api';

const outcomes: Row = {
  not_reached: 'Non joint',
  switchboard: 'Passage au standard',
  callback: 'Rappel convenu',
  meeting: 'Rendez-vous obtenu',
  no_need: 'Pas de besoin',
  wrong_person: 'Mauvais interlocuteur',
  wrong_number: 'Numéro incorrect',
  stop: 'Ne plus contacter',
  done: 'Échange effectué',
};
const eventNames: Row = {
  call: 'Appel effectué',
  email_sent: 'Email envoyé',
  reply: 'Réponse reçue',
  positive_reply: 'Intérêt confirmé',
  stop: 'Ne plus contacter',
  meeting: 'Rendez-vous',
  note: 'Note',
  task_completed: 'Action terminée',
  bounce: 'Email en erreur',
};

export function ProspectDrawer({
  id,
  api,
  upload,
  revision,
  onRefresh,
  navigate,
  onDirty,
  close,
  onOpen,
}: {
  id: string;
  api: Api;
  upload: Upload;
  revision: unknown;
  onRefresh: () => Promise<void>;
  navigate: (url: string) => void;
  onDirty: (dirty: boolean) => void;
  close: () => void;
  onOpen: (name: string, row?: Row) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    requestId = useRef(crypto.randomUUID());
  const [contact, setContact] = useState<Row | null>(null),
    [page, setPage] = useState(1),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [outcome, setOutcome] = useState('not_reached'),
    [callOpen, setCallOpen] = useState(false),
    [notice, setNotice] = useState('');
  useEffect(() => {
    dialog.current?.showModal();
    return () => onDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount and unmount only: opens the dialog once, clears the dirty flag when it closes
  }, []);
  useEffect(() => {
    setContact(null);
    setPage(1);
    setCallOpen(false);
    setNotice('');
    requestId.current = crypto.randomUUID();
  }, [id]);
  useEffect(() => {
    let live = true;
    setError('');
    api('/prospects/' + id + '?page=' + page)
      .then((row) => {
        if (live) setContact(row);
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [api, id, page, revision]);
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true);
    setError('');
    try {
      await api('/prospects/' + id + '/calls', 'POST', {
        request_id: requestId.current,
        outcome,
        notes: f.notes,
        next_at: f.next_at ? new Date(String(f.next_at)).toISOString() : null,
        occurred_at: f.at ? new Date(String(f.at)).toISOString() : null,
        task_id: contact?.next_task?.channel === 'phone' ? contact.next_task.id : null,
      });
      requestId.current = crypto.randomUUID();
      onDirty(false);
      setCallOpen(false);
      setNotice('Appel enregistré' + (outcome === 'callback' ? ' et rappel planifié.' : '.'));
      await onRefresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="prospect-drawer"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      aria-labelledby="prospect-title"
    >
      <header>
        <div>
          <small>FICHE PROSPECT</small>
          <h2 id="prospect-title">{contact ? personName(contact) : 'Chargement…'}</h2>
          <p>
            {contact?.role} · {contact?.account.name}
          </p>
        </div>
        <button className="icon" aria-label="Fermer la fiche prospect" onClick={close}>
          <X size={20} />
        </button>
      </header>
      <div className="prospect-drawer-body">
        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="alert success" role="status">
            {notice}
          </p>
        )}
        {contact && (
          <>
            <div className="prospect-priority">
              <span className={'prospect-score ' + (contact.score >= 70 ? 'high' : '')}>
                {contact.score}
                <small>/100</small>
              </span>
              <div>
                <strong>Priorité de contact</strong>
                <p>
                  {contact.fit.matched}/{contact.fit.total} critères de cible documentés
                </p>
                <details>
                  <summary>Comment est calculé le score ?</summary>
                  <p>
                    Adéquation à la cible : 60 points. Email et téléphone : 25 points. Preuve confirmée de
                    moins de 90 jours : 15 points. L’intention d’achat reste à confirmer en conversation.
                  </p>
                </details>
              </div>
            </div>
            <CallWorkspace
              api={api}
              upload={upload}
              accountId={contact.account_id || contact.account.id}
              contactId={contact.id}
              campaignId={contact.campaign_id}
              defaultDestination={contact.phone || ''}
              blockedReason={callBlockedReason(contact)}
              onChanged={() => {
                void onRefresh();
              }}
            />
            <div className="prospect-coordinate">
              <Phone size={17} />
              <div>
                <strong>{contact.phone || 'Téléphone à récupérer'}</strong>
                <small>
                  {contact.phone_type === 'direct'
                    ? 'Ligne directe'
                    : contact.phone_type === 'switchboard'
                      ? 'Standard de l’entreprise'
                      : 'Type de numéro à vérifier'}
                  {contact.phone_status === 'invalid' ? ' · Numéro incorrect' : ''}
                </small>
              </div>
              {contact.phone && contact.phone_status !== 'invalid' && (
                <a className="button" href={'tel:' + contact.phone}>
                  Appeler
                </a>
              )}
            </div>
            <div className="prospect-coordinate">
              <Mail size={17} />
              <div>
                <strong>{contact.email || 'Email à récupérer'}</strong>
                <small>
                  {contact.email_status === 'valid'
                    ? 'Email vérifié'
                    : contact.email_status === 'invalid'
                      ? 'Email invalide'
                      : 'Adresse non vérifiée'}
                </small>
              </div>
              {contact.conversation_id && (
                <button onClick={() => navigate('/inbox/' + contact.conversation_id)}>Conversation</button>
              )}
            </div>
            <div className="prospect-quick-actions">
              <button
                className="primary"
                onClick={() => {
                  setCallOpen(true);
                }}
              >
                <Phone size={15} />
                Consigner un appel
              </button>
              <button onClick={() => onOpen('newTask', contact)}>Planifier un suivi</button>
              <button onClick={() => navigate('/contacts/' + id)}>
                Fiche complète
                <ArrowUpRight size={14} />
              </button>
            </div>
            {callOpen && (
              <form className="prospect-call-form" onSubmit={save} onChange={() => onDirty(true)}>
                <h3>Appel effectué</h3>
                <label className="field">
                  <span>Résultat</span>
                  <select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
                    {Object.entries(outcomes).map(([value, label]) => (
                      <option key={value} value={value}>
                        {String(label)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Date de l’appel</span>
                  <input
                    name="at"
                    type="datetime-local"
                    defaultValue={new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
                      .toISOString()
                      .slice(0, 16)}
                    required
                  />
                </label>
                {outcome === 'callback' && (
                  <label className="field">
                    <span>Date du rappel convenu</span>
                    <input type="datetime-local" name="next_at" required />
                  </label>
                )}
                <label className="field">
                  <span>Note</span>
                  <textarea
                    name="notes"
                    rows={3}
                    placeholder="Ce qui a été dit et la suite convenue…"
                    maxLength={10000}
                  />
                </label>
                {outcome === 'stop' && (
                  <p className="callout">La prospection sera arrêtée pour ce contact.</p>
                )}
                <button className="primary" disabled={busy}>
                  {busy ? 'Enregistrement…' : 'Enregistrer l’appel'}
                </button>
              </form>
            )}
            <section className="prospect-next">
              <h3>Prochaine action</h3>
              <p>
                {contact.next_task?.title ||
                  contact.qualification?.agent_review?.next_action ||
                  'Aucun suivi planifié.'}
              </p>
              {contact.next_task && <small>{when(contact.next_task.due_at)}</small>}
            </section>
            <section className="prospect-timeline">
              <h3>Appels, emails et notes</h3>
              {contact.timeline.length ? (
                <ol>
                  {contact.timeline.map((item: Row) => (
                    <li key={item.id}>
                      <span className={'timeline-dot ' + item.channel} />
                      <div>
                        <strong>
                          {eventNames[item.kind] || item.kind}
                          {item.outcome ? ' · ' + (outcomes[item.outcome] || item.outcome) : ''}
                        </strong>
                        <time>{when(item.at)}</time>
                        {item.summary && <p>{item.summary}</p>}
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted">Aucun échange enregistré.</p>
              )}
              <div className="actions">
                {page > 1 && <button onClick={() => setPage(page - 1)}>Plus récents</button>}
                {contact.has_more && <button onClick={() => setPage(page + 1)}>Plus anciens</button>}
              </div>
            </section>
          </>
        )}
      </div>
    </dialog>
  );
}

export function LeadAcquisition({
  api,
  data,
  onRefresh,
}: {
  api: Api;
  data: Row;
  onRefresh: () => Promise<void>;
}) {
  const [status, setStatus] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    let active = true;
    api('/acquisition/status')
      .then((row) => {
        if (active) setStatus(row);
      })
      .catch((e) => {
        if (active) setError(errorText(e));
      });
    return () => {
      active = false;
    };
  }, [api, data]);
  async function start(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true);
    setError('');
    try {
      await api('/acquisition/apify', 'POST', {
        campaign_id: f.campaign,
        limit: Number(f.count),
        search: f.search,
        location: f.location,
        request_id: requestId.current,
      });
      requestId.current = crypto.randomUUID();
      setNotice(
        'Collecte programmée. Les entreprises seront ajoutées avec leurs coordonnées et leur source.',
      );
      await onRefresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const statuses: Row = {
    queued: 'Programmée',
    starting: 'Démarrage',
    running: 'En cours',
    completed: 'Terminée',
    partial: 'Résultats partiels',
    failed: 'Échec',
    uncertain: 'À rapprocher',
    cancelled: 'Annulée',
  };
  return (
    <div className="lead-acquisition">
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="alert success" role="status">
          {notice}
        </p>
      )}
      <p>
        Récupérez des entreprises et leurs téléphones publiés avec Apify. Les décideurs, l’effectif et
        l’adéquation à votre cible restent à qualifier.
      </p>
      {status && !status.connected && (
        <p className="callout">
          Connectez Apify dans <a href="/settings">Réglages</a> pour lancer la collecte.
        </p>
      )}
      <form onSubmit={start}>
        <label className="field">
          <span>Campagne cible</span>
          <select name="campaign" required>
            {data.campaigns.map((campaign: Row) => (
              <option key={campaign.id} value={campaign.id}>
                {campaign.name}
              </option>
            ))}
          </select>
        </label>
        <div className="ws-form-grid">
          <label className="field">
            <span>Entreprises recherchées</span>
            <input name="search" minLength={3} maxLength={160} defaultValue="cabinet de conseil" required />
          </label>
          <label className="field">
            <span>Zone géographique</span>
            <input name="location" minLength={3} maxLength={160} defaultValue="Paris, France" required />
          </label>
        </div>
        <label className="field">
          <span>Taille maximale du lot</span>
          <select name="count" defaultValue="100">
            <option value="25">25 établissements</option>
            <option value="50">50 établissements</option>
            <option value="100">100 établissements</option>
          </select>
        </label>
        <small>
          Plafond : 0,50 USD par collecte · 5 USD/mois pour Lumelia. Une nouvelle collecte au maximum par
          heure. Les doublons et les entreprises exclues sont préservés. Les établissements sans site restent
          hors import.
        </small>
        <button className="primary" disabled={busy || !data.campaigns.length || !status?.connected}>
          {busy ? 'Programmation…' : 'Récupérer les entreprises'}
        </button>
      </form>
      {status && (
        <p>
          <small>
            {Number(status.reserved_or_spent_usd).toFixed(2)} / {Number(status.max_month_usd).toFixed(2)} USD
            réservés ou consommés ce mois
          </small>
        </p>
      )}
      {status?.jobs?.length > 0 && (
        <div className="acquisition-results">
          <h3>Dernières collectes</h3>
          {status?.jobs.slice(0, 5).map((row: Row) => (
            <div key={row.id}>
              <strong>
                {row.search} · {row.location}
              </strong>
              <small>
                {statuses[row.status] || row.status} · {when(row.at)}
              </small>
              {row.result?.received != null && (
                <p>
                  {row.result.created} entreprises ajoutées · {row.result.company_phones} téléphones
                  d’entreprise · {row.result.existing + row.result.duplicates} doublons ·{' '}
                  {row.result.without_domain} sans site
                </p>
              )}
              {row.error && <p className="error-text">{row.error}</p>}
              {row.result?.created > 0 && <a href="/companies">Voir les entreprises</a>}
              {row.run_url && (
                <a href={row.run_url} target="_blank" rel="noreferrer">
                  Résultat Apify
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
