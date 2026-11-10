import {useEffect, useState, type MouseEvent, type ReactNode} from 'react';
import {ArrowUpRight, Building2, ExternalLink, Mail, Phone} from 'lucide-react';
import {
  ListSummary,
  ListTable,
  ListToolbar,
  SelectionBar,
  TableBadge,
  useListState,
  useServerList,
  type Api,
  type Column,
  type Filter,
  type Row,
} from './ListTable';
import {markets} from './Locales';
import {contactUrl} from './navigation';
import {errorText} from './api';

const labels: Row = {
  candidate: 'À qualifier',
  qualified: 'Qualifié',
  conversation: 'En conversation',
  suppressed: 'Opposition',
  paused: 'En pause',
  excluded: 'Entreprise exclue',
  customer: 'Client',
  prospect: 'À explorer',
  active: 'Active',
  draft: 'Brouillon',
  approved: 'Validé',
  queued: 'Planifié',
  sent: 'Envoyé',
  simulated: 'Simulé',
  pending: 'À faire',
  completed: 'Terminé',
  cancelled: 'Annulé',
  unknown: 'À vérifier',
  valid: 'Vérifié',
  invalid: 'Invalide',
  risky: 'Risqué',
  meeting: 'Rendez-vous',
  proposal: 'Proposition',
  won: 'Gagnée',
  lost: 'Perdue',
  phone: 'Appel',
  linkedin: 'LinkedIn',
  review: 'Suivi interne',
  email: 'Email',
  appropriate: 'Qualifié',
  direct: 'Direct',
  switchboard: 'Standard',
  confirmed: 'Confirmé',
  signal: 'Signal public',
  absent: 'Absent',
  failed: 'En erreur',
  uncertain: 'À rapprocher',
};
const money = (cents: number) =>
  new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(
    (cents || 0) / 100,
  );
const shortDate = (value?: string) =>
  value
    ? new Date(value).toLocaleDateString('fr-FR', {day: 'numeric', month: 'short', year: 'numeric'})
    : '—';
const time = (value?: string) =>
  value ? new Date(value).toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'}) : '';
const fullName = (row: Row) =>
  [row.first_name, row.last_name].filter(Boolean).join(' ') || row.email || 'Contact sans nom';
const campaignOptions = (data: Row) => ({
  none: 'Sans campagne',
  ...Object.fromEntries(
    data.campaigns.map((row: Row) => [row.id, row.name + ' · v' + (row.config?.sequence_version || 1)]),
  ),
});
const campaignCell = (row: Row) => (
  <div className="lt-cell-stack">
    {row.campaign ? (
      <>
        <strong>{row.campaign.name}</strong>
        <small>{row.campaign.niche || 'Niche à préciser'}</small>
      </>
    ) : (
      <span className="lt-muted">Non affecté</span>
    )}
  </div>
);
const countries = (result: Row | null) =>
  Object.fromEntries((result?.facets.countries || []).map((code: string) => [code, markets[code] || code]));
const badge = (value: string, label?: ReactNode) => (
  <TableBadge value={value}>{label || labels[value] || value}</TableBadge>
);
const companyCell = (row: Row) => (
  <div className="lt-cell-stack">
    <strong>{row.account?.name || 'Entreprise à renseigner'}</strong>
    <small>
      {markets[row.account?.country] || row.account?.country}
      {row.account?.employee_count != null ? ' · ' + row.account.employee_count + ' personnes' : ''}
    </small>
  </div>
);
const contactStatus = (row: Row) =>
  row.account?.lifecycle === 'excluded'
    ? 'excluded'
    : row.status === 'suppressed'
      ? 'suppressed'
      : row.paused || row.account?.outreach_paused || row.account?.lifecycle === 'customer'
        ? 'paused'
        : row.status;
