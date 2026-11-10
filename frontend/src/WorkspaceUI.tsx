import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
  type MouseEvent,
} from 'react';
import {ArrowLeft, ArrowUpRight, ExternalLink, MessageSquare, FileText, Clock3} from 'lucide-react';
import {errorText, type Api, type Row} from './api';
import './workspace-pages.css';
export type {Api, Row};
export type Navigate = (url: string) => void;
export const money = (cents: number) =>
  new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(
    (cents || 0) / 100,
  );
export const when = (value?: string) =>
  value ? new Date(value).toLocaleString('fr-FR', {dateStyle: 'medium', timeStyle: 'short'}) : '—';
export const personName = (row?: Row | null) =>
  row
    ? [row.first_name, row.last_name].filter(Boolean).join(' ') || row.email || 'Contact sans nom'
    : 'Contact effacé';
const evidenceNames: Row = {
  phone: 'Téléphone',
  phone_company: 'Téléphone de l’entreprise',
  phone_verification: 'Vérification du téléphone',
  email: 'Email',
  email_verification: 'Vérification de l’email',
  identity: 'Identité',
  fonction: 'Fonction',
  role: 'Fonction',
  industry: 'Activité',
  employee_count: 'Effectif',
  hubspot_status: 'Utilisation de HubSpot',
};
export const eventNames: Row = {
  autopilot_configured: 'Recherche automatique configurée',
  campaign_created: 'Sous-campagne créée',
  campaign_activated: 'Sous-campagne activée',
  campaign_paused: 'Sous-campagne suspendue',
  budget_approved: 'Budget validé',
  sourcing_configured: 'Recherche configurée',
  account_created: 'Entreprise ajoutée',
  reply: 'Réponse reçue',
  positive_reply: 'Intérêt confirmé',
  negative_reply: 'Pas de besoin',
  out_of_office: 'Absence',
  bounce: 'Email en erreur',
  stop: 'Opposition',
  note: 'Note',
  account_reviewed: 'Entreprise examinée',
  contact_reviewed: 'Contact examiné',
  contact_approved: 'Qualification validée',
  account_updated: 'Entreprise modifiée',
  account_evidence_added: 'Source entreprise ajoutée',
  evidence_added: 'Source ajoutée',
  inbox_reviewed: 'Réponse traitée',
  opportunity_created: 'Opportunité créée',
  opportunity_updated: 'Opportunité modifiée',
  task_completed: 'Action terminée',
  contact_updated: 'Contact modifié',
  contact_created: 'Contact créé',
  sequence_edited: 'Séquence enregistrée',
  prepared: 'Séquence préparée',
  language_reviewed: 'Langue vérifiée',
};
export function follow(event: MouseEvent<HTMLAnchorElement>, url: string, navigate: Navigate) {
  if (event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
  event.preventDefault();
  navigate(url);
}
export function RouteLink({
  url,
  navigate,
  children,
  className,
  label,
  current,
}: {
  url: string;
  navigate: Navigate;
  children: ReactNode;
  className?: string;
  label?: string;
  current?: boolean;
}) {
  return (
    <a
      href={url}
      className={className}
      aria-label={label}
      aria-current={current ? 'page' : undefined}
      onClick={(event) => follow(event, url, navigate)}
    >
      {children}
    </a>
  );
}
export function WorkspaceHeader({
  back,
  backLabel,
  title,
  subtitle,
  eyebrow,
  navigate,
  actions,
}: {
  back: string;
  backLabel: string;
  title: string;
  subtitle?: ReactNode;
  eyebrow: string;
  navigate: Navigate;
  actions?: ReactNode;
}) {
  return (
    <>
      <RouteLink url={back} navigate={navigate} className="ws-back">
        <ArrowLeft size={16} />
        {backLabel}
      </RouteLink>
      <header className="ws-heading">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </header>
    </>
  );
}
export function WorkspaceTabs({
  items,
  current,
  navigate,
  label,
}: {
  items: [string, string, string][];
  current: string;
  navigate: Navigate;
  label: string;
}) {
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = nav.current,
      active = element?.querySelector<HTMLElement>('[aria-current="page"]');
    if (element && active) {
      const parent = element.getBoundingClientRect(),
        child = active.getBoundingClientRect();
      if (child.right > parent.right) element.scrollLeft += child.right - parent.right + 12;
      if (child.left < parent.left) element.scrollLeft -= parent.left - child.left + 12;
    }
  }, [current]);
  return (
    <nav ref={nav} className="ws-tabs" aria-label={label}>
      {items.map(([id, text, url]) => (
        <RouteLink
          key={id}
          url={url}
          navigate={navigate}
          current={current === id}
          className={current === id ? 'active' : ''}
        >
          {text}
        </RouteLink>
      ))}
    </nav>
  );
}
export function Panel({
  title,
  description,
  action,
  children,
  className = '',
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={'ws-panel ' + className}>
      <header>
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </header>
      <div className="ws-panel-body">{children}</div>
    </section>
  );
}
export function Field({label, children, hint}: {label: string; children: ReactNode; hint?: string}) {
  const id = useId();
  return (
    <div className="ws-field">
      <label htmlFor={id}>{label}</label>
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
export function Feedback({error, notice}: {error?: string; notice?: string}) {
  return (
    <>
      {error && (
        <p role="alert" className="ws-feedback error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="ws-feedback success">
          {notice}
        </p>
      )}
    </>
  );
}
export function Blank({title, children}: {title: string; children?: ReactNode}) {
  return (
    <div className="ws-blank">
      <FileText size={23} />
      <p className="ws-blank-title">{title}</p>
      {children && <p>{children}</p>}
    </div>
  );
}
export function useRecord(path: string, api: Api, revision?: unknown) {
  const [record, setRecord] = useState<Row | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(true),
    [attempt, setAttempt] = useState(0);
  const previous = useRef('');
  useEffect(() => {
    let alive = true;
    if (previous.current !== path) {
      setRecord(null);
      setLoading(true);
      previous.current = path;
    }
    setError('');
    api(path)
      .then((value) => {
        if (alive) {
          setRecord(value);
          setLoading(false);
        }
      })
      .catch((error) => {
        if (alive) {
          setError(errorText(error));
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [api, path, revision, attempt]);
  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  return {record, error, loading, refresh};
}
export function Sources({rows, limit}: {rows: Row[]; limit?: number}) {
  return rows.length ? (
    <ul className="ws-sources">
      {rows.slice(0, limit).map((row) => (
        <li key={row.id}>
          <div>
            <strong>{evidenceNames[row.field.toLowerCase()] || row.field}</strong>
            <span className={'ws-proof ' + row.confidence}>
              {({confirmed: 'Confirmé', signal: 'Indice', unverified: 'À vérifier'} as Row)[row.confidence] ||
                row.confidence}
            </span>
          </div>
          <p>{row.value}</p>
          <small>
            {when(row.observed_at)} · {row.contact_id ? 'Source liée au contact' : 'Source entreprise'}
          </small>
          {/^https?:\/\//i.test(row.source_url) && (
            <a href={row.source_url} target="_blank" rel="noreferrer">
              Consulter la source
              <ExternalLink size={12} />
            </a>
          )}
        </li>
      ))}
    </ul>
  ) : (
    <Blank title="Aucune source documentée">
      Ajoutez un fait et sa provenance pour étayer la qualification.
    </Blank>
  );
}
export function Events({rows}: {rows: Row[]}) {
  return rows.length ? (
    <ol className="ws-events">
      {rows.map((row) => (
        <li key={row.id}>
          <span className="ws-event-icon">
            {row.kind.includes('reply') ? (
              <MessageSquare size={15} />
            ) : row.kind === 'note' ? (
              <FileText size={15} />
            ) : (
              <Clock3 size={15} />
            )}
          </span>
          <div>
            <div className="ws-event-title">
              <strong>{eventNames[row.kind] || row.kind.replaceAll('_', ' ')}</strong>
              <time dateTime={row.created_at}>{when(row.created_at)}</time>
            </div>
            <p>
              {row.summary ||
                (row.kind === 'autopilot_configured' && typeof row.data?.enabled === 'boolean'
                  ? row.data.enabled
                    ? 'Recherche automatique activée.'
                    : 'Recherche automatique suspendue.'
                  : 'Événement enregistré.')}
            </p>
            {row.data?.decision && (
              <small>
                {row.data.decision === 'qualified' ? 'Retenue après revue' : 'Écartée après revue'}
              </small>
            )}
          </div>
        </li>
      ))}
    </ol>
  ) : (
    <Blank title="Aucun événement pour le moment" />
  );
}
export function MoreLink({children, onClick}: {children: ReactNode; onClick: () => void}) {
  return (
    <button className="ws-text-button" onClick={onClick}>
      {children}
      <ArrowUpRight size={14} />
    </button>
  );
}
