import {useEffect, useRef, useState, type FormEvent} from 'react';
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  Eye,
  Linkedin,
  Mail,
  Phone,
  Plus,
  Save,
  Trash2,
} from 'lucide-react';
import {
  WorkspaceHeader,
  Panel,
  Field,
  Feedback,
  Blank,
  personName,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {languages} from './Locales';
import {errorText} from './api';
const channelNames: Row = {email: 'Email', phone: 'Appel', linkedin: 'LinkedIn manuel'};
const variables = ['prenom', 'nom', 'societe', 'role', 'pertinence'];
const channels: Row = {email: Mail, phone: Phone, linkedin: Linkedin};
function issues(steps: Row[]) {
  const errors: string[] = [];
  if (!steps.length) errors.push('Ajoutez une première étape.');
  steps.forEach((step, index) => {
    const text = step.body + (step.channel === 'email' ? '\n' + step.subject : '');
    const keys = [...text.matchAll(/\{\{\s*([\w_]+)\s*\}\}/g)].map((match) => match[1]);
    const unknown = [...new Set(keys.filter((key) => !variables.includes(key)))];
    if (unknown.length)
      errors.push('Étape ' + (index + 1) + ' · variables inconnues : ' + unknown.join(', '));
    if (/\{\{|\}\}/.test(text.replace(/\{\{\s*([\w_]+)\s*\}\}/g, '')))
      errors.push('Étape ' + (index + 1) + ' · variable mal formée.');
    if (!step.body.trim()) errors.push('Étape ' + (index + 1) + ' · contenu à rédiger.');
    if (step.channel === 'email' && !step.subject?.trim())
      errors.push('Étape ' + (index + 1) + ' · objet à rédiger.');
    if (
      !Number.isInteger(step.day) ||
      step.day < 0 ||
      step.day > 180 ||
      (index > 0 && step.day < steps[index - 1].day)
    )
      errors.push('Étape ' + (index + 1) + ' · corrigez le jour dans la chronologie.');
  });
  return errors;
}
export function SequenceComposer({
  steps,
  onChange,
  disabled = false,
}: {
  steps: Row[];
  onChange: (steps: Row[]) => void;
  disabled?: boolean;
}) {
  const [selected, setSelected] = useState(0);
  const index = Math.min(selected, Math.max(0, steps.length - 1)),
    step = steps[index];
  const area = useRef<HTMLTextAreaElement>(null);
  function edit(key: string, value: unknown) {
    onChange(steps.map((row, i) => (i === index ? {...row, [key]: value} : row)));
  }
  function move(delta: number) {
    const next = steps.map((row) => ({...row}));
    const days = next.map((row) => row.day);
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    onChange(next.map((row, i) => ({...row, day: days[i]})));
    setSelected(index + delta);
  }
  return (
    <div className="ws-composer">
      <div className="ws-sequence-rail">
        <ol>
          {steps.map((row, i) => {
            const Icon = channels[row.channel] || Mail;
            return (
              <li key={i}>
                <button
                  type="button"
                  aria-pressed={index === i}
                  disabled={disabled}
                  onClick={() => setSelected(i)}
                >
                  <span className="ws-sequence-dot">
                    <Icon size={16} />
                  </span>
                  <span>
                    <strong>
                      J+{row.day} · {channelNames[row.channel]}
                    </strong>
                    <small>{row.subject || row.body?.slice(0, 65) || 'Contenu à rédiger'}</small>
                  </span>
                  <ChevronRight size={14} />
                </button>
              </li>
            );
          })}
        </ol>
        <button
          type="button"
          className="ws-add-step"
          disabled={disabled || steps.length >= 10}
          onClick={() => {
            onChange([
              ...steps,
              {
                channel: 'email',
                day: Math.min(180, steps.length ? Number(steps[steps.length - 1].day) + 3 : 0),
                subject: '',
                body: '',
              },
            ]);
            setSelected(steps.length);
          }}
        >
          <Plus size={15} />
          Ajouter une étape
        </button>
        <small>{steps.length}/10 étapes · J+0 correspond au démarrage.</small>
      </div>
      <div className="ws-step-editor">
        {step ? (
          <>
            <header>
              <div>
                <span className="eyebrow">ÉTAPE {index + 1}</span>
                <h3>{channelNames[step.channel]}</h3>
              </div>
              <div className="actions">
                <button
                  type="button"
                  className="ws-icon"
                  disabled={disabled || index === 0}
                  aria-label="Monter cette étape"
                  onClick={() => move(-1)}
                >
                  <ArrowUp size={16} />
                </button>
                <button
                  type="button"
                  className="ws-icon"
                  disabled={disabled || index === steps.length - 1}
                  aria-label="Descendre cette étape"
                  onClick={() => move(1)}
                >
                  <ArrowDown size={16} />
                </button>
                <button
                  type="button"
                  className="ws-icon"
                  disabled={disabled}
                  aria-label="Supprimer cette étape"
                  onClick={() => {
                    onChange(steps.filter((_, i) => i !== index));
                    setSelected(Math.max(0, index - 1));
                  }}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </header>
            <div className="ws-form-grid">
              <Field label="Canal de l’étape">
                <select
                  value={step.channel}
                  disabled={disabled}
                  onChange={(event) => edit('channel', event.target.value)}
                >
                  {Object.entries(channelNames).map(([key, label]) => (
                    <option key={key} value={key}>
                      {String(label)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Jour après démarrage">
                <input
                  type="number"
                  min="0"
                  max="180"
                  value={step.day}
                  disabled={disabled}
                  onChange={(event) => edit('day', Number(event.target.value))}
                  required
                />
              </Field>
            </div>
            {step.channel === 'email' && (
              <Field label="Objet de l’email">
                <input
                  value={step.subject || ''}
                  disabled={disabled}
                  onChange={(event) => edit('subject', event.target.value)}
                  required
                  maxLength={500}
                />
              </Field>
            )}
            <Field label={step.channel === 'email' ? 'Contenu du message' : 'Consigne de l’action'}>
              <textarea
                ref={area}
                rows={8}
                value={step.body}
                disabled={disabled}
                onChange={(event) => edit('body', event.target.value)}
                required
                maxLength={10000}
              />
            </Field>
            <div className="ws-variable-tools">
              <span>Insérer une variable</span>
              {variables.map((key) => (
                <button
                  type="button"
                  key={key}
                  disabled={disabled}
                  onClick={() => {
                    const input = area.current;
                    const start = input?.selectionStart ?? step.body.length,
                      end = input?.selectionEnd ?? start;
                    const token = '{{' + key + '}}';
                    edit('body', step.body.slice(0, start) + token + step.body.slice(end));
                    requestAnimationFrame(() => {
                      input?.focus();
                      input?.setSelectionRange(start + token.length, start + token.length);
                    });
                  }}
                >
                  {'{{' + key + '}}'}
                </button>
              ))}
            </div>
          </>
        ) : (
          <Blank title="Construisez votre séquence">Ajoutez un email ou une action pour commencer.</Blank>
        )}
      </div>
    </div>
  );
}

type Props = {
  id: string;
  api: Api;
  navigate: Navigate;
  onRefresh: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  discardVersion: number;
  admin: boolean;
};
export function SequenceEditor({id, api, navigate, onRefresh, onDirtyChange, discardVersion, admin}: Props) {
  const [loaded, setLoaded] = useState<Row | null>(null),
    [draft, setDraft] = useState<Row | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false),
    [reload, setReload] = useState(0);
  const [query, setQuery] = useState(''),
    [contacts, setContacts] = useState<Row[]>([]),
    [contactId, setContactId] = useState(''),
    [preview, setPreview] = useState<Row | null>(null),
    [previewBusy, setPreviewBusy] = useState(false),
    [contactError, setContactError] = useState('');
  const previewGeneration = useRef(0);
  const original = useRef(''),
    requestId = useRef(crypto.randomUUID()),
    dirtyRef = useRef(onDirtyChange);
  dirtyRef.current = onDirtyChange;
  useEffect(() => {
    let alive = true;
    previewGeneration.current++;
    setLoaded(null);
    setDraft(null);
    setError('');
    setNotice('');
    setPreview(null);
    setContactId('');
    requestId.current = crypto.randomUUID();
    dirtyRef.current(false);
    api('/campaigns/' + id + '/sequence')
      .then((result) => {
        if (!alive) return;
        const row = result.campaign;
        const initial = {
          name: row.name,
          sequence: row.sequence.map((step: Row) => ({...step})),
          language: row.config.language || '',
          localized_footer: row.config.localized_footer || '',
        };
        setLoaded(result);
        setDraft(initial);
        original.current = JSON.stringify(initial);
        document.title = 'Séquence · ' + row.name + ' · Lumelia';
      })
      .catch((error) => {
        if (alive) setError(errorText(error));
      });
    return () => {
      alive = false;
      dirtyRef.current(false);
    };
  }, [api, id, reload, discardVersion]);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      api('/lists/contacts?page_size=10&q=' + encodeURIComponent(query))
        .then((result) => {
          if (alive) {
            setContacts(result.items);
            setContactError('');
          }
        })
        .catch((error) => {
          if (alive) setContactError(errorText(error));
        });
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, query]);
  const dirty = !!draft && JSON.stringify(draft) !== original.current;
  useEffect(() => {
    dirtyRef.current(dirty);
  }, [dirty]);
  function update(patch: Row) {
    previewGeneration.current++;
    setPreviewBusy(false);
    setDraft((value) => ({...value, ...patch}));
    setPreview(null);
    setNotice('');
    requestId.current = crypto.randomUUID();
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || !loaded) return;
    setBusy(true);
    setError('');
    try {
      const result = await api('/campaigns/' + id + '/sequence', 'PUT', {
        ...draft,
        expected_updated_at: loaded.campaign.updated_at,
        request_id: requestId.current,
      });
      const current = {...draft};
      original.current = JSON.stringify(current);
      dirtyRef.current(false);
      setLoaded({campaign: result.campaign, edit_in_place: true});
      await onRefresh();
      if (result.saved_as === 'new_version') navigate('/sequences/' + result.campaign.id + '?created=1');
      else setNotice('Séquence enregistrée en brouillon.');
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  async function inspect() {
    if (!draft) return;
    const generation = ++previewGeneration.current;
    setPreviewBusy(true);
    setError('');
    try {
      const result = await api('/campaigns/' + id + '/sequence/preview', 'POST', {
        sequence: draft.sequence,
        contact_id: contactId || null,
      });
      if (generation === previewGeneration.current) setPreview(result);
    } catch (error) {
      if (generation === previewGeneration.current) setError(errorText(error));
    } finally {
      if (generation === previewGeneration.current) setPreviewBusy(false);
    }
  }
  if (!draft || !loaded)
    return (
      <div className="ws-page">
        <WorkspaceHeader
          back="/?view=campaigns"
          backLabel="Toutes les campagnes"
          eyebrow="ÉDITEUR DE SÉQUENCE"
          title={error ? 'Séquence indisponible' : 'Chargement de la séquence…'}
          navigate={navigate}
        />
        <Feedback error={error} />
        {error && <button onClick={() => setReload((value) => value + 1)}>Réessayer</button>}
      </div>
    );
  const row = loaded.campaign,
    problems = issues(draft.sequence);
  const back = row.group_id ? '/campaigns/' + row.group_id + '?tab=sequences' : '/?view=campaigns';
  return (
    <article className="ws-page sequence-workspace">
      <WorkspaceHeader
        back={back}
        backLabel="Retour à la campagne"
        eyebrow={'SÉQUENCE · VERSION ' + (row.config.sequence_version || 1)}
        title={row.name}
        subtitle={
          row.niche +
          ' · ' +
          (loaded.edit_in_place ? 'Brouillon modifiable' : 'Nouvelle version à la sauvegarde')
        }
        navigate={navigate}
      />
      <Feedback
        error={error}
        notice={
          notice ||
          (new URLSearchParams(location.search).get('created') === '1'
            ? 'Nouvelle version créée en brouillon. Les contacts sont restés sur leur version précédente.'
            : '')
        }
      />
      <form className="ws-sequence-form" onSubmit={save}>
        <Panel
          title="Construire la séquence"
          description={
            loaded.edit_in_place
              ? 'Ce brouillon n’a encore aucun contact ou élément préparé.'
              : 'L’enregistrement créera une nouvelle version en brouillon. Les contacts, messages et échéances actuels seront conservés.'
          }
        >
          <div className="ws-form-grid">
            <Field label="Nom de la sous-campagne">
              <input
                value={draft.name}
                disabled={!admin || busy}
                onChange={(event) => update({name: event.target.value})}
                required
                maxLength={180}
              />
            </Field>
            <Field label="Langue de la séquence">
              <select
                value={draft.language}
                disabled={!admin || busy}
                onChange={(event) => update({language: event.target.value})}
                required
              >
                <option value="">Choisir une langue</option>
                {Object.entries(languages).map(([key, label]) => (
                  <option key={key} value={key}>
                    {String(label)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <SequenceComposer
            steps={draft.sequence}
            onChange={(sequence) => update({sequence})}
            disabled={!admin || busy}
          />
          {draft.language &&
            draft.language !== 'fr' &&
            draft.sequence.some((step: Row) => step.channel === 'email') && (
              <Field
                label="Pied d’email dans la langue choisie"
                hint="Identité, motif du contact et possibilité d’opposition."
              >
                <textarea
                  value={draft.localized_footer}
                  onChange={(event) => update({localized_footer: event.target.value})}
                  required
                  minLength={20}
                  maxLength={5000}
                  disabled={!admin || busy}
                />
              </Field>
            )}
          {problems.length > 0 && (
            <ul className="ws-sequence-problems" aria-label="Points à compléter dans la séquence">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          <div className="ws-save-bar">
            <span>{dirty ? 'Modifications non enregistrées' : 'Version enregistrée'}</span>
            {admin && (
              <button
                type="submit"
                className="primary"
                disabled={busy || problems.length > 0 || !draft.language || !dirty}
              >
                <Save size={16} />
                {busy
                  ? 'Enregistrement…'
                  : loaded.edit_in_place
                    ? 'Enregistrer le brouillon'
                    : 'Créer la nouvelle version'}
              </button>
            )}
          </div>
        </Panel>
      </form>
      <Panel
        title="Aperçu avec un contact"
        description="Vérifiez les textes personnalisés et les informations manquantes avant de préparer les messages."
      >
        <div className="ws-preview-controls">
          <Field label="Rechercher un contact pour l’aperçu">
            <input
              type="search"
              value={query}
              onChange={(event) => {
                previewGeneration.current++;
                setPreviewBusy(false);
                setQuery(event.target.value);
                setContactId('');
                setPreview(null);
              }}
              placeholder="Nom, email ou entreprise…"
            />
          </Field>
          <Field label="Contact de l’aperçu">
            <select
              value={contactId}
              onChange={(event) => {
                previewGeneration.current++;
                setPreviewBusy(false);
                setContactId(event.target.value);
                setPreview(null);
              }}
            >
              <option value="">Choisir un contact</option>
              {contacts.map((contact) => (
                <option value={contact.id} key={contact.id}>
                  {personName(contact)} · {contact.account.name}
                </option>
              ))}
            </select>
          </Field>
          <button disabled={previewBusy || !contactId || !draft.sequence.length} onClick={inspect}>
            <Eye size={16} />
            {previewBusy ? 'Préparation…' : 'Prévisualiser'}
          </button>
        </div>
        <Feedback error={contactError} />
        {preview ? (
          <div className="ws-preview-list">
            <p className="ws-context">
              Aperçu pour {personName(preview.contact)} · {preview.account?.name}. L’expéditeur et le pied
              d’email complet seront vérifiés après attribution d’une boîte.
            </p>
            {preview.steps.map((step: Row, index: number) => (
              <section className="ws-message-preview" key={index}>
                <header>
                  <strong>
                    J+{step.day} · {channelNames[step.channel]}
                  </strong>
                  <span>Étape {index + 1}</span>
                </header>
                {step.missing.length > 0 && (
                  <p className="ws-inline-error" role="status">
                    À renseigner sur ce contact : {step.missing.join(', ')}.
                  </p>
                )}
                {step.issues.map((problem: string) => (
                  <p className="ws-inline-error" key={problem}>
                    {problem}
                  </p>
                ))}
                {step.subject && <h3>{step.subject}</h3>}
                <p>{step.body || 'Contenu à rédiger.'}</p>
                {!step.missing.length && !step.issues.length && (
                  <small>
                    <Check size={13} />
                    Variables renseignées
                  </small>
                )}
              </section>
            ))}
          </div>
        ) : (
          <Blank title="Choisissez un contact pour relire la séquence">
            L’aperçu n’enregistre aucun message.
          </Blank>
        )}
      </Panel>
    </article>
  );
}