export type OpenContact = (event: MouseEvent<HTMLAnchorElement>, id: string) => void;
type Props = {
  scope?: Record<string, string>;
  preferenceKey?: string;
  onCompany?: OpenContact;
  data: Row;
  api: Api;
  onOpen: (name: string, row?: Row) => void;
  onContact: OpenContact;
  busy?: boolean;
  admin?: boolean;
  onAction?: (fn: () => Promise<unknown>, success?: string) => Promise<void>;
};
function Person({row, onContact}: {row: Row | null; onContact: OpenContact}) {
  if (!row) return <span className="lt-muted">Aucun contact associé</span>;
  return (
    <div className="lt-record">
      <span className="lt-avatar" aria-hidden="true">
        {(row.first_name?.[0] || '') + (row.last_name?.[0] || '') || '—'}
      </span>
      <div>
        <a href={contactUrl(row.id)} onClick={(event) => onContact(event, row.id)}>
          {fullName(row)}
        </a>
        <small>{row.role || 'Fonction à renseigner'}</small>
      </div>
    </div>
  );
}
function ContactLink({row, onContact}: {row: Row; onContact: OpenContact}) {
  return (
    <a
      className="lt-row-link"
      href={contactUrl(row.id)}
      aria-label={'Ouvrir ' + fullName(row)}
      title="Ouvrir la fiche"
      onClick={(event) => onContact(event, row.id)}
    >
      <ArrowUpRight size={16} />
    </a>
  );
}
function Coordinate({row}: {row: Row}) {
  return (
    <div className="lt-cell-stack lt-coordinates">
      <div className="lt-cell-line">
        <Mail size={13} />
        {row.email ? (
          <>
            <span>{row.email}</span>
            <i
              className={'lt-verification-dot ' + row.email_status}
              title={'Email : ' + (labels[row.email_status] || row.email_status)}
              aria-label={'Email : ' + (labels[row.email_status] || row.email_status)}
            />
          </>
        ) : (
          <span className="lt-muted">Email manquant</span>
        )}
      </div>
      <div className="lt-cell-line">
        <Phone size={13} />
        {row.phone ? (
          <>
            <span>{row.phone}</span>
            <small>
              {row.phone_type === 'direct'
                ? 'Direct'
                : row.phone_type === 'switchboard'
                  ? 'Standard'
                  : 'Type à vérifier'}
            </small>
          </>
        ) : (
          <span className="lt-muted">Téléphone manquant</span>
        )}
      </div>
    </div>
  );
}
function PagePanel({children}: {children: ReactNode}) {
  return <section className="lt-panel">{children}</section>;
}

