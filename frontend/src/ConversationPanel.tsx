import {useEffect, useRef, useState} from 'react';
import {ArrowDownLeft, ArrowUpRight, RefreshCw, Save, Send} from 'lucide-react';
import {
  Panel,
  Field,
  Feedback,
  Blank,
  useRecord,
  when,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {errorText} from './api';

const statusNames: Row = {
  draft: 'Brouillon',
  queued: 'En attente d’envoi',
  sending: 'Transmission en cours',
  sent: 'Envoyé',
  uncertain: 'Transmission à vérifier',
  failed: 'Échec',
  cancelled: 'Annulé',
  reply: 'Réponse reçue',
  positive_reply: 'Intérêt confirmé',
  out_of_office: 'Absence',
  bounce: 'Erreur de réception',
  stop: 'Opposition',
};
type Props = {
  id: string;
  api: Api;
  navigate: Navigate;
  onDirty: (value: boolean) => void;
  onSaved: () => Promise<void>;
  onClassify: (id: string) => void;
};
export function ConversationPanel({id, api, navigate, onDirty, onSaved, onClassify}: Props) {
  const [page, setPage] = useState(1),
    loaded = useRecord('/inbox/' + id + '/conversation?page=' + page, api),
    record = loaded.record;
  const [draft, setDraft] = useState<Row | null>(null),
    [context, setContext] = useState<Row | null>(null),
    [subject, setSubject] = useState(''),
    [body, setBody] = useState(''),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false),
    [sending, setSending] = useState(false);
  const initialized = useRef(false),
    original = useRef(''),
    request = useRef(crypto.randomUUID()),
    queueKey = useRef(crypto.randomUUID()),
    callback = useRef(onDirty);
  callback.current = onDirty;
  const dirty = !!context && JSON.stringify([subject, body]) !== original.current;
  useEffect(() => {
    callback.current(dirty);
    return () => callback.current(false);
  }, [dirty]);
  useEffect(() => {
    if (record && !initialized.current) {
      initialized.current = true;
      setContext(record);
      setDraft(record.draft);
      setSubject(record.draft?.subject || record.subject);
      setBody(record.draft?.body || '');
      original.current = JSON.stringify([record.draft?.subject || record.subject, record.draft?.body || '']);
    }
  }, [record]);
  function reload() {
    initialized.current = false;
    setPage(1);
    loaded.refresh();
    setNotice('');
  }
  async function save() {
    if (!context) return;
    setBusy(true);
    setError('');
    try {
      const result = await api('/inbox/' + context.reply_to_event_id + '/reply-draft', 'POST', {
        subject,
        body,
        request_id: request.current,
        expected_event_updated_at: context.latest_updated_at,
        draft_id: draft?.id || null,
        expected_updated_at: draft?.updated_at || null,
      });
      setDraft(result);
      original.current = JSON.stringify([subject, body]);
      callback.current(false);
      setNotice('Brouillon enregistré.');
      loaded.refresh();
      await onSaved();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  async function send() {
    if (!draft) return;
    setSending(true);
    setError('');
    try {
      await api('/reply-messages/' + draft.id + '/send', 'POST', {
        expected_updated_at: draft.updated_at,
        request_id: queueKey.current,
      });
      setDraft({...draft, status: 'queued'});
      setNotice('Réponse mise en attente d’envoi.');
      loaded.refresh();
      await onSaved();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setSending(false);
    }
  }
  async function cancel(row: Row) {
    setBusy(true);
    setError('');
    try {
      await api('/reply-messages/' + row.id + '/cancel', 'POST');
      if (draft?.id === row.id) {
        setDraft(null);
        setBody('');
        original.current = JSON.stringify([subject, '']);
        request.current = crypto.randomUUID();
        queueKey.current = crypto.randomUUID();
      }
      loaded.refresh();
      await onSaved();
      setNotice('Réponse annulée.');
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  const latest = record?.latest_message;
  const history: Row[] =
    record?.items.filter((row: Row) => row.id !== latest?.id && row.id !== draft?.id) || [];
  const pages = Math.max(1, Math.ceil((record?.total || 0) / (record?.page_size || 10)));
  function messageBody(row: Row, allowClassify = true) {
    return (
      <>
        <p className="op-email-body">{row.body}</p>
        {row.legacy && <small>Historique antérieur : contenu conservé lors de la collecte.</small>}
        {row.truncated && <small>Les 100 000 premiers caractères sont disponibles.</small>}
        {row.attachments?.length > 0 && (
          <p className="op-attachments">
            Pièces jointes : {row.attachments.map((item: Row) => item.name).join(', ')}. À télécharger dans la
            boîte mail.
          </p>
        )}
        {row.failure_reason && <p className="ws-inline-error">{row.failure_reason}</p>}
        {allowClassify && row.direction === 'in' && (
          <button onClick={() => onClassify(row.id)}>Classer cette réponse</button>
        )}
        {row.status === 'uncertain' && <button onClick={() => navigate('/alerts')}>Examiner l’envoi</button>}
        {row.purpose === 'reply' && ['draft', 'queued'].includes(row.status) && (
          <button disabled={busy} onClick={() => cancel(row)}>
            Annuler cette réponse
          </button>
        )}
      </>
    );
  }
  return (
    <div className="op-reply-workspace">
      <Feedback error={loaded.error || error} notice={notice} />
      <Panel
        title="Lire et répondre"
        description={
          context
            ? 'De ' +
              (context.sender?.email || 'boîte non attribuée') +
              ' à ' +
              (context.recipient || 'adresse manquante')
            : undefined
        }
        action={
          <button disabled={dirty || busy} onClick={reload}>
            <RefreshCw size={14} />
            Actualiser
          </button>
        }
      >
        {draft && draft.status !== 'draft' && (
          <p className="ws-context">
            {statusNames[draft.status] || draft.status}
            {draft.status === 'uncertain' && (
              <button onClick={() => navigate('/alerts')}>Examiner l’envoi</button>
            )}
          </p>
        )}
        {latest && (
          <div className="op-latest-message">
            <header>
              <strong>{latest.subject}</strong>
              <time>{when(latest.at)}</time>
            </header>
            <div className="op-message-reader" tabIndex={0} aria-label="Dernier message reçu">
              {messageBody(latest, false)}
            </div>
          </div>
        )}
        {context?.draft_reasons.length ? (
          <ul className="ws-sequence-problems">
            {context.draft_reasons.map((reason: string) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : (
          context && (
            <form
              className="op-reply-form"
              onSubmit={(event) => {
                event.preventDefault();
                save();
              }}
            >
              <details className="op-email-subject">
                <summary>Objet : {subject}</summary>
                <Field label="Objet de la réponse">
                  <input
                    value={subject}
                    required
                    maxLength={500}
                    disabled={busy || (!!draft && draft.status !== 'draft')}
                    onChange={(event) => {
                      setSubject(event.target.value);
                      request.current = crypto.randomUUID();
                    }}
                  />
                </Field>
              </details>
              <Field label="Votre réponse">
                <textarea
                  value={body}
                  rows={4}
                  required
                  maxLength={20000}
                  disabled={busy || (!!draft && draft.status !== 'draft')}
                  placeholder="Rédigez la suite de votre échange…"
                  onChange={(event) => {
                    setBody(event.target.value);
                    request.current = crypto.randomUUID();
                  }}
                />
              </Field>
              <div className="op-reply-actions">
                <button disabled={busy || !body.trim() || !dirty || (!!draft && draft.status !== 'draft')}>
                  <Save size={15} />
                  {busy ? 'Enregistrement…' : 'Enregistrer le brouillon'}
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={
                    sending ||
                    busy ||
                    dirty ||
                    !draft ||
                    draft.status !== 'draft' ||
                    context.send_reasons.length > 0
                  }
                  onClick={send}
                >
                  <Send size={15} />
                  {sending ? 'Mise en attente…' : 'Envoyer cette réponse'}
                </button>
                {draft && ['draft', 'queued'].includes(draft.status) && (
                  <button type="button" disabled={busy} onClick={() => cancel(draft)}>
                    {draft.status === 'queued' ? 'Annuler l’envoi' : 'Annuler le brouillon'}
                  </button>
                )}
              </div>
              {context.send_reasons.length > 0 && (
                <details className="op-send-reasons">
                  <summary>Envoi indisponible · brouillon conservable</summary>
                  <ul>
                    {context.send_reasons.map((reason: string) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </details>
              )}
            </form>
          )
        )}
      </Panel>
      <details className="op-conversation-history">
        <summary>Historique de la conversation · {record?.total ?? '…'} éléments</summary>
        <ol className="op-conversation">
          {history.map((row: Row) => (
            <li key={row.id} className={row.direction === 'in' ? 'received' : 'outgoing'}>
              <details>
                <summary>
                  <span>
                    {row.direction === 'in' ? <ArrowDownLeft size={14} /> : <ArrowUpRight size={14} />}
                    <strong>{row.subject || statusNames[row.status]}</strong>
                  </span>
                  <small>
                    {statusNames[row.status] || row.status} · {when(row.at)}
                  </small>
                </summary>
                <small>
                  {row.from_email}
                  {row.recipient_email ? ' → ' + row.recipient_email : ''}
                </small>
                {messageBody(row)}
              </details>
            </li>
          ))}
        </ol>
        {!history.length && <Blank title="Aucun autre échange sur cette page" />}
        {pages > 1 && (
          <div className="ws-pagination">
            <button disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>
              Échanges plus anciens
            </button>
            <span>
              {record?.page || page}/{pages}
            </span>
            <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
              Plus récents
            </button>
          </div>
        )}
      </details>
    </div>
  );
}
