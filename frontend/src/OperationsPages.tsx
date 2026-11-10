import {useEffect, useState} from 'react';
import {
  AlertCircle,
  ArrowUpRight,
  Bell,
  ChevronLeft,
  ChevronRight,
  CalendarDays,
  Plus,
  Check,
  X,
} from 'lucide-react';
import {
  WorkspaceHeader,
  Panel,
  Field,
  Feedback,
  Blank,
  RouteLink,
  useRecord,
  when,
  personName,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {ListSummary} from './ListTable';
import './operations.css';
import {errorText} from './api';
export type OperationsProps = {
  api: Api;
  data: Row;
  url: string;
  navigate: Navigate;
  onOpen: (name: string, row?: Row) => void;
  onRefresh: () => Promise<void>;
};
export function AlertIndicator({api, navigate, revision}: {api: Api; navigate: Navigate; revision: unknown}) {
  const loaded = useRecord('/alerts', api, revision),
    counts = loaded.record?.counts;
  const total = (counts?.critical || 0) + (counts?.warning || 0);
  return (
    <button
      className="op-alert-trigger"
      aria-label={'Alertes' + (total ? ' · ' + total + ' à examiner' : '')}
      onClick={() => navigate('/alerts')}
    >
      <Bell size={17} />
      {total > 0 && <b>{total}</b>}
    </button>
  );
}
export function AlertsPage({api, data, navigate}: OperationsProps) {
  const [severity, setSeverity] = useState('all'),
    [page, setPage] = useState(1),
    loaded = useRecord('/alerts?severity=' + severity, api, data),
    record = loaded.record;
  return (
    <article className="ws-page">
      <WorkspaceHeader
        back="/"
        backLabel="Vue d’ensemble"
        eyebrow="EXPLOITATION"
        title="Alertes de l’espace"
        subtitle="Connexions, traitements et échéances qui demandent votre attention."
        navigate={navigate}
        actions={<button onClick={loaded.refresh}>Actualiser</button>}
      />
      <Feedback error={loaded.error} />
      <div className="op-status-tabs" role="group" aria-label="Gravité des alertes">
        {[
          ['all', 'Toutes'],
          ['critical', 'À résoudre'],
          ['warning', 'À examiner'],
          ['info', 'Informations'],
        ].map(([key, label]) => (
          <button
            key={key}
            aria-pressed={severity === key}
            onClick={() => {
              setSeverity(key);
              setPage(1);
            }}
          >
            {label}
            <span>{key === 'all' ? (record?.total ?? '…') : (record?.counts[key] ?? '…')}</span>
          </button>
        ))}
      </div>
      <Panel
        title={loaded.loading ? 'Chargement…' : (record?.items.length || 0) + ' alerte(s)'}
        description={
          'État recalculé à partir des données actuelles. ' +
          (record ? 'Actualisé le ' + when(record.checked_at) : '')
        }
      >
        {record?.items.length ? (
          <ul className="op-alert-list">
            {record.items.slice((page - 1) * 6, page * 6).map((row: Row) => (
              <li key={row.id} className={row.severity}>
                <AlertCircle size={20} />
                <div>
                  <h3>{row.title}</h3>
                  <p>{row.detail}</p>
                </div>
                <RouteLink url={row.url} navigate={navigate} className="ws-button-link">
                  Examiner
                  <ArrowUpRight size={14} />
                </RouteLink>
              </li>
            ))}
          </ul>
        ) : (
          <Blank
            title={loaded.loading ? 'Vérification des alertes…' : 'Aucune alerte dans cette catégorie'}
          />
        )}
        <CompactPages page={page} total={record?.items.length || 0} size={6} onPage={setPage} />
      </Panel>
    </article>
  );
}
export function QualityPage({api, data, navigate}: OperationsProps) {
  const [resource, setResource] = useState('contacts'),
    [scope, setScope] = useState('prospects'),
    [issue, setIssue] = useState(''),
    [q, setQ] = useState(''),
    [page, setPage] = useState(1);
  const loaded = useRecord(
      '/data-quality?' + new URLSearchParams({resource, scope, issue, q, page: String(page)}),
      api,
      data,
    ),
    record = loaded.record;
  return (
    <article className="ws-page">
      <WorkspaceHeader
        back="/companies"
        backLabel="Entreprises & contacts"
        eyebrow="QUALITÉ DES DONNÉES"
        title="Des fiches prêtes à travailler"
        subtitle="Retrouvez les informations manquantes et les preuves à actualiser."
        navigate={navigate}
      />
      <div className="op-status-tabs" role="group" aria-label="Fiches à contrôler">
        {[
          ['contacts', 'Contacts'],
          ['accounts', 'Entreprises'],
        ].map(([key, label]) => (
          <button
            key={key}
            aria-pressed={resource === key}
            onClick={() => {
              setResource(key);
              setIssue('');
              setPage(1);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <details className="op-filter-disclosure">
        <summary>
          Rechercher et filtrer les fiches
          {issue ? ' · ' + record?.checks.find((check: Row) => check.key === issue)?.label : ''}
        </summary>
        <div className="ws-filter-bar op-quality-filters">
          <Field label="Périmètre">
            <select
              value={scope}
              onChange={(event) => {
                setScope(event.target.value);
                setPage(1);
              }}
            >
              <option value="prospects">Entreprises prospectes</option>
              <option value="all">Toutes les fiches</option>
            </select>
          </Field>
          <Field label="Rechercher une fiche">
            <input
              type="search"
              value={q}
              maxLength={120}
              onChange={(event) => {
                setQ(event.target.value);
                setPage(1);
              }}
              placeholder="Nom, entreprise ou email…"
            />
          </Field>
          <Field label="Contrôle">
            <select
              value={issue}
              onChange={(event) => {
                setIssue(event.target.value);
                setPage(1);
              }}
            >
              <option value="">Tous les points à vérifier</option>
              {record?.checks.map((check: Row) => (
                <option key={check.key} value={check.key}>
                  {check.label} · {check.count}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </details>
      <Feedback error={loaded.error} />
      <details className="op-quality-overview">
        <summary>Bilan de qualité et critères de contrôle</summary>
        <div className="op-quality-checks">
          {record?.checks.map((check: Row) => (
            <button
              key={check.key}
              aria-pressed={issue === check.key}
              onClick={() => {
                setIssue(issue === check.key ? '' : check.key);
                setPage(1);
              }}
            >
              <strong>{check.count}</strong>
              <span>{check.label}</span>
            </button>
          ))}
        </div>
        <p className="op-method">
          Email et preuve du numéro : 90 jours. Preuves générales : 180 jours. Ces seuils signalent une revue
          à effectuer ; ils ne modifient aucune fiche.
        </p>
      </details>
      <Panel
        title={(record?.total ?? '…') + ' fiches à examiner'}
        description={(record?.total_records ?? '…') + ' fiches dans le périmètre choisi.'}
      >
        {record?.items.length ? (
          <ul className="op-quality-list">
            {record.items.map((row: Row) => (
              <li key={row.id}>
                <RouteLink url={row.url} navigate={navigate}>
                  <strong>{row.name}</strong>
                  <small>{row.company}</small>
                </RouteLink>
                <div>
                  {row.issues.map((issue: Row) => (
                    <span key={issue.key}>{issue.label}</span>
                  ))}
                </div>
                <RouteLink url={row.url} navigate={navigate} label={'Compléter ' + row.name}>
                  <ArrowUpRight size={17} />
                </RouteLink>
              </li>
            ))}
          </ul>
        ) : (
          <Blank title={loaded.loading ? 'Chargement…' : 'Aucun point à vérifier dans cette sélection'} />
        )}
        <div className="ws-pagination">
          <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
            Précédent
          </button>
          <span>
            {record?.page || page} / {Math.max(1, Math.ceil((record?.total || 0) / 10))}
          </span>
          <button disabled={page * 10 >= (record?.total || 0)} onClick={() => setPage((value) => value + 1)}>
            Suivant
          </button>
        </div>
      </Panel>
    </article>
  );
}
export function AnalyticsPage({api, data, url, navigate}: OperationsProps) {
  const params = new URL(url, location.origin).searchParams;
  const [group, setGroup] = useState(params.get('group') || data.groups[0]?.id || ''),
    [campaign, setCampaign] = useState(''),
    [versionPage, setVersionPage] = useState(1);
  const loaded = useRecord(
      '/sequence-stats?' + new URLSearchParams({group_id: group, campaign_id: campaign}),
      api,
      data,
    ),
    record = loaded.record;
  const allSteps: Row[] = record?.versions.flatMap((row: Row) => row.steps) || [];
  return (
    <article className="ws-page">
      <WorkspaceHeader
        back="/?view=campaigns"
        backLabel="Toutes les campagnes"
        eyebrow="STATISTIQUES"
        title="L’effet de chaque étape"
        subtitle="Comparez les séquences et repérez les messages qui ouvrent une conversation."
        navigate={navigate}
      />
      <div className="ws-filter-bar op-quality-filters">
        <Field label="Campagne principale">
          <select
            value={group}
            onChange={(event) => {
              setGroup(event.target.value);
              setCampaign('');
              setVersionPage(1);
            }}
          >
            <option value="">Toutes les campagnes</option>
            {data.groups.map((row: Row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Version à examiner">
          <select
            value={campaign}
            onChange={(event) => {
              setCampaign(event.target.value);
              setVersionPage(1);
            }}
          >
            <option value="">Comparer toutes les versions</option>
            {data.campaigns
              .filter((row: Row) => !group || row.group_id === group)
              .map((row: Row) => (
                <option key={row.id} value={row.id}>
                  {row.name} · v{row.config.sequence_version || 1}
                </option>
              ))}
          </select>
        </Field>
      </div>
      <Feedback error={loaded.error} />
      <div className="op-stats-summary">
        <ListSummary
          collapseMobile={false}
          label="Statistiques des séquences"
          items={[
            [allSteps.reduce((n, row) => n + row.sent, 0), 'emails envoyés'],
            [allSteps.reduce((n, row) => n + row.replies, 0), 'répondants par étape'],
            [record?.unattributed_events ?? 0, 'événements sans attribution certaine'],
            [record?.manual_reply_events ?? 0, 'réponses à un email manuel'],
          ]}
        />
      </div>
      <details className="op-method-details">
        <summary>Comment les résultats sont calculés</summary>
        <p className="op-method">
          {record?.unattributed_events || 0} événements sans attribution certaine ;{' '}
          {record?.manual_reply_events || 0} réponses à un email manuel. {record?.method} Comparaison
          descriptive : les volumes et les cibles peuvent différer entre versions.
        </p>
      </details>
      {record?.versions.length ? (
        record.versions.slice((versionPage - 1) * 6, versionPage * 6).map((row: Row) => (
          <details className="op-version" key={row.id} open={record.versions.length === 1}>
            <summary>
              <span>
                <strong>
                  {row.name} · v{row.version}
                </strong>
                <small>{row.niche}</small>
              </span>
              <span>
                {row.steps.reduce((n: number, step: Row) => n + step.sent, 0)} envoyés ·{' '}
                {row.steps.reduce((n: number, step: Row) => n + step.replies, 0)} répondants
              </span>
            </summary>
            <Panel
              title={row.name + ' · v' + row.version}
              description={row.niche}
              action={
                <RouteLink url={'/sequences/' + row.id} navigate={navigate} className="ws-button-link">
                  Voir la séquence
                  <ArrowUpRight size={14} />
                </RouteLink>
              }
            >
              <div className="ws-comparison">
                <table>
                  <thead>
                    <tr>
                      <th>Étape</th>
                      <th>Envoyés</th>
                      <th>Répondants</th>
                      <th>Taux de réponse</th>
                      <th>Intérêt confirmé</th>
                      <th>Erreurs / rejets</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.steps.map((step: Row) => (
                      <tr key={step.index}>
                        <td>
                          <strong>
                            J+{step.day} ·{' '}
                            {{email: 'Email', phone: 'Appel', linkedin: 'LinkedIn'}[step.channel as string]}
                          </strong>
                          <small>{step.subject || 'Action manuelle'}</small>
                        </td>
                        {step.channel === 'email' ? (
                          <>
                            <td>{step.sent}</td>
                            <td>{step.replies}</td>
                            <td>
                              <span className="op-rate">
                                <i style={{width: (step.reply_rate || 0) + '%'}} />
                                {step.reply_rate == null
                                  ? '—'
                                  : step.reply_rate.toLocaleString('fr-FR') + ' %'}
                              </span>
                            </td>
                            <td>{step.positive}</td>
                            <td>
                              {step.failed} / {step.bounces}
                            </td>
                          </>
                        ) : (
                          <td colSpan={5}>Statistiques email non applicables à cette action.</td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!row.steps.some((step: Row) => step.sent) && (
                <p className="ws-context">
                  Aucun email de cette version n’a encore été envoyé. Les taux apparaîtront avec l’activité
                  réelle.
                </p>
              )}
            </Panel>
          </details>
        ))
      ) : (
        <Blank title={loaded.loading ? 'Chargement…' : 'Aucune séquence dans cette sélection'} />
      )}
      <CompactPages
        page={versionPage}
        total={record?.versions.length || 0}
        size={6}
        onPage={setVersionPage}
      />
    </article>
  );
}
const dateKey = (date: Date) =>
  [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
const localValue = (value: string) => {
  const date = new Date(value);
  return (
    dateKey(date) +
    'T' +
    String(date.getHours()).padStart(2, '0') +
    ':' +
    String(date.getMinutes()).padStart(2, '0')
  );
};
const kindNames: Row = {
  phone: 'Appel',
  linkedin: 'LinkedIn',
  review: 'Suivi / rappel',
  meeting: 'Rendez-vous',
  next_action: 'Prochaine action',
};
export function CalendarPage({api, data, url, navigate, onOpen, onRefresh}: OperationsProps) {
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1)),
    [day, setDay] = useState(dateKey(new Date())),
    [kind, setKind] = useState('all'),
    [status, setStatus] = useState('pending'),
    [campaign, setCampaign] = useState(''),
    [selected, setSelected] = useState<Row | null>(null),
    [at, setAt] = useState(''),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false),
    [creating, setCreating] = useState(false),
    [agendaPage, setAgendaPage] = useState(1),
    [calendarView, setCalendarView] = useState('day');
  const overdue = new URL(url, location.origin).searchParams.get('overdue') === '1';
  const start = new Date(month);
  start.setDate(1 - ((start.getDay() + 6) % 7));
  const end = new Date(start);
  end.setDate(end.getDate() + 42);
  const loaded = useRecord(
      '/calendar?' +
        new URLSearchParams({
          start: start.toISOString(),
          end: end.toISOString(),
          kind,
          status,
          campaign_id: campaign,
          overdue: String(overdue),
        }),
      api,
      data,
    ),
    items: Row[] = loaded.record?.items || [],
    daily = items.filter((row) => dateKey(new Date(row.at)) === day);
  const visible = overdue ? items : daily;
  useEffect(() => {
    setAgendaPage(1);
    setSelected(null);
  }, [day, kind, status, campaign, overdue]);
  function shift(delta: number) {
    const next = new Date(month.getFullYear(), month.getMonth() + delta, 1);
    setMonth(next);
    setDay(dateKey(next));
    setSelected(null);
  }
  async function save() {
    if (!selected || !at) return;
    setBusy(true);
    setError('');
    try {
      await api('/calendar/' + selected.record_id, 'PATCH', {
        kind: selected.type === 'task' ? 'task' : selected.kind,
        at: new Date(at).toISOString(),
        expected_updated_at: selected.updated_at,
        request_id: crypto.randomUUID(),
      });
      setSelected(null);
      setNotice('Nouvelle échéance enregistrée.');
      loaded.refresh();
      await onRefresh();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="ws-page op-planning-page">
      <WorkspaceHeader
        back="/?view=tasks"
        backLabel="Prochaines actions"
        eyebrow="PLANNING"
        title={overdue ? 'Les actions à reprendre' : 'Le planning des conversations'}
        subtitle="Appels, rappels, rendez-vous et prochaines étapes des opportunités."
        navigate={navigate}
        actions={
          <button className="primary" onClick={() => setCreating(!creating)}>
            <Plus size={16} />
            Planifier un suivi
          </button>
        }
      />
      {creating && (
        <QuickSchedule
          api={api}
          onClose={() => setCreating(false)}
          onSaved={async () => {
            setCreating(false);
            loaded.refresh();
            await onRefresh();
          }}
        />
      )}
      {!creating && (
        <>
          <details className="op-filter-disclosure">
            <summary>
              Filtrer le planning
              {kind !== 'all' || status !== 'pending' || campaign ? ' · filtres actifs' : ''}
            </summary>
            <div className="ws-filter-bar op-quality-filters">
              <Field label="Type d’action">
                <select value={kind} onChange={(event) => setKind(event.target.value)}>
                  <option value="all">Tous les types</option>
                  {Object.entries(kindNames).map(([key, label]) => (
                    <option key={key} value={key}>
                      {String(label)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="État">
                <select value={status} onChange={(event) => setStatus(event.target.value)}>
                  <option value="pending">À réaliser</option>
                  <option value="all">Tous les états</option>
                </select>
              </Field>
              <Field label="Sous-campagne">
                <select value={campaign} onChange={(event) => setCampaign(event.target.value)}>
                  <option value="">Toutes les sous-campagnes</option>
                  {data.campaigns.map((row: Row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </details>
          <Feedback error={error || loaded.error} notice={notice} />
          <div className="op-calendar-mobile-tabs" role="group" aria-label="Vue du planning">
            <button aria-pressed={calendarView === 'day'} onClick={() => setCalendarView('day')}>
              Journée
            </button>
            <button aria-pressed={calendarView === 'month'} onClick={() => setCalendarView('month')}>
              Choisir une date
            </button>
          </div>
          <div
            data-view={calendarView}
            className={overdue ? 'op-planning-split overdue' : 'op-planning-split'}
          >
            {overdue ? (
              <RouteLink url="/calendar" navigate={navigate} className="ws-button-link">
                Revenir au calendrier
                <CalendarDays size={15} />
              </RouteLink>
            ) : (
              <div className="op-calendar">
                <header>
                  <h2>{month.toLocaleDateString('fr-FR', {month: 'long', year: 'numeric'})}</h2>
                  <div className="actions">
                    <button aria-label="Mois précédent" onClick={() => shift(-1)}>
                      <ChevronLeft size={16} />
                    </button>
                    <button
                      onClick={() => {
                        const today = new Date();
                        setMonth(new Date(today.getFullYear(), today.getMonth(), 1));
                        setDay(dateKey(today));
                      }}
                    >
                      Aujourd’hui
                    </button>
                    <button aria-label="Mois suivant" onClick={() => shift(1)}>
                      <ChevronRight size={16} />
                    </button>
                  </div>
                </header>
                <div className="op-weekdays">
                  {['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'].map((text) => (
                    <span key={text}>{text}</span>
                  ))}
                </div>
                <div className="op-month-grid">
                  {Array.from({length: 42}, (_, index) => {
                    const date = new Date(start);
                    date.setDate(date.getDate() + index);
                    const key = dateKey(date),
                      events = items.filter((row) => dateKey(new Date(row.at)) === key);
                    return (
                      <button
                        key={key}
                        className={
                          (date.getMonth() !== month.getMonth() ? 'other-month ' : '') +
                          (key === dateKey(new Date()) ? 'today' : '')
                        }
                        aria-pressed={day === key}
                        aria-label={
                          date.toLocaleDateString('fr-FR', {dateStyle: 'full'}) +
                          ' · ' +
                          events.length +
                          ' action(s)'
                        }
                        onClick={() => {
                          setDay(key);
                          setSelected(null);
                          setCalendarView('day');
                        }}
                      >
                        <time>{date.getDate()}</time>
                        {events.slice(0, 2).map((row) => (
                          <span key={row.id} className={'op-calendar-event ' + row.kind}>
                            {kindNames[row.kind]} · {row.title}
                          </span>
                        ))}
                        {events.length > 2 && <small>+{events.length - 2} autre(s)</small>}
                        {events.length > 0 && <i>{events.length}</i>}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            <Panel
              title={
                overdue
                  ? 'Actions en retard'
                  : new Date(day + 'T12:00:00').toLocaleDateString('fr-FR', {
                      weekday: 'long',
                      day: 'numeric',
                      month: 'long',
                    })
              }
              description={
                visible.length + ' élément(s) · ' + Intl.DateTimeFormat().resolvedOptions().timeZone
              }
            >
              {!selected &&
                (visible.length ? (
                  <ul className="op-agenda">
                    {visible.slice((agendaPage - 1) * 6, agendaPage * 6).map((row) => (
                      <li key={row.id}>
                        <time>
                          {overdue
                            ? when(row.at)
                            : new Date(row.at).toLocaleTimeString('fr-FR', {
                                hour: '2-digit',
                                minute: '2-digit',
                              })}
                        </time>
                        <div>
                          <span>{kindNames[row.kind]}</span>
                          <strong>{row.title}</strong>
                          <small>
                            {row.contact
                              ? personName(row.contact) + ' · ' + row.contact.account.name
                              : row.company}
                          </small>
                        </div>
                        <div className="actions">
                          <button
                            onClick={() =>
                              onOpen(row.type === 'task' ? 'task' : 'editOpportunity', row.record)
                            }
                          >
                            {row.status === 'pending' ? 'Ouvrir' : 'Consulter'}
                          </button>
                          {row.status === 'pending' && (
                            <button
                              onClick={() => {
                                setSelected(row);
                                setAt(localValue(row.at));
                              }}
                            >
                              Déplacer
                            </button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Blank title={loaded.loading ? 'Chargement du planning…' : 'Aucune action prévue'}>
                    Choisissez un autre jour ou planifiez un suivi.
                  </Blank>
                ))}
              {!selected && (
                <CompactPages page={agendaPage} total={visible.length} size={6} onPage={setAgendaPage} />
              )}{' '}
              {selected && (
                <form
                  className="op-reschedule"
                  onSubmit={(event) => {
                    event.preventDefault();
                    save();
                  }}
                >
                  <strong>{selected.title}</strong>
                  <Field label="Nouvelle date et heure">
                    <input
                      type="datetime-local"
                      required
                      value={at}
                      onChange={(event) => setAt(event.target.value)}
                    />
                  </Field>
                  <div className="actions">
                    <button className="primary" disabled={busy}>
                      <Check size={15} />
                      Enregistrer l’échéance
                    </button>
                    <button type="button" onClick={() => setSelected(null)}>
                      Annuler
                    </button>
                  </div>
                </form>
              )}
            </Panel>
          </div>
        </>
      )}
    </article>
  );
}
function QuickSchedule({
  api,
  onClose,
  onSaved,
}: {
  api: Api;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [q, setQ] = useState(''),
    [contacts, setContacts] = useState<Row[]>([]),
    [contact, setContact] = useState(''),
    [title, setTitle] = useState(''),
    [at, setAt] = useState(''),
    [notes, setNotes] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(
      () =>
        api('/lists/contacts?page_size=10&q=' + encodeURIComponent(q))
          .then((result) => {
            if (alive) setContacts(result.items);
          })
          .catch((error) => {
            if (alive) setError(errorText(error));
          }),
      200,
    );
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, q]);
  return (
    <Panel
      title="Planifier un suivi interne"
      description="Ajoutez un rappel ; aucun appel n’est déclenché automatiquement."
      action={
        <button className="ws-icon" aria-label="Fermer le suivi" onClick={onClose}>
          <X size={16} />
        </button>
      }
    >
      <Feedback error={error} />
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError('');
          try {
            await api('/tasks', 'POST', {
              contact_id: contact,
              channel: 'review',
              title,
              due_at: new Date(at).toISOString(),
              notes,
            });
            await onSaved();
          } catch (error) {
            setError(errorText(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="ws-form-grid">
          <Field label="Rechercher le contact">
            <input
              type="search"
              value={q}
              onChange={(event) => {
                setQ(event.target.value);
                setContact('');
              }}
            />
          </Field>
          <Field label="Contact du suivi">
            <select value={contact} onChange={(event) => setContact(event.target.value)} required>
              <option value="">Choisir un contact</option>
              {contacts.map((row) => (
                <option key={row.id} value={row.id}>
                  {personName(row)} · {row.account.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Intitulé du suivi">
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              required
              maxLength={220}
            />
          </Field>
          <Field label="Date et heure du suivi">
            <input
              type="datetime-local"
              value={at}
              onChange={(event) => setAt(event.target.value)}
              required
            />
          </Field>
        </div>
        <Field label="Contexte du suivi">
          <textarea value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={3000} />
        </Field>
        <button className="primary" disabled={busy}>
          Planifier le suivi
        </button>
      </form>
    </Panel>
  );
}

function CompactPages({
  page,
  total,
  size,
  onPage,
}: {
  page: number;
  total: number;
  size: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return pages > 1 ? (
    <div className="ws-pagination">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Précédent
      </button>
      <span>
        {page} / {pages} · {total} éléments
      </span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Suivant
      </button>
    </div>
  ) : null;
}