export function ContactsList({
  data,
  api,
  onOpen,
  onContact,
  busy,
  admin,
  onAction,
  scope = {},
  preferenceKey = 'prospects-v2',
}: Props) {
  const [state, change] = useListState(preferenceKey, {
    size: 10,
    sort: 'score',
    direction: 'desc',
    compact: true,
    filters: {focus: 'active'},
    hidden: ['qualification', 'campaign', 'sender', 'created', 'updated'],
  });
  const server = useServerList('contacts', state, change, api, data, scope);
  const result = server.result,
    rows: Row[] = result?.items || [],
    summary = result?.summary || {};
  const columns: Column[] = [
    {
      key: 'name',
      label: 'Prospect',
      fixed: true,
      sort: 'name',
      width: 175,
      render: (row) => <Person row={row} onContact={onContact} />,
    },
    {key: 'company', label: 'Entreprise', sort: 'company', width: 140, render: companyCell},
    {
      key: 'score',
      label: 'Score',
      sort: 'score',
      width: 70,
      render: (row) => (
        <span
          className={'prospect-score ' + (row.score >= 70 ? 'high' : '')}
          title="Priorité : adéquation à la cible /60, coordonnées /25, preuve récente /15. Ce score ne mesure pas l’intention d’achat."
        >
          {row.score}
          <small>/100</small>
        </span>
      ),
    },
    {
      key: 'coordinates',
      label: 'Coordonnées',
      sort: 'email',
      width: 190,
      render: (row) => <Coordinate row={row} />,
    },
    {key: 'campaign', label: 'Campagne', sort: 'campaign', width: 185, optional: true, render: campaignCell},
    {
      key: 'qualification',
      label: 'Qualification',
      width: 130,
      mobile: false,
      optional: true,
      render: (row) => (
        <div className="lt-cell-stack">
          <div className="lt-score">
            <strong>
              {row.fit?.matched || 0}/{row.fit?.total || 0}
            </strong>
            <span className="lt-score-track" aria-hidden="true">
              <span style={{width: (row.fit?.total ? (100 * row.fit.matched) / row.fit.total : 0) + '%'}} />
            </span>
          </div>
          <small>
            {row.approved
              ? 'Fiche validée'
              : row.fit?.total
                ? row.fit.unknown + ' à vérifier'
                : 'Cible à définir'}
          </small>
        </div>
      ),
    },
    {
      key: 'status',
      label: 'Statut',
      sort: 'status',
      width: 105,
      render: (row) => (
        <div className="lt-cell-stack">
          {badge(contactStatus(row))}
          <small>
            {row.fit?.matched || 0}/{row.fit?.total || 0} critères cibles
          </small>
        </div>
      ),
    },
    {
      key: 'activity',
      label: 'Derniers échanges',
      width: 155,
      render: (row) => (
        <div className="lt-cell-stack">
          <span>
            <Phone size={12} /> {row.last_call_at ? shortDate(row.last_call_at) : 'Aucun appel'}
          </span>
          <small>
            <Mail size={12} /> {row.last_email_at ? shortDate(row.last_email_at) : 'Aucun envoi'}
          </small>
        </div>
      ),
    },
    {
      key: 'next_action',
      label: 'Prochaine action',
      sort: 'next_action',
      width: 175,
      render: (row) => (
        <div className="lt-cell-stack">
          {row.next_task ? (
            <>
              <strong>{row.next_task.title}</strong>
              <small>
                {shortDate(row.next_task.due_at)} · {labels[row.next_task.channel]}
              </small>
            </>
          ) : (
            <span className="lt-muted">{row.qualification?.agent_review?.next_action || 'À définir'}</span>
          )}
        </div>
      ),
    },
    {
      key: 'sender',
      label: 'Boîte expéditrice',
      width: 205,
      optional: true,
      render: (row) => <span>{row.sender?.from_email || 'Automatique'}</span>,
    },
    {
      key: 'created',
      label: 'Ajouté le',
      sort: 'created',
      width: 130,
      optional: true,
      render: (row) => (
        <time className="lt-date" dateTime={row.created_at}>
          {shortDate(row.created_at)}
        </time>
      ),
    },
    {
      key: 'updated',
      label: 'Modifié le',
      sort: 'updated',
      width: 130,
      optional: true,
      render: (row) => (
        <time className="lt-date" dateTime={row.updated_at}>
          {shortDate(row.updated_at)}
        </time>
      ),
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => <ContactLink row={row} onContact={onContact} />,
    },
  ];
  const filters: Filter[] = [
    {
      key: 'status',
      label: 'Statut',
      options: {
        candidate: 'À qualifier',
        qualified: 'Qualifiés',
        conversation: 'En conversation',
        paused: 'Prospection suspendue',
        suppressed: 'Opposition',
      },
    },
    {key: 'campaign_id', label: 'Campagne', options: campaignOptions(data), extra: true},
    {
      key: 'email_status',
      label: 'Email',
      extra: true,
      options: {
        valid: 'Vérifié',
        unknown: 'À vérifier',
        risky: 'Risqué',
        invalid: 'Invalide',
        missing: 'Non renseigné',
      },
    },
    {
      key: 'phone',
      label: 'Téléphone',
      options: {direct: 'Ligne directe', present: 'Numéro renseigné', missing: 'Non renseigné'},
    },
    {key: 'country', label: 'Pays', options: countries(result), extra: true},
    {
      key: 'lifecycle',
      label: 'Entreprise',
      options: {prospect: 'À explorer', customer: 'Cliente', excluded: 'Exclue'},
      extra: true,
    },
    {
      key: 'qualification',
      label: 'Validation',
      options: {approved: 'Fiche validée', unapproved: 'À valider'},
      extra: true,
    },
  ];
  const hiddenSelected = state.selection.filter((id) => !rows.some((row) => row.id === id)).length;
  return (
    <div className="lt-page">
      <ListSummary
        label={Object.keys(scope).length ? 'Chiffres de cette sélection' : 'Chiffres de l’espace'}
        items={[
          [
            summary.total,
            Object.keys(scope).length ? 'contacts dans cette sélection' : 'contacts dans l’espace',
          ],
          [summary.email_verified, 'emails vérifiés'],
          [summary.direct_phones, 'lignes directes'],
          [summary.approved, 'fiches validées'],
        ]}
      />
      <PagePanel>
        <div className="prospect-focus" role="group" aria-label="Vue des prospects">
          {[
            ['active', 'Tous les prospects'],
            ['priority', 'Prioritaires'],
            ['call', 'Avec téléphone'],
            ['never_contacted', 'Jamais contactés'],
            ['incomplete', 'À compléter'],
          ].map(([id, label]) => (
            <button
              key={id}
              aria-pressed={state.filters.focus === id}
              onClick={() =>
                change({filters: {...state.filters, focus: state.filters.focus === id ? '' : id}})
              }
            >
              {label}
            </button>
          ))}
        </div>
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          placeholder="Nom, entreprise, email…"
          searchLabel="Rechercher un contact"
        />
        <SelectionBar count={state.selection.length} onClear={() => change({selection: []}, false)}>
          {admin && (
            <>
              <button
                disabled={busy || server.loading}
                onClick={() =>
                  onAction?.(
                    () => api('/batches/prepare', 'POST', {contact_ids: state.selection}),
                    'Brouillons préparés pour le lot.',
                  )
                }
              >
                Préparer le lot
              </button>
              <button
                disabled={busy || server.loading}
                onClick={() =>
                  onAction?.(
                    () => api('/batches/approve', 'POST', {contact_ids: state.selection}),
                    'Fiches du lot validées.',
                  )
                }
              >
                Valider les fiches
              </button>
              <button
                disabled={busy || server.loading}
                onClick={() => onOpen('enrich', {contact_ids: state.selection})}
              >
                Enrichir
              </button>
            </>
          )}
        </SelectionBar>
        {hiddenSelected > 0 && (
          <p className="lt-selection-help">
            {hiddenSelected} contact(s) sélectionné(s) sur d’autres pages. Les actions concernent les{' '}
            {state.selection.length} fiches sélectionnées.
          </p>
        )}
        <ListTable
          title="Prospects"
          rows={rows}
          total={result?.total || 0}
          overall={result?.total_unfiltered}
          state={state}
          change={change}
          columns={columns}
          loading={server.loading}
          error={server.error}
          onRetry={server.retry}
          selection={{
            ids: state.selection,
            onChange: (ids) => change({selection: ids}, false),
            label: fullName,
            disabled: busy,
            max: 100,
          }}
          emptyTitle={
            state.q || Object.values(state.filters).some(Boolean)
              ? 'Aucun contact ne correspond'
              : 'Votre premier contact commence ici'
          }
          emptyText="Ajoutez un contact, importez un fichier ou ajustez les filtres pour retrouver une fiche."
        />
      </PagePanel>
    </div>
  );
}

