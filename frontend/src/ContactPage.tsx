import {CallWorkspace} from './CallWorkspace';
import {callBlockedReason} from './callPolicy';
import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
  type FormEvent,
  type MouseEvent,
} from 'react';
import {
  ArrowLeft,
  ArrowUpRight,
  Building2,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Copy,
  ExternalLink,
  FileText,
  Globe2,
  Linkedin,
  Mail,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Phone,
  Plus,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react';
import {languages, markets} from './Locales';
import {contactUrl, type ContactTab} from './navigation';
import './contact-page.css';
import {errorText, type Api, type Json, type Row, type Upload} from './api';

type EditorKind =
  'profile' | 'qualification' | 'verification' | 'language' | 'campaign' | 'sender' | 'exchange';
type Editor = {kind: EditorKind; values: Row; original: Row};
type Props = {
  id: string;
  tab: ContactTab;
  data: Row;
  admin: boolean;
  api: Api;
  upload: Upload;
  onOpen: (name: string, row: Row) => void;
  onRefresh: () => Promise<void>;
  navigate: (url: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  discardVersion: number;
};
const names: Row = {
  candidate: 'À qualifier',
  qualified: 'Qualifié',
  conversation: 'En conversation',
  suppressed: 'Opposition',
  active: 'Active',
  paused: 'En pause',
  draft: 'Brouillon',
  approved: 'Validé',
  queued: 'Planifié',
  sent: 'Envoyé',
  cancelled: 'Annulé',
  failed: 'En erreur',
  uncertain: 'À rapprocher',
  simulated: 'Simulé',
  unknown: 'À vérifier',
  valid: 'Vérifié',
  invalid: 'Invalide',
  risky: 'Risqué',
  appropriate: 'Approprié à la fonction',
  direct: 'Ligne directe',
  switchboard: 'Standard',
  pending: 'À préparer',
  planned: 'Prévue au premier contact',
  provided: 'Déjà fournie',
  confirmed: 'Confirmé',
  signal: 'Signal public',
  absent: 'Absent',
  unverified: 'À vérifier',
  email: 'Email',
  phone: 'Téléphone',
  linkedin: 'LinkedIn',
  review: 'Suivi interne',
  system: 'Application',
  official_website: 'Site officiel',
  conversation_source: 'Conversation',
  manual: 'Recherche manuelle',
  provider: 'Fournisseur',
  fullenrich: 'FullEnrich',
  hunter: 'Hunter',
  csv: 'Import CSV',
  reply: 'Réponse reçue',
  positive_reply: 'Réponse positive',
  out_of_office: 'Absence',
  meeting: 'Rendez-vous confirmé',
  stop: 'Opposition',
  bounce: 'Email en erreur',
  note: 'Note',
  contact_created: 'Contact ajouté',
  contact_updated: 'Fiche modifiée',
  contact_approved: 'Qualification validée',
  evidence_added: 'Source ajoutée',
  language_reviewed: 'Langue vérifiée',
  prepared: 'Séquence préparée',
  messages_prepared: 'Séquence préparée',
  message_approved: 'Email validé',
  task_completed: 'Action terminée',
};
const evidenceNames: Row = {
  phone: 'Téléphone',
  phone_company: 'Téléphone de l’entreprise',
  phone_verification: 'Vérification du téléphone',
  email: 'Email',
  email_verification: 'Vérification de l’email',
  identity: 'Identité',
  fonction: 'Fonction',
  role: 'Fonction',
};
const tabNames: [ContactTab, string][] = [
  ['overview', 'Vue d’ensemble'],
  ['qualification', 'Qualification'],
  ['emails', 'Emails'],
  ['history', 'Historique & sources'],
];
const editorNames: Record<EditorKind, [string, string]> = {
  profile: ['Modifier le contact', 'Identité et coordonnées professionnelles.'],
  qualification: [
    'Préciser la pertinence',
    'Documentez le lien entre la fonction du contact et votre offre.',
  ],
  verification: ['Vérifier les coordonnées', 'Enregistrez les contrôles que vous avez réellement effectués.'],
  language: ['Vérifier la langue', 'Appuyez-vous sur un échange, un profil ou une page professionnelle.'],
  campaign: ['Affecter une sous-campagne', 'Choisissez la version dont le ciblage correspond à ce contact.'],
  sender: ['Choisir la boîte expéditrice', 'La même adresse est conservée tout au long de la conversation.'],
  exchange: ['Ajouter un échange', 'Gardez une trace de la conversation ou d’une note de recherche.'],
};
const formatDate = (value?: string, short = false) =>
  value
    ? new Date(value).toLocaleString(
        'fr-FR',
        short ? {day: 'numeric', month: 'short'} : {dateStyle: 'medium', timeStyle: 'short'},
      )
    : '—';
function Tag({value, children}: {value: string; children?: ReactNode}) {
  return <span className={'cp-tag cp-tag-' + value}>{children || names[value] || value}</span>;
}
function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <div className="cp-field">
      <label htmlFor={id}>
        {label}
        {required && (
          <span className="cp-required" aria-hidden="true">
            {' '}
            *
          </span>
        )}
      </label>
      {isValidElement(children)
        ? cloneElement(children as ReactElement<HTMLAttributes<HTMLElement>>, {
            id,
            'aria-describedby': hint ? id + '-hint' : undefined,
          })
        : children}
      {hint && <small id={id + '-hint'}>{hint}</small>}
    </div>
  );
}
function Section({
  title,
  children,
  action,
  description,
  className = '',
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
  description?: string;
  className?: string;
}) {
  return (
    <section className={'cp-section ' + className}>
      <header>
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
function CopyValue({value, label}: {value: string; label: string}) {
  const [copied, setCopied] = useState(false),
    [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <>
      <button
        className="cp-icon-button"
        aria-label={copied ? label + ' copié' : 'Copier ' + label.toLowerCase()}
        title={copied ? 'Copié' : 'Copier'}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setFailed(false);
            clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 2000);
          } catch {
            setFailed(true);
          }
        }}
      >
        {copied ? <Check size={16} /> : <Copy size={16} />}
      </button>
      {failed && <small role="status">Copie indisponible. Sélectionnez le texte.</small>}
    </>
  );
}
function Timeline({events, compact = false}: {events: Row[]; compact?: boolean}) {
  return (
    <ol className={'cp-timeline ' + (compact ? 'cp-timeline-compact' : '')}>
      {events.map((event) => (
        <li key={event.id}>
          <span className="cp-timeline-dot">
            {event.kind === 'note' ? (
              <FileText size={14} />
            ) : ['reply', 'positive_reply'].includes(event.kind) ? (
              <MessageSquare size={14} />
            ) : event.channel === 'phone' ? (
              <Phone size={14} />
            ) : (
              <Clock3 size={14} />
            )}
          </span>
          <div>
            <div className="cp-event-heading">
              <strong>{names[event.kind] || event.kind.replaceAll('_', ' ')}</strong>
              <time dateTime={event.created_at}>{formatDate(event.created_at, compact)}</time>
            </div>
            <p>{event.summary || 'Aucun compte rendu ajouté.'}</p>
            <small>{names[event.channel] || event.channel || 'Application'}</small>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function ContactPage({
  id,
  tab,
  data,
  admin,
  api,
  upload,
  onOpen,
  onRefresh,
  navigate,
  onDirtyChange,
  discardVersion,
}: Props) {
  const [contact, setContact] = useState<Row | null>(null);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null),
    [more, setMore] = useState(false);
  const [historyFilter, setHistoryFilter] = useState('conversations'),
    [expandedMessage, setExpandedMessage] = useState('');
  const [eventLimit, setEventLimit] = useState(10);
  const titleRef = useRef<HTMLHeadingElement>(null),
    editorRef = useRef<HTMLFormElement>(null),
    menuRef = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const idRef = useRef(id);
  idRef.current = id;
  const dirtyCallback = useRef(onDirtyChange);
  dirtyCallback.current = onDirtyChange;
  const refresh = async () => {
    const request = ++generation.current;
    const result = await api('/contacts/' + id);
    if (request === generation.current && idRef.current === id) setContact(result);
  };
  useEffect(() => {
    let active = true;
    const request = ++generation.current;
    setLoading(true);
    setContact(null);
    setError('');
    setNotice('');
    setEditor(null);
    setMore(false);
    dirtyCallback.current(false);
    api('/contacts/' + id)
      .then((result) => {
        if (active && request === generation.current) {
          setContact(result);
          setLoading(false);
        }
      })
      .catch((error) => {
        if (active) {
          setError(errorText(error));
          setLoading(false);
        }
      });
    return () => {
      active = false;
      // eslint-disable-next-line react-hooks/exhaustive-deps -- generation is a request counter, not a DOM node: the cleanup must invalidate the latest request
      generation.current++;
      dirtyCallback.current(false);
    };
  }, [api, id]);
  useEffect(() => {
    if (!contact || contact.id !== id) return;
    let active = true;
    const request = ++generation.current;
    api('/contacts/' + id)
      .then((result) => {
        if (active && request === generation.current) setContact(result);
      })
      .catch(() => {
        /* Preserve the loaded record during a background refresh. */
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- background refresh follows shared data, not the record it has just replaced
  }, [data]);
  useEffect(() => {
    setEditor(null);
    setError('');
    setMore(false);
    dirtyCallback.current(false);
  }, [tab, discardVersion]);
  const shown = contact?.id === id && !loading,
    pageTitle = contact ? `${contact.first_name} ${contact.last_name} · Lumelia` : '';
  useEffect(() => {
    if (shown) document.title = pageTitle;
  }, [shown, pageTitle]);
  useEffect(() => {
    if (shown) titleRef.current?.focus({preventScroll: true});
  }, [shown]);
  const editorKind = editor?.kind;
  useEffect(() => {
    if (editorKind) {
      editorRef.current?.closest('.cp-editor')?.scrollIntoView({block: 'start', behavior: 'instant'});
      editorRef.current
        ?.querySelector<HTMLInputElement>('input, textarea, select')
        ?.focus({preventScroll: true});
    }
  }, [editorKind]);
  useEffect(() => {
    if (!more) return;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMore(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMore(false);
        menuRef.current?.querySelector('button')?.focus();
      }
    };
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', escape);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', escape);
    };
  }, [more]);

  function follow(event: MouseEvent<HTMLAnchorElement>, url: string) {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(url);
  }
  function begin(kind: EditorKind) {
    if (!contact || editor) return;
    let values: Row = {};
    const keys: Partial<Record<EditorKind, string[]>> = {
      profile: ['first_name', 'last_name', 'role', 'email', 'phone', 'linkedin_url'],
      qualification: ['professional_relevance', 'information_status'],
      verification: ['email_status', 'phone_type', 'phone_status', 'sourcing_note'],
      campaign: ['campaign_id'],
      sender: ['email_sender_id'],
    };
    if (kind === 'language')
      values = {
        language: contact.communication_language?.language || '',
        note: contact.communication_language?.note || '',
        source_url: contact.communication_language?.source_url || '',
      };
    else if (kind === 'exchange') values = {kind: 'note', channel: 'email', summary: ''};
    else for (const key of keys[kind] || []) values[key] = contact[key] || '';
    setEditor({kind, values, original: {...values}});
    setError('');
    setNotice('');
    setMore(false);
  }
  function update(key: string, value: string) {
    if (!editor) return;
    const next = {...editor, values: {...editor.values, [key]: value}};
    setEditor(next);
    onDirtyChange(JSON.stringify(next.values) !== JSON.stringify(next.original));
  }
  function cancelEdit() {
    setEditor(null);
    onDirtyChange(false);
    setError('');
    titleRef.current?.focus();
  }
  async function run(
    fn: () => Promise<Json>,
    success: string | ((result: Json) => string),
    closeEditor = false,
  ) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await fn();
      if (closeEditor) {
        setEditor(null);
        onDirtyChange(false);
        titleRef.current?.focus();
      }
      setNotice(typeof success === 'function' ? success(result) : success);
      try {
        await Promise.all([refresh(), onRefresh()]);
      } catch {
        setError('La modification est enregistrée. Actualisez la page pour retrouver les dernières données.');
      }
      return true;
    } catch (error) {
      setError(errorText(error));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editor || !contact) return;
    const {kind, values, original} = editor;
    const changed: Row = {};
    for (const [key, value] of Object.entries(values))
      if (value !== original[key]) changed[key] = typeof value === 'string' ? value.trim() : value;
    if ('email' in changed) changed.email = changed.email || null;
    if ('campaign_id' in changed) changed.campaign_id = changed.campaign_id || null;
    const suffix =
      kind === 'language'
        ? '/language'
        : kind === 'exchange'
          ? '/events'
          : kind === 'sender'
            ? '/sender'
            : '';
    const payload =
      kind === 'language' || kind === 'exchange'
        ? values
        : kind === 'sender'
          ? {sender_id: values.email_sender_id || null}
          : changed;
    await run(
      () => api('/contacts/' + contact.id + suffix, suffix ? 'POST' : 'PATCH', payload),
      kind === 'exchange' ? 'Échange ajouté à l’historique.' : 'Modifications enregistrées.',
      true,
    );
  }
  const back = (
    <a className="cp-back" href="/contacts" onClick={(event) => follow(event, '/contacts')}>
      <ArrowLeft size={16} />
      Tous les contacts
    </a>
  );
  if (loading)
    return (
      <div className="contact-page">
        {back}
        <div className="cp-loading" role="status">
          <div className="cp-skeleton cp-skeleton-title" />
          <div className="cp-skeleton" />
          <p>Chargement du contact…</p>
        </div>
      </div>
    );
  if (!contact)
    return (
      <div className="contact-page">
        {back}
        <section className="cp-missing">
          <CircleHelp size={32} />
          <h1>Fiche indisponible</h1>
          <p>{error || 'Ce contact ne fait plus partie de votre espace.'}</p>
          <button
            onClick={() => {
              setLoading(true);
              refresh()
                .catch((error) => setError(errorText(error)))
                .finally(() => setLoading(false));
            }}
          >
            Réessayer
          </button>
        </section>
      </div>
    );

  const campaign = data.campaigns.find((row: Row) => row.id === contact.campaign_id);
  const offer = data.offers.find((row: Row) => row.id === campaign?.offer_id);
  const account = contact.account;
  const checks: Row[] = contact.fit?.checks || [];
  const messages: Row[] = data.messages.filter((row: Row) => row.contact_id === id);
  const tasks: Row[] = data.tasks.filter((row: Row) => row.contact_id === id && row.status === 'pending');
  const events: Row[] = contact.events || [];
  const exchanges = events.filter((row) => row.channel !== 'system');
  const emailBlockers: string[] = contact.eligibility?.email || [];
  const hasHold =
    contact.paused ||
    account.outreach_paused ||
    account.lifecycle !== 'prospect' ||
    contact.status === 'suppressed';
  const channelChecks: Row = contact.eligibility || {};
  const usedChannels: string[] = [
    ...new Set<string>((campaign?.sequence || []).map((step: Row) => step.channel)),
  ];
  const mayApprove =
    !hasHold &&
    usedChannels.some(
      (channel) =>
        (channelChecks[channel] || []).filter((reason: string) => reason !== 'Contact non validé').length ===
        0,
    );
  const readyMessages = messages.filter((row) => ['draft', 'approved', 'queued'].includes(row.status));
  const archivedMessages = messages.filter((row) => !['draft', 'approved', 'queued'].includes(row.status));
  const locked = busy || !!editor;
  const fullName = [contact.first_name, contact.last_name].filter(Boolean).join(' ') || 'Contact sans nom';
  const status =
    account.lifecycle === 'excluded'
      ? 'excluded'
      : contact.status === 'suppressed'
        ? 'suppressed'
        : hasHold
          ? 'paused'
          : contact.status;
  const modification = (kind: EditorKind, label = 'Modifier') => (
    <button
      className="cp-text-button"
      aria-label={label ? undefined : editorNames[kind][0]}
      disabled={locked}
      onClick={() => begin(kind)}
    >
      <Pencil size={14} />
      {label}
    </button>
  );
  const field = (
    label: string,
    key: string,
    options: {
      type?: string;
      required?: boolean;
      max?: number;
      hint?: string;
      placeholder?: string;
      textarea?: boolean;
      choices?: Row;
    } = {},
  ) => (
    <Field label={label} hint={options.hint} required={options.required}>
      {options.choices ? (
        <select
          disabled={busy}
          name={key}
          value={editor?.values[key] || ''}
          required={options.required}
          onChange={(event) => update(key, event.target.value)}
        >
          {Object.entries(options.choices).map(([value, label]) => (
            <option key={value} value={value}>
              {String(label)}
            </option>
          ))}
        </select>
      ) : options.textarea ? (
        <textarea
          disabled={busy}
          name={key}
          rows={4}
          value={editor?.values[key] || ''}
          required={options.required}
          minLength={options.required && key === 'note' ? 10 : undefined}
          maxLength={options.max || 2000}
          placeholder={options.placeholder}
          onChange={(event) => update(key, event.target.value)}
        />
      ) : (
        <input
          disabled={busy}
          name={key}
          type={options.type || 'text'}
          value={editor?.values[key] || ''}
          required={options.required}
          maxLength={options.max || 180}
          placeholder={options.placeholder}
          autoComplete="off"
          onChange={(event) => update(key, event.target.value)}
        />
      )}
    </Field>
  );
  const showMessage = (message: Row) => (
    <article className="cp-email" key={message.id}>
      <button
        className="cp-email-summary"
        aria-expanded={expandedMessage === message.id}
        onClick={() => setExpandedMessage(expandedMessage === message.id ? '' : message.id)}
      >
        <Mail size={17} />
        <span>
          <strong>{message.subject || 'Sans objet'}</strong>
          <small>
            {message.sent_at
              ? 'Envoyé le ' + formatDate(message.sent_at)
              : message.status === 'cancelled'
                ? 'Conservé dans l’historique'
                : 'À relire avant validation'}
          </small>
        </span>
        <Tag value={message.status} />
        <ChevronDown size={17} className={expandedMessage === message.id ? 'cp-rotated' : ''} />
      </button>
      {expandedMessage === message.id && (
        <div className="cp-email-body">
          <p>{message.body}</p>
          {message.failure_reason && <div className="cp-inline-note">{message.failure_reason}</div>}
          <div className="cp-actions">
            <button
              disabled={busy}
              onClick={() =>
                run(async () => onOpen('preview', await api('/messages/' + message.id + '/preview')), '')
              }
            >
              Voir l’email complet
              <ArrowUpRight size={14} />
            </button>
            {['draft', 'approved', 'queued'].includes(message.status) && (
              <button disabled={busy} onClick={() => onOpen('editMessage', message)}>
                <Pencil size={14} />
                Modifier
              </button>
            )}
            {admin && message.status === 'draft' && (
              <button
                className="primary"
                disabled={busy || emailBlockers.length > 0}
                onClick={() =>
                  run(() => api('/messages/' + message.id + '/approve', 'POST'), 'Email validé.')
                }
              >
                <Check size={15} />
                Valider cet email
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  );

  return (
    <article className="contact-page">
      <div className="cp-breadcrumb">
        {back}
        <span>Fiche contact</span>
      </div>
      <header className="cp-hero">
        <div className="cp-person">
          <div className="cp-monogram" aria-hidden="true">
            {(contact.first_name?.[0] || '') + (contact.last_name?.[0] || '')}
          </div>
          <div>
            <div className="cp-name-row">
              <h1 ref={titleRef} tabIndex={-1}>
                {fullName}
              </h1>
              <Tag value={status}>{status === 'excluded' ? 'Entreprise exclue' : undefined}</Tag>
            </div>
            <p>
              {contact.role || 'Fonction à renseigner'}
              <span aria-hidden="true"> · </span>
              <a
                href={'/companies/' + account.id}
                onClick={(event) => follow(event, '/companies/' + account.id)}
              >
                {account.name}
              </a>
            </p>
          </div>
        </div>
        <div className="cp-hero-actions">
          <button disabled={locked} onClick={() => begin('profile')}>
            <Pencil size={15} />
            Modifier
          </button>
          <div className="cp-more" ref={menuRef}>
            <button
              className="cp-more-trigger"
              disabled={locked}
              aria-label="Autres actions sur le contact"
              aria-expanded={more}
              onClick={() => setMore(!more)}
            >
              <MoreHorizontal size={19} />
            </button>
            {more && (
              <div className="cp-more-menu">
                <button
                  onClick={() => {
                    setMore(false);
                    onOpen('account', account);
                  }}
                >
                  Modifier l’entreprise
                </button>
                {admin && (
                  <>
                    <button
                      onClick={() => {
                        setMore(false);
                        onOpen('enrich', contact);
                      }}
                    >
                      Enrichir les coordonnées
                    </button>
                    <a href={'/api/contacts/' + id + '/export'} download>
                      Exporter la fiche
                    </a>
                    <button
                      onClick={() => {
                        setMore(false);
                        onOpen('retention', contact);
                      }}
                    >
                      Conservation des données
                    </button>
                    {(contact.paused || account.outreach_paused) && (
                      <button
                        onClick={() => {
                          setMore(false);
                          onOpen('resume', contact);
                        }}
                      >
                        Reprendre après revue
                      </button>
                    )}
                    <button
                      className="cp-danger"
                      onClick={() => {
                        setMore(false);
                        onOpen('erase', contact);
                      }}
                    >
                      Effacer la fiche…
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </header>
      <div
        className="cp-tabs"
        role="tablist"
        aria-label="Rubriques du contact"
        onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const index = tabNames.findIndex(([key]) => key === tab);
          const next =
            event.key === 'Home'
              ? 0
              : event.key === 'End'
                ? tabNames.length - 1
                : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabNames.length) % tabNames.length;
          (event.currentTarget.querySelectorAll('button')[next] as HTMLButtonElement).focus();
          navigate(contactUrl(id, tabNames[next][0]));
        }}
      >
        {tabNames.map(([key, label]) => (
          <button
            role="tab"
            id={'cp-tab-' + key}
            aria-controls="cp-tab-panel"
            aria-selected={tab === key}
            tabIndex={tab === key ? 0 : -1}
            key={key}
            onClick={() => navigate(contactUrl(id, key))}
          >
            {label}
            {key === 'emails' && readyMessages.length > 0 && <span>{readyMessages.length}</span>}
          </button>
        ))}
      </div>
      {notice && (
        <div className="cp-feedback cp-feedback-success" role="status">
          <CheckCircle2 size={17} />
          <span>{notice}</span>
          <button
            className="cp-icon-button"
            aria-label="Masquer la confirmation"
            onClick={() => setNotice('')}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {error && (
        <div className="cp-feedback cp-feedback-error" role="alert">
          <CircleHelp size={18} />
          <span>{error}</span>
          <button className="cp-icon-button" aria-label="Masquer l’erreur" onClick={() => setError('')}>
            <X size={16} />
          </button>
        </div>
      )}
      <div className="cp-layout">
        <div className="cp-quick-actions">
          <button className="primary" disabled={locked} onClick={() => begin('exchange')}>
            <MessageSquare size={16} />
            Ajouter un échange
          </button>
          <button disabled={locked} onClick={() => onOpen('newTask', contact)}>
            <CalendarDays size={16} />
            Planifier une action
          </button>
        </div>
        <div className="cp-main-column" role="tabpanel" id="cp-tab-panel" aria-labelledby={'cp-tab-' + tab}>
          {editor ? (
            <section className="cp-section cp-editor">
              <header>
                <div>
                  <span className="cp-overline">Modification de la fiche</span>
                  <h2>{editorNames[editor.kind][0]}</h2>
                  <p>{editorNames[editor.kind][1]}</p>
                </div>
              </header>
              <form ref={editorRef} onSubmit={save}>
                {editor.kind === 'profile' && (
                  <>
                    <div className="cp-form-grid">
                      {field('Prénom', 'first_name', {max: 120})}
                      {field('Nom', 'last_name', {max: 120})}
                    </div>
                    {field('Fonction', 'role', {placeholder: 'Ex. Directrice commerciale'})}
                    <div className="cp-form-grid">
                      {field('Email professionnel', 'email', {
                        type: 'email',
                        max: 320,
                        placeholder: 'prenom@example.com',
                      })}
                      {field('Téléphone', 'phone', {type: 'tel', max: 50, placeholder: '+33 1 99 00 00 00'})}
                    </div>
                    {field('Profil LinkedIn', 'linkedin_url', {
                      type: 'url',
                      max: 600,
                      placeholder: 'https://www.linkedin.com/in/…',
                    })}
                    {(editor.values.email !== editor.original.email ||
                      editor.values.phone !== editor.original.phone) && (
                      <p className="cp-inline-note">
                        Les nouvelles coordonnées seront à vérifier après l’enregistrement.
                      </p>
                    )}
                  </>
                )}
                {editor.kind === 'qualification' && (
                  <>
                    {field('Pourquoi cette offre concerne sa fonction', 'professional_relevance', {
                      textarea: true,
                      required: true,
                      hint: 'Séparez les faits confirmés des hypothèses à explorer.',
                    })}
                    {field('Information sur l’utilisation des données', 'information_status', {
                      choices: {
                        pending: 'À préparer',
                        planned: 'Prévue au premier contact',
                        provided: 'Déjà fournie',
                      },
                    })}
                  </>
                )}
                {editor.kind === 'verification' && (
                  <>
                    <div className="cp-form-context">
                      <Mail size={15} />
                      <span>{contact.email || 'Email non renseigné'}</span>
                    </div>
                    {field('Vérification de l’email', 'email_status', {
                      choices: {
                        unknown: 'À vérifier',
                        valid: 'Vérifié',
                        risky: 'Risqué',
                        invalid: 'Invalide',
                      },
                    })}
                    <div className="cp-form-context">
                      <Phone size={15} />
                      <span>{contact.phone || 'Téléphone non renseigné'}</span>
                    </div>
                    <div className="cp-form-grid">
                      {field('Type de numéro', 'phone_type', {
                        choices: {unknown: 'À identifier', direct: 'Ligne directe', switchboard: 'Standard'},
                      })}
                      {field('Qualification du téléphone', 'phone_status', {
                        choices: {
                          unknown: 'À vérifier',
                          appropriate: 'Approprié à la fonction',
                          invalid: 'Incorrect',
                        },
                      })}
                    </div>
                    {field('Provenance et contrôles effectués', 'sourcing_note', {
                      textarea: true,
                      required: true,
                      hint: 'Précisez la source et la date de vérification.',
                    })}
                  </>
                )}
                {editor.kind === 'language' && (
                  <>
                    {field('Langue professionnelle', 'language', {
                      choices: {'': 'À déterminer', ...languages},
                    })}
                    {field('Observation qui confirme cette langue', 'note', {
                      textarea: true,
                      required: true,
                      hint: 'Le pays du contact ne suffit pas à établir sa langue.',
                    })}
                    {field('Lien vers la source', 'source_url', {
                      type: 'url',
                      max: 1000,
                      placeholder: 'https://…',
                    })}
                  </>
                )}
                {editor.kind === 'campaign' && (
                  <>
                    {field('Sous-campagne et version', 'campaign_id', {
                      choices: {
                        '': 'Sans campagne',
                        ...Object.fromEntries(
                          data.campaigns.map((row: Row) => [
                            row.id,
                            row.name + ' · v' + (row.config?.sequence_version || 1),
                          ]),
                        ),
                      },
                    })}
                    <p>Changer de campagne demandera de relire et valider les messages correspondants.</p>
                  </>
                )}
                {editor.kind === 'sender' && (
                  <>
                    {field('Boîte expéditrice', 'email_sender_id', {
                      choices: {
                        '': 'Attribution automatique parmi les boîtes prêtes',
                        ...Object.fromEntries(
                          data.senders.map((row: Row) => [
                            row.id,
                            row.from_email + (row.paused ? ' · en pause' : ''),
                          ]),
                        ),
                      },
                    })}
                    <p>
                      Après la première tentative, l’expéditeur reste fixe. Avant envoi, un changement remet
                      les messages à valider.
                    </p>
                  </>
                )}
                {editor.kind === 'exchange' && (
                  <>
                    <div className="cp-form-grid">
                      {field('Type d’échange', 'kind', {
                        choices: {
                          note: 'Note',
                          reply: 'Réponse reçue',
                          positive_reply: 'Réponse positive',
                          out_of_office: 'Absence',
                          meeting: 'Rendez-vous confirmé',
                          stop: 'Ne plus contacter',
                          bounce: 'Email en erreur',
                        },
                      })}
                      {field('Canal', 'channel', {
                        choices: {email: 'Email', phone: 'Téléphone', linkedin: 'LinkedIn'},
                      })}
                    </div>
                    {field('Compte rendu', 'summary', {
                      textarea: true,
                      required: true,
                      max: 10000,
                      placeholder: 'Ce qui a été dit, les points utiles et la prochaine étape…',
                    })}
                    {editor.values.kind === 'stop' && (
                      <p className="cp-inline-note cp-note-warning">
                        L’opposition arrêtera les prochaines prises de contact sur tous les canaux.
                      </p>
                    )}
                  </>
                )}
                <footer className="cp-form-footer">
                  <span>
                    {JSON.stringify(editor.values) !== JSON.stringify(editor.original)
                      ? 'Modifications non enregistrées'
                      : 'Aucune modification'}
                  </span>
                  <div>
                    <button type="button" disabled={busy} onClick={cancelEdit}>
                      Annuler
                    </button>
                    <button
                      className="primary"
                      disabled={busy || JSON.stringify(editor.values) === JSON.stringify(editor.original)}
                    >
                      {busy
                        ? 'Enregistrement…'
                        : editor.kind === 'exchange'
                          ? 'Ajouter à l’historique'
                          : 'Enregistrer'}
                      <Check size={15} />
                    </button>
                  </div>
                </footer>
              </form>
            </section>
          ) : (
            <>
              {tab === 'overview' && (
                <>
                  <CallWorkspace
                    defaultDestination={contact.phone || ''}
                    blockedReason={callBlockedReason(contact)}
                    api={api}
                    upload={upload}
                    accountId={contact.account_id}
                    contactId={id}
                    campaignId={contact.campaign_id}
                    onChanged={() => {
                      void refresh();
                      void onRefresh();
                    }}
                  />
                  <section className={'cp-next-step ' + (hasHold ? 'cp-next-held' : '')}>
                    <div className="cp-next-label">
                      <span className="cp-overline">Prochaine étape</span>
                      <span>
                        {hasHold
                          ? 'Prospection suspendue'
                          : contact.approved
                            ? 'Contact validé'
                            : 'Avant toute prise de contact'}
                      </span>
                    </div>
                    <h2>
                      {hasHold
                        ? 'Revoir les conditions de reprise'
                        : tasks.length
                          ? tasks[0].title
                          : !contact.approved
                            ? 'Finaliser la qualification'
                            : 'Préparer la prochaine conversation'}
                    </h2>
                    <p>
                      {hasHold
                        ? account.pause_reason ||
                          (account.lifecycle === 'excluded'
                            ? 'Cette entreprise est exclue de la prospection.'
                            : 'Le contact ou son entreprise est en pause.')
                        : tasks.length
                          ? (names[tasks[0].channel] || tasks[0].channel) +
                            ' · ' +
                            formatDate(tasks[0].due_at)
                          : !contact.approved
                            ? 'Vérifiez les informations manquantes avant de valider cette fiche.'
                            : 'Préparez un message ou planifiez une action adaptée à ce contact.'}
                    </p>
                    <button
                      onClick={() =>
                        tasks.length && !hasHold
                          ? onOpen('task', tasks[0])
                          : navigate(contactUrl(id, 'qualification'))
                      }
                    >
                      {tasks.length && !hasHold ? 'Traiter cette action' : 'Revoir la qualification'}
                      <ArrowUpRight size={16} />
                    </button>
                  </section>
                  <Section title="Contexte commercial" action={modification('qualification')}>
                    <div className="cp-section-body">
                      <p className="cp-context-text">
                        {contact.professional_relevance ||
                          'La pertinence de l’offre pour ce contact reste à documenter.'}
                      </p>
                      <div className="cp-context-footnote">
                        <CircleHelp size={15} />
                        <span>Besoin commercial : {contact.fit?.pain || 'à confirmer en conversation'}</span>
                      </div>
                    </div>
                  </Section>
                  <Section
                    title="Derniers échanges"
                    description="Les conversations et notes de l’équipe."
                    action={
                      <button className="cp-text-button" disabled={locked} onClick={() => begin('exchange')}>
                        <Plus size={16} />
                        Ajouter
                      </button>
                    }
                  >
                    {exchanges.length ? (
                      <>
                        <Timeline events={exchanges.slice(0, 3)} compact />
                        {exchanges.length > 3 && (
                          <button
                            className="cp-section-link"
                            onClick={() => navigate(contactUrl(id, 'history'))}
                          >
                            Voir tout l’historique
                            <ChevronRight size={15} />
                          </button>
                        )}
                      </>
                    ) : (
                      <div className="cp-empty">
                        <MessageSquare size={23} />
                        <div>
                          <h3>Aucun échange enregistré</h3>
                          <p>Ajoutez une conversation ou une note utile pour la suite.</p>
                        </div>
                      </div>
                    )}
                  </Section>
                  {tasks.length > 0 && (
                    <Section
                      title="Actions planifiées"
                      action={
                        <button className="cp-text-button" onClick={() => onOpen('newTask', contact)}>
                          Planifier
                          <Plus size={15} />
                        </button>
                      }
                    >
                      {tasks.slice(0, 4).map((task) => (
                        <div className="cp-task" key={task.id}>
                          <CalendarDays size={17} />
                          <div>
                            <strong>{task.title}</strong>
                            <small>
                              {names[task.channel] || task.channel} · {formatDate(task.due_at)}
                            </small>
                          </div>
                          <button onClick={() => onOpen('task', task)}>Traiter</button>
                        </div>
                      ))}
                    </Section>
                  )}
                </>
              )}
              {tab === 'qualification' && (
                <>
                  <Section
                    title="Adéquation à la cible"
                    description={campaign ? campaign.name : 'Aucune sous-campagne affectée.'}
                    action={
                      <Tag value={contact.approved ? 'approved' : 'candidate'}>
                        {contact.approved ? 'Contact validé' : 'À compléter'}
                      </Tag>
                    }
                  >
                    <div className="cp-fit-list">
                      {checks.map((check) => (
                        <div key={check.criterion}>
                          {check.status === 'match' ? (
                            <CheckCircle2 size={18} className="cp-fit-match" />
                          ) : check.status === 'mismatch' ? (
                            <XCircle size={18} className="cp-fit-mismatch" />
                          ) : (
                            <CircleHelp size={18} />
                          )}
                          <span>{check.criterion}</span>
                          <strong>{names[check.evidence] || check.evidence}</strong>
                        </div>
                      ))}
                    </div>
                    <div className="cp-fit-footer">
                      <span>
                        {checks.filter((check) => check.status === 'match').length} critère(s) confirmé(s) sur{' '}
                        {checks.length}
                      </span>
                      <button className="cp-text-button" onClick={() => onOpen('account', account)}>
                        Revoir l’entreprise
                        <ArrowUpRight size={14} />
                      </button>
                    </div>
                  </Section>
                  <Section title="Pertinence professionnelle" action={modification('qualification')}>
                    <div className="cp-section-body">
                      <p className="cp-context-text">{contact.professional_relevance || 'À documenter.'}</p>
                      <dl className="cp-read-list">
                        <div>
                          <dt>Information sur les données</dt>
                          <dd>{names[contact.information_status] || contact.information_status}</dd>
                        </div>
                      </dl>
                    </div>
                  </Section>
                  <Section
                    title="Vérification des coordonnées"
                    action={modification('verification', 'Mettre à jour')}
                  >
                    <dl className="cp-read-list cp-section-body">
                      <div>
                        <dt>Email</dt>
                        <dd>
                          <Tag value={contact.email_status} />
                          {contact.email_verified_at && (
                            <small>Vérifié le {formatDate(contact.email_verified_at, true)}</small>
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>Téléphone</dt>
                        <dd>
                          {contact.phone ? (
                            <>
                              {contact.phone_type === 'unknown'
                                ? 'Type à identifier'
                                : names[contact.phone_type]}
                              <small>{names[contact.phone_status]}</small>
                            </>
                          ) : (
                            'Non renseigné'
                          )}
                        </dd>
                      </div>
                    </dl>
                  </Section>
                  <Section title="Langue professionnelle" action={modification('language', 'Vérifier')}>
                    <div className="cp-section-body">
                      <strong className="cp-language-value">
                        <Globe2 size={17} />
                        {languages[contact.communication_language?.language] || 'À déterminer'}
                      </strong>
                      <p>
                        {contact.communication_language?.note ||
                          'À confirmer à partir d’un échange ou d’une source professionnelle.'}
                      </p>
                      {contact.communication_language?.source_url && (
                        <a
                          className="cp-source-link"
                          href={contact.communication_language.source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Consulter la source
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>
                  </Section>
                  <Section
                    title="Validation du contact"
                    description="La validation conserve les autres conditions d’envoi."
                  >
                    <div className="cp-section-body">
                      {emailBlockers.length > 0 && (
                        <details className="cp-disclosure">
                          <summary>
                            {emailBlockers.length} condition(s) à résoudre pour l’email
                            <ChevronDown size={16} />
                          </summary>
                          <ul>
                            {emailBlockers.map((reason) => (
                              <li key={reason}>{reason}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                      {admin && (
                        <button
                          className="primary"
                          disabled={busy || contact.approved || !mayApprove}
                          onClick={() =>
                            run(() => api('/batches/approve', 'POST', {contact_ids: [id]}), 'Contact validé.')
                          }
                        >
                          <ShieldCheck size={16} />
                          {contact.approved ? 'Contact déjà validé' : 'Valider le contact'}
                        </button>
                      )}
                      {!mayApprove && !contact.approved && (
                        <p className="cp-help">Complétez les conditions du canal prévu avant de valider.</p>
                      )}
                    </div>
                  </Section>
                </>
              )}
              {tab === 'emails' && (
                <>
                  <Section
                    title="Séquence email"
                    description={campaign ? campaign.name : 'Affectez d’abord une sous-campagne.'}
                    action={
                      <button
                        disabled={busy || !campaign || hasHold}
                        onClick={() =>
                          run(
                            () => api('/batches/prepare', 'POST', {contact_ids: [id]}),
                            (result) =>
                              result.created
                                ? `${result.created} élément(s) préparé(s).`
                                : 'Aucun nouvel élément préparé. Les éléments existent déjà ou la séquence reste à compléter.',
                          )
                        }
                      >
                        <Plus size={15} />
                        Préparer
                      </button>
                    }
                  >
                    {data.state.paused && (
                      <p className="cp-inline-note cp-section-note">
                        Les envois de l’espace sont en pause. Les brouillons restent disponibles pour
                        relecture.
                      </p>
                    )}
                    {readyMessages.length ? (
                      readyMessages.map(showMessage)
                    ) : (
                      <div className="cp-empty">
                        <Mail size={23} />
                        <div>
                          <h3>Aucun email à relire</h3>
                          <p>
                            {archivedMessages.length
                              ? 'Les anciens messages sont conservés dans l’historique ci-dessous.'
                              : 'Préparez la séquence après avoir vérifié le contact et la rédaction.'}
                          </p>
                        </div>
                      </div>
                    )}
                  </Section>
                  {archivedMessages.length > 0 && (
                    <Section
                      title="Historique des emails"
                      description="Messages envoyés, annulés ou issus d’une simulation."
                    >
                      {archivedMessages.map(showMessage)}
                    </Section>
                  )}
                  <Section
                    title="Boîte expéditrice"
                    action={admin ? modification('sender', 'Changer') : undefined}
                  >
                    <div className="cp-section-body">
                      <strong>
                        {data.senders.find((row: Row) => row.id === contact.email_sender_id)?.from_email ||
                          'Attribution automatique'}
                      </strong>
                      <p>L’expéditeur est conservé après la première tentative.</p>
                    </div>
                  </Section>
                </>
              )}
              {tab === 'history' && (
                <>
                  <Section
                    title="Historique du contact"
                    action={
                      <button className="cp-text-button" onClick={() => begin('exchange')}>
                        <Plus size={16} />
                        Ajouter un échange
                      </button>
                    }
                  >
                    <div className="cp-history-filters" role="group" aria-label="Filtrer l’historique">
                      {[
                        ['conversations', 'Échanges & notes'],
                        ['all', 'Toute l’activité'],
                      ].map(([key, label]) => (
                        <button
                          key={key}
                          aria-pressed={historyFilter === key}
                          onClick={() => {
                            setHistoryFilter(key);
                            setEventLimit(10);
                          }}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    {(historyFilter === 'all' ? events : exchanges).length ? (
                      <>
                        <Timeline
                          events={(historyFilter === 'all' ? events : exchanges).slice(0, eventLimit)}
                        />
                        {(historyFilter === 'all' ? events : exchanges).length > eventLimit && (
                          <button
                            className="cp-section-link"
                            onClick={() => setEventLimit((value) => value + 10)}
                          >
                            Afficher les événements suivants
                            <ChevronDown size={15} />
                          </button>
                        )}
                      </>
                    ) : (
                      <div className="cp-empty">
                        <Clock3 size={23} />
                        <div>
                          <h3>Aucun échange enregistré</h3>
                          <p>Les mises à jour automatiques restent accessibles dans « Toute l’activité ».</p>
                        </div>
                      </div>
                    )}
                  </Section>
                  <Section
                    title="Sources & preuves"
                    description="Les observations conservées avec leur provenance."
                    action={
                      <button className="cp-text-button" onClick={() => onOpen('evidence', contact)}>
                        <Plus size={16} />
                        Ajouter une source
                      </button>
                    }
                  >
                    {contact.sourcing_note && (
                      <details className="cp-disclosure cp-source-notes">
                        <summary>
                          Notes de recherche et vérification
                          <ChevronDown size={16} />
                        </summary>
                        <p>{contact.sourcing_note}</p>
                      </details>
                    )}
                    {(contact.evidence || []).map((evidence: Row) => (
                      <details className="cp-evidence" key={evidence.id}>
                        <summary>
                          <span>
                            <strong>{evidenceNames[evidence.field] || evidence.field}</strong>
                            <small>
                              {evidence.source_type === 'conversation'
                                ? 'Conversation'
                                : names[evidence.source_type] || evidence.source_type}{' '}
                              · {formatDate(evidence.observed_at, true)}
                            </small>
                          </span>
                          <Tag value={evidence.confidence} />
                          <ChevronDown size={15} />
                        </summary>
                        <p>{names[evidence.value] || evidence.value}</p>
                        {evidence.source_url && (
                          <a
                            className="cp-source-link"
                            href={evidence.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Voir la source
                            <ExternalLink size={13} />
                          </a>
                        )}
                      </details>
                    ))}
                    {!contact.evidence?.length && !contact.sourcing_note && (
                      <div className="cp-empty">
                        <FileText size={23} />
                        <div>
                          <h3>Aucune source documentée</h3>
                          <p>Ajoutez l’observation et le lien qui permettent de la vérifier.</p>
                        </div>
                      </div>
                    )}
                  </Section>
                </>
              )}
            </>
          )}
        </div>
        <div className="cp-side-column">
          <Section title="Coordonnées" action={modification('profile', '')} className="cp-coordinate-section">
            <div className="cp-coordinate">
              <Mail size={16} />
              <div>
                <small>Email professionnel</small>
                <strong>{contact.email || 'Non renseigné'}</strong>
                {contact.email && <Tag value={contact.email_status} />}
              </div>
              {contact.email && <CopyValue value={contact.email} label="Email" />}
            </div>
            <div className="cp-coordinate">
              <Phone size={16} />
              <div>
                <small>Téléphone</small>
                <strong>{contact.phone || 'Non renseigné'}</strong>
                {contact.phone && (
                  <small>
                    {contact.phone_type === 'unknown' ? 'Type à identifier' : names[contact.phone_type]} ·{' '}
                    {names[contact.phone_status]}
                  </small>
                )}
              </div>
              {contact.phone && <CopyValue value={contact.phone} label="Téléphone" />}
            </div>
            {contact.linkedin_url && (
              <a className="cp-profile-link" href={contact.linkedin_url} target="_blank" rel="noreferrer">
                <Linkedin size={15} />
                Profil LinkedIn
                <ExternalLink size={13} />
              </a>
            )}
            <div className="cp-language-line">
              <Globe2 size={15} />
              <span>{languages[contact.communication_language?.language] || 'Langue à déterminer'}</span>
            </div>
          </Section>
          <Section
            title="Sous-campagne"
            action={modification('campaign', '')}
            className="cp-campaign-section"
          >
            <div className="cp-side-body">
              {campaign ? (
                <>
                  <span className="cp-overline">{offer?.name || 'Offre'}</span>
                  <strong>{campaign.name}</strong>
                  <p>
                    {campaign.niche || 'Niche à préciser'} · Version {campaign.config?.sequence_version || 1}
                  </p>
                  <Tag value={campaign.status} />
                </>
              ) : (
                <>
                  <strong>Pas encore affecté</strong>
                  <p>Choisissez une sous-campagne adaptée à ce contact.</p>
                  <button className="cp-text-button" disabled={locked} onClick={() => begin('campaign')}>
                    Affecter une campagne
                    <Plus size={14} />
                  </button>
                </>
              )}
            </div>
          </Section>
          <div className="cp-company-summary">
            <Building2 size={17} />
            <div>
              <strong>{account.name}</strong>
              <span>
                {markets[account.country] || account.country}
                {account.employee_count != null ? ' · ' + account.employee_count + ' personnes' : ''}
              </span>
              {account.domain && (
                <a href={'https://' + account.domain} target="_blank" rel="noreferrer">
                  {account.domain}
                  <ExternalLink size={12} />
                </a>
              )}
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}
