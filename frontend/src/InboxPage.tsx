import {useEffect, useRef, useState, type FormEvent} from 'react';
import {ArrowUpRight, Check, ChevronRight, Inbox, RotateCcw} from 'lucide-react';
import {
  WorkspaceHeader,
  Panel,
  Field,
  Feedback,
  Blank,
  Events,
  RouteLink,
  useRecord,
  when,
  personName,
  money,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {ConversationPanel} from './ConversationPanel';
import {errorText} from './api';
const classifications: Row = {
  reply: 'Réponse à examiner',
  positive_reply: 'Intérêt confirmé',
  negative_reply: 'Pas de besoin',
  out_of_office: 'Absence',
  bounce: 'Email en erreur',
  stop: 'Opposition',
};
type Props = {
  id: string;
  url: string;
  data: Row;
  api: Api;
  navigate: Navigate;
  onRefresh: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  discardVersion: number;
  onOpen: (name: string, row?: Row) => void;
};
export function InboxPage(props: Props) {
  const {id, url, data, api, navigate} = props;
  const searchParams = new URL(url, location.origin).searchParams;
  const status = searchParams.get('status') || 'open',
    kind = searchParams.get('kind') || '',
    channel = searchParams.get('channel') || '',
    campaign = searchParams.get('campaign') || '',
    sender = searchParams.get('sender') || '',
    q = searchParams.get('q') || '';
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const [search, setSearch] = useState(q),
    [notice, setNotice] = useState('');
  const listed = useRecord(
    '/conversations?' +
      new URLSearchParams({
        status,
        kind,
        channel,
        campaign_id: campaign,
        sender_id: sender,
        q,
        page: String(page),
      }),
    api,
    data,
  );
  useEffect(() => {
    setSearch(q);
  }, [q]);
  useEffect(() => {
    document.title = 'Boîte de réception · Lumelia';
  }, []);
  function path(record = '', patch: Row = {}) {
    const next = new URLSearchParams({status, kind, channel, campaign, sender, q, ...patch});
    for (const [key, value] of [...next]) if (!value) next.delete(key);
    return '/inbox' + (record ? '/' + record : '') + '?' + next;
  }
  const totals = listed.record?.totals || {},
    rows: Row[] = listed.record?.items || [];
  return (
    <article className="ws-page inbox-workspace">
      <WorkspaceHeader
        back={id ? path('', {page}) : '/'}
        backLabel={id ? 'Toutes les conversations' : 'Vue d’ensemble'}
        eyebrow="CONVERSATIONS"
        title="Boîte de réception"
        subtitle={id ? undefined : 'Lisez les réponses reçues et choisissez la suite.'}
        navigate={navigate}
      />
      <div className="ws-inbox-status" role="group" aria-label="Statut des réponses">
        {[
          ['open', 'À traiter', totals.open],
          ['done', 'Traitées', totals.done],
          ['all', 'Toutes', (totals.open || 0) + (totals.done || 0)],
        ].map(([key, label, total]) => (
          <button
            key={String(key)}
            aria-pressed={status === key}
            onClick={() => navigate(path('', {status: key}))}
          >
            {label}
            <span>{total ?? '…'}</span>
          </button>
        ))}
      </div>
      <details className="op-inbox-filters">
        <summary>Rechercher et filtrer les conversations</summary>
        <form
          className="ws-filter-bar inbox-filters"
          onSubmit={(event) => {
            event.preventDefault();
            navigate(path('', {q: search}));
          }}
        >
          <Field label="Nature de la réponse">
            <select value={kind} onChange={(event) => navigate(path('', {kind: event.target.value}))}>
              <option value="">Toutes les réponses</option>
              {Object.entries(classifications).map(([key, label]) => (
                <option key={key} value={key}>
                  {String(label)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Canal">
            <select value={channel} onChange={(event) => navigate(path('', {channel: event.target.value}))}>
              <option value="">Tous les canaux</option>
              <option value="email">Email</option>
              <option value="phone">Appel</option>
              <option value="linkedin">LinkedIn</option>
            </select>
          </Field>
          <Field label="Sous-campagne">
            <select value={campaign} onChange={(event) => navigate(path('', {campaign: event.target.value}))}>
              <option value="">Toutes les sous-campagnes</option>
              {data.campaigns.map((row: Row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Boîte destinataire">
            <select value={sender} onChange={(event) => navigate(path('', {sender: event.target.value}))}>
              <option value="">Toutes les boîtes</option>
              {data.senders.map((row: Row) => (
                <option key={row.id} value={row.id}>
                  {row.from_email}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Rechercher une réponse">
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              maxLength={120}
              placeholder="Nom, entreprise ou texte…"
            />
          </Field>
          <button type="submit">Rechercher</button>
        </form>
      </details>
      <Feedback error={listed.error} notice={notice} />
      <div className={'ws-inbox-layout ' + (id ? 'has-detail' : '')}>
        <section className="ws-inbox-list" aria-label="Réponses reçues">
          <header>
            <strong>{listed.record?.total ?? '…'} conversation(s)</strong>
            <small>Les plus récentes d’abord</small>
          </header>
          {rows.map((row) => (
            <RouteLink
              key={row.id}
              url={path(row.id, {page})}
              navigate={navigate}
              className={'ws-inbox-item ' + (id === row.id ? 'active' : '')}
            >
              <span className="ws-inbox-avatar">{personName(row.contact).slice(0, 1)}</span>
              <span>
                <span className="ws-inbox-item-title">
                  <strong>{personName(row.contact)}</strong>
                  <time>{when(row.created_at)}</time>
                </span>
                <small>
                  {row.account?.name || 'Entreprise indisponible'} · {row.reply_count || 1} réponse(s) ·{' '}
                  {row.channel === 'phone' ? 'Appel' : row.channel === 'linkedin' ? 'LinkedIn' : 'Email'}
                </small>
                <p>{row.summary || 'Réponse reçue sans extrait.'}</p>
                <span className={'ws-classification ' + row.classification}>
                  {classifications[row.classification]}
                </span>
              </span>
              <ChevronRight size={15} />
            </RouteLink>
          ))}
          {!rows.length && (
            <Blank
              title={listed.loading ? 'Chargement des réponses…' : 'Aucune réponse dans cette sélection'}
            >
              {status === 'open'
                ? 'Les nouvelles réponses apparaîtront ici au fil des conversations.'
                : 'Modifiez les filtres pour retrouver une conversation.'}
            </Blank>
          )}
          {(listed.record?.total || 0) > 20 && (
            <div className="ws-pagination">
              <button disabled={page <= 1} onClick={() => navigate(path('', {page: page - 1}))}>
                Précédent
              </button>
              <span>
                {listed.record?.page || page}/{Math.ceil(listed.record!.total / 20)}
              </span>
              <button
                disabled={page * 20 >= listed.record!.total}
                onClick={() => navigate(path('', {page: page + 1}))}
              >
                Suivant
              </button>
            </div>
          )}
        </section>
        {id ? (
          <InboxDetail
            key={id + ':' + props.discardVersion}
            {...props}
            onDone={async (message: string) => {
              listed.refresh();
              setNotice(message);
              await props.onRefresh();
            }}
          />
        ) : (
          rows.length > 0 && (
            <div className="ws-inbox-placeholder">
              <Inbox size={32} />
              <h2>Choisissez une conversation</h2>
              <p>Retrouvez son contexte, classez la réponse et préparez la suite.</p>
            </div>
          )
        )}
      </div>
    </article>
  );
}
function InboxDetail({
  id,
  url,
  api,
  navigate,
  onDirtyChange,
  onOpen,
  onDone,
}: Props & {onDone: (message: string) => Promise<void>}) {
  const detail = useRecord('/inbox/' + id, api),
    row = detail.record;
  const [replyDirty, setReplyDirty] = useState(false),
    [section, setSection] = useState(
      new URL(url, location.origin).searchParams.get('mode') === 'classify' ? 'classify' : 'conversation',
    );
  const [classification, setClassification] = useState(''),
    [action, setAction] = useState('done'),
    [note, setNote] = useState(''),
    [title, setTitle] = useState(''),
    [due, setDue] = useState(''),
    [amount, setAmount] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const dirtyRef = useRef(onDirtyChange);
  dirtyRef.current = onDirtyChange;
  const original = useRef(''),
    requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    if (row) {
      setClassification(row.classification);
      setNote(row.data.inbox?.note || '');
      original.current = JSON.stringify([row.classification, 'done', row.data.inbox?.note || '', '', '', '']);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the form is re-seeded for another message or a newer version, not on each refetch of the same one
  }, [row?.id, row?.updated_at]);
  const changed =
    !!row && JSON.stringify([classification, action, note, title, due, amount]) !== original.current;
  useEffect(() => {
    dirtyRef.current(changed || replyDirty);
    return () => dirtyRef.current(false);
  }, [changed, replyDirty]);
  function update(fn: () => void) {
    fn();
    requestId.current = crypto.randomUUID();
  }
  async function submit(event: FormEvent, override?: string) {
    event.preventDefault();
    if (!row) return;
    setBusy(true);
    setError('');
    try {
      await api('/inbox/' + id + '/handle', 'POST', {
        action: override || action,
        classification,
        note,
        title,
        due_at: action === 'callback' && due ? new Date(due).toISOString() : null,
        amount_cents: Math.round(Number(amount || 0) * 100),
        expected_updated_at: row.updated_at,
        request_id: requestId.current,
      });
      dirtyRef.current(false);
      setAction('done');
      setTitle('');
      setDue('');
      setAmount('');
      requestId.current = crypto.randomUUID();
      detail.refresh();
      await onDone(
        override === 'reopen'
          ? 'Réponse remise dans la file à traiter.'
          : action === 'callback'
            ? 'Rappel ajouté aux prochaines actions.'
            : action === 'opportunity'
              ? 'Opportunité liée à cette conversation.'
              : 'Réponse classée et marquée comme traitée.',
      );
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  if (!row)
    return (
      <Panel title={detail.loading ? 'Chargement de la conversation…' : 'Réponse indisponible'}>
        <Feedback error={detail.error} />
        {detail.error && <button onClick={detail.refresh}>Réessayer</button>}
      </Panel>
    );
  const blocked = ['stop', 'bounce'].includes(classification) || !row.contact;
  return (
    <div className="ws-inbox-detail">
      <div className="op-detail-title">
        <strong>{personName(row.contact)}</strong>
        <span>{row.account?.name}</span>
        {row.contact && (
          <RouteLink url={'/contacts/' + row.contact.id} navigate={navigate}>
            Fiche contact
            <ArrowUpRight size={14} />
          </RouteLink>
        )}
      </div>
      <div className="op-status-tabs" role="group" aria-label="Intention de la conversation">
        <button aria-pressed={section === 'conversation'} onClick={() => setSection('conversation')}>
          Lire et répondre
        </button>
        <button aria-pressed={section === 'classify'} onClick={() => setSection('classify')}>
          Classer et prévoir la suite
        </button>
      </div>
      <div hidden={section !== 'conversation'}>
        <ConversationPanel
          id={id}
          api={api}
          navigate={navigate}
          onDirty={setReplyDirty}
          onClassify={(eventId) => {
            if (eventId === id) setSection('classify');
            else navigate('/inbox/' + eventId + '?status=all&mode=classify');
          }}
          onSaved={async () => {
            detail.refresh();
            await onDone('Conversation actualisée.');
          }}
        />
      </div>
      <div hidden={section !== 'classify'} className="op-classify-workspace">
        <details className="ws-history-details">
          <summary>Contexte de la réponse et opportunités</summary>
          <Panel
            title={personName(row.contact)}
            description={row.account?.name}
            action={
              row.contact && (
                <RouteLink url={'/contacts/' + row.contact.id} navigate={navigate} className="ws-button-link">
                  Fiche contact
                  <ArrowUpRight size={14} />
                </RouteLink>
              )
            }
          >
            <dl className="ws-facts single">
              <div>
                <dt>Reçue le</dt>
                <dd>{when(row.created_at)}</dd>
              </div>
              <div>
                <dt>Boîte destinataire</dt>
                <dd>{row.sender?.from_email || 'Non attribuée'}</dd>
              </div>
              <div>
                <dt>Sous-campagne</dt>
                <dd>
                  {row.campaign?.group_id ? (
                    <RouteLink url={'/campaigns/' + row.campaign.group_id} navigate={navigate}>
                      {row.campaign.name}
                    </RouteLink>
                  ) : (
                    row.campaign?.name || 'Sans sous-campagne'
                  )}
                </dd>
              </div>
            </dl>
            <div className="ws-received-message op-message-reader">
              <span className="eyebrow">
                RÉPONSE REÇUE ·{' '}
                {row.channel === 'phone' ? 'APPEL' : row.channel === 'linkedin' ? 'LINKEDIN' : 'EMAIL'}
              </span>
              <p>{row.summary || 'Aucun contenu enregistré pour cette réponse.'}</p>
            </div>
            {row.related_message && (
              <details className="ws-related-message">
                <summary>Message auquel le contact a répondu</summary>
                <h3>{row.related_message.subject}</h3>
                <p>{row.related_message.body}</p>
              </details>
            )}
            {row.followup && (
              <div className="ws-context">
                <strong>Rappel · {when(row.followup.due_at)}</strong>
                <p>{row.followup.title}</p>
                <RouteLink url="/calendar" navigate={navigate}>
                  Voir le planning
                </RouteLink>
              </div>
            )}
            {row.opportunities.length > 0 && (
              <div className="ws-linked-deals">
                <h3>Opportunités de l’entreprise</h3>
                {row.opportunities.map((deal: Row) => (
                  <button key={deal.id} onClick={() => onOpen('editOpportunity', deal)}>
                    {deal.title}
                    <span>{money(deal.amount_cents)}</span>
                    <ArrowUpRight size={14} />
                  </button>
                ))}
              </div>
            )}
          </Panel>
        </details>
        <Panel
          title={row.review_status === 'done' ? 'Réponse traitée' : 'Traiter cette réponse'}
          description={
            row.review_status === 'done'
              ? 'Vous pouvez ajuster le classement ou remettre la réponse dans la file.'
              : 'Le classement et la prochaine action restent dans l’historique.'
          }
        >
          <Feedback error={error} />
          <form onSubmit={(event) => submit(event)}>
            <Field label="Classement de la réponse">
              <select
                value={classification}
                disabled={busy}
                onChange={(event) =>
                  update(() => {
                    setClassification(event.target.value);
                    if (['stop', 'bounce'].includes(event.target.value)) setAction('done');
                  })
                }
              >
                {Object.entries(classifications).map(([key, label]) => (
                  <option key={key} value={key}>
                    {String(label)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Prochaine action">
              <select
                value={action}
                disabled={busy}
                onChange={(event) => update(() => setAction(event.target.value))}
              >
                <option value="done">Classer comme traitée</option>
                <option value="callback" disabled={blocked}>
                  Convenir d’un rappel
                </option>
                <option value="opportunity" disabled={blocked}>
                  Créer ou lier une opportunité
                </option>
              </select>
            </Field>
            {classification === 'stop' && (
              <p className="ws-context">
                Le classement en opposition inscrit le contact et son email dans les exclusions de
                prospection.
              </p>
            )}
            {action !== 'done' && (
              <Field label={action === 'callback' ? 'Intitulé du rappel' : 'Titre de l’opportunité'}>
                <input
                  value={title}
                  onChange={(event) => update(() => setTitle(event.target.value))}
                  required
                  maxLength={220}
                  placeholder={
                    action === 'callback'
                      ? 'Reprendre la conversation sur les devis'
                      : 'Mise en place du suivi des devis'
                  }
                />
              </Field>
            )}
            {action === 'callback' && (
              <Field label="Date et heure du rappel" hint="Dans votre fuseau horaire actuel.">
                <input
                  type="datetime-local"
                  value={due}
                  onChange={(event) => update(() => setDue(event.target.value))}
                  required
                />
              </Field>
            )}
            {action === 'opportunity' && (
              <Field
                label="Montant estimé (€)"
                hint="Une opportunité déjà ouverte pour cette entreprise sera réutilisée."
              >
                <input
                  type="number"
                  min="0"
                  max="1000000"
                  step="0.01"
                  value={amount}
                  onChange={(event) => update(() => setAmount(event.target.value))}
                  placeholder="0"
                />
              </Field>
            )}
            <Field label="Note de traitement">
              <textarea
                value={note}
                onChange={(event) => update(() => setNote(event.target.value))}
                rows={3}
                maxLength={3000}
                placeholder="Besoin exprimé, contexte et suite convenue…"
              />
            </Field>
            <div className="ws-decision-actions">
              <button className="primary" disabled={busy}>
                <Check size={16} />
                {busy
                  ? 'Enregistrement…'
                  : action === 'callback'
                    ? 'Enregistrer le rappel'
                    : action === 'opportunity'
                      ? 'Créer ou lier l’opportunité'
                      : 'Enregistrer le classement'}
              </button>
              {row.review_status === 'done' && (
                <button type="button" disabled={busy || changed} onClick={(event) => submit(event, 'reopen')}>
                  <RotateCcw size={15} />
                  Remettre à traiter
                </button>
              )}
            </div>
          </form>
        </Panel>
        <details className="ws-history-details">
          <summary>Historique du contact</summary>
          <Events rows={row.history} />
        </details>
      </div>
    </div>
  );
}