export function CompaniesList({data, api, onOpen, onContact, onCompany}: Props) {
  const [state, change] = useListState('companies', {hidden: ['evidence', 'updated']});
  const server = useServerList('accounts', state, change, api, data),
    result = server.result,
    summary = result?.summary || {};
  const [expanded, setExpanded] = useState(''),
    [detail, setDetail] = useState<Row | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    if (!expanded) {
      setDetail(null);
      return;
    }
    let live = true;
    setError('');
    setDetail(null);
    api('/accounts/' + expanded)
      .then((value) => {
        if (live) setDetail(value);
      })
      .catch((error) => {
        if (live) setError(errorText(error));
      });
    return () => {
      live = false;
    };
  }, [api, expanded, data]);
  const toggle = (row: Row) => setExpanded(expanded === row.id ? '' : row.id);
  const columns: Column[] = [
    {
      key: 'name',
      label: 'Entreprise',
      fixed: true,
      sort: 'name',
      width: 215,
      render: (row) => (
        <div className="lt-record">
          <span className="lt-avatar company" aria-hidden="true">
            <Building2 size={17} />
          </span>
          <div>
            <a
              className="lt-company-name"
              href={'/companies/' + row.id}
              onClick={(event) => {
                if (onCompany) onCompany(event, row.id);
                else {
                  event.preventDefault();
                  toggle(row);
                }
              }}
            >
              {row.name}
            </a>
            <small>{row.domain}</small>
          </div>
        </div>
      ),
    },
    {
      key: 'industry',
      label: 'Activité',
      sort: 'industry',
      width: 180,
      render: (row) => <span>{row.industry || <span className="lt-muted">À renseigner</span>}</span>,
    },
    {
      key: 'country',
      label: 'Pays',
      sort: 'country',
      width: 90,
      render: (row) => <span>{markets[row.country] || row.country || '—'}</span>,
    },
    {
      key: 'employees',
      label: 'Effectif',
      sort: 'employees',
      width: 85,
      render: (row) =>
        row.employee_count == null ? (
          <span className="lt-muted">Inconnu</span>
        ) : (
          <span>{row.employee_count.toLocaleString('fr-FR')}</span>
        ),
    },
    {
      key: 'company_phone',
      label: 'Téléphone de l’entreprise',
      width: 170,
      render: (row) =>
        row.company_phone ? (
          <div className="lt-cell-stack">
            <a href={'tel:' + row.company_phone}>{row.company_phone}</a>
            <small>Établissement · à vérifier</small>
          </div>
        ) : (
          <span className="lt-muted">À récupérer</span>
        ),
    },
    {
      key: 'crm',
      label: 'HubSpot',
      width: 110,
      render: (row) => badge(row.hubspot_status, row.hubspot_status === 'unknown' ? 'À vérifier' : undefined),
    },
    {
      key: 'contacts',
      label: 'Contacts',
      sort: 'contacts',
      width: 90,
      render: (row) => (
        <span>
          {row.contact_count} <span className="lt-muted">connu(s)</span>
        </span>
      ),
    },
    {
      key: 'status',
      label: 'Statut',
      sort: 'status',
      width: 105,
      render: (row) => badge(row.lifecycle, row.lifecycle === 'excluded' ? 'Exclue' : undefined),
    },
    {
      key: 'evidence',
      label: 'Sources',
      sort: 'evidence',
      width: 100,
      optional: true,
      render: (row) => <span>{row.evidence_count} preuve(s)</span>,
    },
    {
      key: 'updated',
      label: 'Modifiée le',
      sort: 'updated',
      width: 130,
      optional: true,
      render: (row) => (
        <time className="lt-date" dateTime={row.updated_at}>
          {shortDate(row.updated_at)}
        </time>
      ),
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => (
        <a
          className="lt-row-link"
          aria-label={'Ouvrir la fiche de ' + row.name}
          href={'/companies/' + row.id}
          onClick={(event) => {
            if (onCompany) onCompany(event, row.id);
            else {
              event.preventDefault();
              toggle(row);
            }
          }}
        >
          <ArrowUpRight size={16} />
        </a>
      ),
    },
  ];
  const filters: Filter[] = [
    {
      key: 'status',
      label: 'Statut',
      options: {
        prospect: 'À explorer',
        customer: 'Clientes',
        excluded: 'Exclues',
        paused: 'Prospection en pause',
      },
    },
    {key: 'country', label: 'Pays', options: countries(result)},
    {
      key: 'employees',
      label: 'Effectif',
      options: {
        unknown: 'Inconnu',
        '1-19': '1 à 19',
        '20-49': '20 à 49',
        '50-99': '50 à 99',
        '100-249': '100 à 249',
        '250+': '250 et plus',
      },
    },
    {key: 'has_contacts', label: 'Interlocuteurs', options: {yes: 'Avec contacts', no: 'Sans contact'}},
    {
      key: 'crm',
      label: 'HubSpot',
      options: {confirmed: 'Confirmé', signal: 'Signal public', unknown: 'À vérifier', absent: 'Absent'},
      extra: true,
    },
  ];
  function expandedContent(row: Row) {
    return (
      <div className="lt-company-detail">
        {error ? (
          <p role="alert">{error}</p>
        ) : !detail ? (
          <p role="status">Chargement de l’entreprise…</p>
        ) : (
          <>
            <header>
              <h3>{row.name}</h3>
              <div className="actions">
                <a className="lt-source-link" href={'https://' + row.domain} target="_blank" rel="noreferrer">
                  Site web
                  <ExternalLink size={13} />
                </a>
                <button onClick={() => onOpen('account', row)}>Modifier l’entreprise</button>
              </div>
            </header>
            {row.outreach_paused && (
              <p className="callout">
                Prospection suspendue · {row.pause_reason || 'Revue nécessaire avant reprise.'}
              </p>
            )}
            <p>{row.notes || 'Aucune note complémentaire.'}</p>
            <details>
              <summary>Sources & preuves · {detail.evidence.length}</summary>
              {detail.evidence.map((entry: Row) => (
                <div className="company-evidence" key={entry.id}>
                  <strong>{entry.field}</strong>
                  <p>{entry.value}</p>
                  <small>
                    {labels[entry.confidence] || 'À vérifier'} · {shortDate(entry.observed_at)}
                  </small>
                  {/^https?:\/\//i.test(entry.source_url) && (
                    <a href={entry.source_url} target="_blank" rel="noreferrer">
                      Consulter la source
                      <ExternalLink size={13} />
                    </a>
                  )}
                </div>
              ))}
              {!detail.evidence.length && <p>Aucune preuve enregistrée.</p>}
            </details>
            <details>
              <summary>Interlocuteurs connus · {detail.contacts.length}</summary>
              {detail.contacts.length ? (
                <ul>
                  {detail.contacts.map((contact: Row) => (
                    <li key={contact.id}>
                      <a href={contactUrl(contact.id)} onClick={(event) => onContact(event, contact.id)}>
                        {fullName(contact)}
                      </a>{' '}
                      · {contact.role || 'Fonction à préciser'}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>Aucun interlocuteur enregistré.</p>
              )}
            </details>
          </>
        )}
      </div>
    );
  }
  return (
    <div className="lt-page">
      <ListSummary
        items={[
          [summary.total, 'entreprises dans l’espace'],
          [summary.prospects, 'entreprises à explorer'],
          [summary.with_contacts, 'avec un interlocuteur'],
          [summary.excluded, 'entreprises exclues'],
        ]}
      />
      <PagePanel>
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          placeholder="Entreprise, domaine ou activité…"
          searchLabel="Rechercher une entreprise"
        />
        <ListTable
          title="Entreprises"
          rows={result?.items || []}
          total={result?.total || 0}
          overall={result?.total_unfiltered}
          state={state}
          change={change}
          columns={columns}
          loading={server.loading}
          error={server.error}
          onRetry={server.retry}
          expandedId={expanded}
          renderExpanded={expandedContent}
          emptyTitle="Aucune entreprise ne correspond"
          emptyText="Ajustez la recherche ou les filtres. Les entreprises sont visibles même sans interlocuteur."
        />
      </PagePanel>
    </div>
  );
}

export function TasksList({data, api, onOpen, onContact}: Props) {
  const [state, change] = useListState('tasks', {
    sort: 'due',
    filters: {status: 'pending'},
    hidden: ['campaign', 'created'],
  });
  const server = useServerList('tasks', state, change, api, data),
    result = server.result,
    summary = result?.summary || {};
  const columns: Column[] = [
    {
      key: 'title',
      label: 'Action',
      sort: 'title',
      fixed: true,
      width: 275,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.title}</strong>
          <small>{row.notes ? row.notes.slice(0, 100) : 'Aucune consigne complémentaire'}</small>
        </div>
      ),
    },
    {
      key: 'contact',
      label: 'Contact',
      width: 220,
      render: (row) => <Person row={row.contact} onContact={onContact} />,
    },
    {key: 'company', label: 'Entreprise', sort: 'company', width: 155, render: companyCell},
    {key: 'channel', label: 'Canal', sort: 'channel', width: 120, render: (row) => badge(row.channel)},
    {
      key: 'due',
      label: 'Échéance',
      sort: 'due',
      width: 150,
      render: (row) => (
        <div className="lt-cell-stack">
          <time className="lt-date" dateTime={row.due_at}>
            {shortDate(row.due_at)}
          </time>
          <small>
            {time(row.due_at)}
            {row.status === 'pending' && new Date(row.due_at) < new Date() && (
              <>
                {' '}
                · <TableBadge value="overdue">En retard</TableBadge>
              </>
            )}
          </small>
        </div>
      ),
    },
    {key: 'status', label: 'Statut', sort: 'status', width: 100, render: (row) => badge(row.status)},
    {key: 'campaign', label: 'Sous-campagne', width: 190, optional: true, render: campaignCell},
    {
      key: 'created',
      label: 'Créée le',
      sort: 'created',
      width: 130,
      optional: true,
      render: (row) => <span className="lt-date">{shortDate(row.created_at)}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) =>
        row.status === 'pending' ? (
          <button aria-label={'Traiter ' + row.title} onClick={() => onOpen('task', row)}>
            <ArrowUpRight size={16} />
          </button>
        ) : row.contact ? (
          <ContactLink row={row.contact} onContact={onContact} />
        ) : null,
    },
  ];
  const filters: Filter[] = [
    {
      key: 'status',
      label: 'Statut',
      options: {pending: 'À faire', completed: 'Terminées', cancelled: 'Annulées'},
    },
    {
      key: 'channel',
      label: 'Canal',
      options: {phone: 'Appel', review: 'Suivi interne', linkedin: 'LinkedIn', email: 'Email'},
    },
    {
      key: 'period',
      label: 'Échéance',
      options: {overdue: 'En retard', today: 'Aujourd’hui', week: '7 prochains jours', upcoming: 'À venir'},
    },
    {key: 'campaign_id', label: 'Sous-campagne', options: campaignOptions(data), extra: true},
  ];
  return (
    <div className="lt-page">
      <ListSummary
        items={[
          [summary.pending, 'actions à faire'],
          [summary.overdue, 'actions en retard'],
          [summary.completed, 'actions terminées'],
          [summary.total, 'actions au total'],
        ]}
      />
      <PagePanel>
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          placeholder="Action, contact ou entreprise…"
          searchLabel="Rechercher une action"
        />
        <ListTable
          title="Actions"
          rows={result?.items || []}
          total={result?.total || 0}
          overall={result?.total_unfiltered}
          state={state}
          change={change}
          columns={columns}
          loading={server.loading}
          error={server.error}
          onRetry={server.retry}
          emptyTitle="Aucune action dans cette vue"
          emptyText="Les actions planifiées depuis les contacts et les campagnes apparaissent ici."
        />
      </PagePanel>
    </div>
  );
}

export function MessagesList({data, api, onOpen, onContact}: Props) {
  const [state, change] = useListState('messages', {
    sort: 'created',
    direction: 'desc',
    hidden: ['campaign', 'created'],
  });
  const server = useServerList('messages', state, change, api, data),
    result = server.result,
    summary = result?.summary || {};
  const [previewError, setPreviewError] = useState(''),
    [opening, setOpening] = useState('');
  async function preview(row: Row) {
    setPreviewError('');
    setOpening(row.id);
    try {
      onOpen('preview', await api('/messages/' + row.id + '/preview'));
    } catch (error) {
      setPreviewError(errorText(error));
    } finally {
      setOpening('');
    }
  }
  const columns: Column[] = [
    {
      key: 'subject',
      label: 'Email',
      sort: 'subject',
      fixed: true,
      width: 310,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.subject || 'Sans objet'}</strong>
          <small>
            Étape {row.step + 1} · {row.campaign?.name || 'Campagne'}
          </small>
        </div>
      ),
    },
    {
      key: 'contact',
      label: 'Destinataire',
      width: 220,
      render: (row) => <Person row={row.contact} onContact={onContact} />,
    },
    {key: 'company', label: 'Entreprise', sort: 'company', width: 150, render: companyCell},
    {key: 'status', label: 'Statut', sort: 'status', width: 130, render: (row) => badge(row.status)},
    {
      key: 'due',
      label: 'Date prévue',
      sort: 'due',
      width: 145,
      render: (row) => (
        <div className="lt-cell-stack">
          <time className="lt-date" dateTime={row.due_at}>
            {shortDate(row.due_at)}
          </time>
          <small>{time(row.due_at)}</small>
        </div>
      ),
    },
    {
      key: 'sender',
      label: 'Expéditeur',
      width: 200,
      render: (row) => (
        <span>{row.sender_email || <span className="lt-muted">À attribuer avant envoi</span>}</span>
      ),
    },
    {
      key: 'campaign',
      label: 'Sous-campagne',
      sort: 'campaign',
      width: 205,
      optional: true,
      render: campaignCell,
    },
    {
      key: 'created',
      label: 'Créé le',
      sort: 'created',
      width: 130,
      optional: true,
      render: (row) => <span className="lt-date">{shortDate(row.created_at)}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => (
        <button
          disabled={!!opening}
          aria-label={'Voir l’email ' + (row.subject || 'Sans objet')}
          onClick={() => preview(row)}
        >
          <ArrowUpRight size={16} />
        </button>
      ),
    },
  ];
  const filters: Filter[] = [
    {
      key: 'status',
      label: 'Statut',
      options: {
        draft: 'Brouillons',
        approved: 'Validés',
        queued: 'Planifiés',
        sent: 'Envoyés',
        cancelled: 'Annulés',
        failed: 'En erreur',
        uncertain: 'À rapprocher',
        simulated: 'Simulés',
      },
    },
    {key: 'campaign_id', label: 'Sous-campagne', options: campaignOptions(data)},
    {
      key: 'period',
      label: 'Date prévue',
      options: {today: 'Aujourd’hui', week: '7 prochains jours', upcoming: 'À venir'},
      extra: true,
    },
  ];
  return (
    <div className="lt-page">
      <ListSummary
        items={[
          [summary.total, 'emails dans l’historique'],
          [summary.drafts, 'brouillons à relire'],
          [summary.sent, 'emails envoyés'],
          [summary.cancelled, 'emails annulés'],
        ]}
      />
      {previewError && (
        <p className="alert error" role="alert">
          {previewError}
        </p>
      )}
      <PagePanel>
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          placeholder="Objet, destinataire, entreprise…"
          searchLabel="Rechercher un email"
        />
        <ListTable
          title="Emails"
          rows={result?.items || []}
          total={result?.total || 0}
          overall={result?.total_unfiltered}
          state={state}
          change={change}
          columns={columns}
          loading={server.loading}
          error={server.error}
          onRetry={server.retry}
          emptyTitle="Aucun email dans cette vue"
          emptyText="Préparez les séquences depuis les contacts ou ajustez vos filtres."
        />
      </PagePanel>
    </div>
  );
}

export function OpportunitiesList({data, api, onOpen, onContact}: Props) {
  const [state, change] = useListState('opportunities', {
    sort: 'updated',
    direction: 'desc',
    hidden: ['campaign', 'updated'],
  });
  const server = useServerList('opportunities', state, change, api, data),
    result = server.result,
    summary = result?.summary || {};
  const columns: Column[] = [
    {
      key: 'title',
      label: 'Opportunité',
      sort: 'title',
      fixed: true,
      width: 245,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.title}</strong>
          <small>{row.account.name}</small>
        </div>
      ),
    },
    {
      key: 'contact',
      label: 'Interlocuteur',
      width: 210,
      render: (row) => <Person row={row.contact} onContact={onContact} />,
    },
    {key: 'status', label: 'Étape', sort: 'status', width: 145, render: (row) => badge(row.stage)},
    {
      key: 'amount',
      label: 'Montant',
      sort: 'amount',
      width: 115,
      render: (row) => <span className="lt-money">{money(row.amount_cents)}</span>,
    },
    {
      key: 'deposit',
      label: 'Acompte reçu',
      sort: 'deposit',
      width: 130,
      render: (row) => (
        <span className={row.deposit_cents ? 'lt-money' : 'lt-muted'}>
          {row.deposit_cents ? money(row.deposit_cents) : 'Non reçu'}
        </span>
      ),
    },
    {
      key: 'next_action',
      label: 'Prochaine action',
      sort: 'next_action',
      width: 235,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.next_action || 'À définir'}</strong>
          <small>
            {row.next_action_at
              ? shortDate(row.next_action_at) + ' · ' + time(row.next_action_at)
              : 'Sans échéance'}
          </small>
        </div>
      ),
    },
    {key: 'campaign', label: 'Sous-campagne', width: 205, optional: true, render: campaignCell},
    {
      key: 'updated',
      label: 'Modifiée le',
      sort: 'updated',
      width: 130,
      optional: true,
      render: (row) => <span className="lt-date">{shortDate(row.updated_at)}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => (
        <button aria-label={'Modifier ' + row.title} onClick={() => onOpen('editOpportunity', row)}>
          <ArrowUpRight size={16} />
        </button>
      ),
    },
  ];
  const filters: Filter[] = [
    {
      key: 'status',
      label: 'Étape',
      options: {
        conversation: 'En conversation',
        meeting: 'Rendez-vous',
        proposal: 'Proposition',
        won: 'Gagnées',
        lost: 'Perdues',
      },
    },
    {key: 'campaign_id', label: 'Sous-campagne', options: campaignOptions(data)},
    {
      key: 'period',
      label: 'Prochaine action',
      options: {
        overdue: 'En retard',
        today: 'Aujourd’hui',
        week: '7 prochains jours',
        upcoming: 'À venir',
        undated: 'Sans échéance',
      },
    },
  ];
  return (
    <div className="lt-page">
      <ListSummary
        items={[
          [summary.total, 'opportunités au total'],
          [summary.open, 'opportunités ouvertes'],
          [summary.amount_cents == null ? '—' : money(summary.amount_cents), 'montant des affaires ouvertes'],
          [summary.won, 'opportunités gagnées'],
        ]}
      />
      <PagePanel>
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          placeholder="Opportunité, contact ou entreprise…"
          searchLabel="Rechercher une opportunité"
        />
        <ListTable
          title="Opportunités"
          rows={result?.items || []}
          total={result?.total || 0}
          overall={result?.total_unfiltered}
          state={state}
          change={change}
          columns={columns}
          loading={server.loading}
          error={server.error}
          onRetry={server.retry}
          emptyTitle="Aucune opportunité dans cette vue"
          emptyText="Créez une opportunité lorsqu’une conversation avance vers un rendez-vous ou une proposition."
        />
      </PagePanel>
    </div>
  );
}
