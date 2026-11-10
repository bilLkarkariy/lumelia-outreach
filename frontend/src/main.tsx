import {RuntimePanel} from './RuntimePanel';
import {SettingsWorkspace} from './SettingsWorkspace';
import {NativeWarmup} from './NativeWarmup';
import {UnifiedProspects} from './UnifiedProspects';
import React, {useState, useEffect, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {
  Target,
  ArrowUpRight,
  Plus,
  Pause,
  Play,
  X,
  ChevronRight,
  Check,
  Mail,
  Phone,
  Linkedin,
} from 'lucide-react';
import './style.css';
import {Integrations} from './Integrations';
import {SenderAccounts} from './SenderAccounts';
import {Autopilot} from './Autopilot';
import {ListPreferences} from './ListTable';
import {CompaniesList, TasksList, MessagesList, OpportunitiesList} from './ListPages';
import {OfferLibrary, CampaignForm, ICPForm, OpportunityForm} from './Offers';
import {CampaignGroups, CampaignGroupForm} from './CampaignGroups';
import {SourcingForm} from './Sourcing';
import {MarketField} from './Locales';
import {ContactPage} from './ContactPage';
import {CompanyPage} from './CompanyPage';
import {CampaignPage} from './CampaignPage';
import {ProspectDrawer, LeadAcquisition} from './ProspectWorkspace';
import './prospect-workspace.css';
import {SequenceEditor} from './SequenceEditor';
import {QualificationDesk} from './QualificationDesk';
import {InboxPage} from './InboxPage';
import {WorkspaceShell, SidebarToggle, sidebarPageLabel} from './WorkspaceSidebar';
import {AgentDesk, AgentNotifications} from './AgentDesk';
import {CalendarPage, QualityPage, AnalyticsPage, AlertsPage, AlertIndicator} from './OperationsPages';
import {contactUrl, useWorkspaceNavigation} from './navigation';
import {useFormValidationFeedback} from './validation';
import {errorText, type Json, type Row} from './api';

const routeNames: Row = {
  dashboard: 'Vue d’ensemble',
  contacts: 'Prospects',
  offers: 'Offres',
  campaigns: 'Campagnes',
  tasks: 'À faire',
  pipeline: 'Opportunités',
  senders: 'Boîtes email',
  settings: 'Réglages',
  contact: 'Fiche contact',
  company: 'Fiche entreprise',
  campaign: 'Campagne',
  sequence: 'Séquence',
  qualification: 'File de qualification',
  inbox: 'Boîte de réception',
  calendar: 'Le planning des conversations',
  quality: 'Qualité des données',
  analytics: 'Résultats',
  alerts: 'Alertes',
  agent: 'Agent Lumelia',
};
/** One name per surface, used for the page heading, the browser tab and history. */
function pageNameFor(view: string, entityView: string) {
  return view === 'contacts' && entityView === 'companies'
    ? 'Entreprises'
    : routeNames[view] || sidebarPageLabel(view, entityView);
}
let csrf = '';
async function api(path: string, method = 'GET', data?: unknown): Promise<Json> {
  const response = await fetch('/api' + path, {
    method,
    credentials: 'include',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === 'string'
        ? result.detail
        : Array.isArray(result.detail)
          ? result.detail
              .map((item: string | {msg?: string}) =>
                typeof item === 'string'
                  ? item
                  : String(item.msg || 'Vérifiez les champs.').replace(/^Value error, /, ''),
              )
              .join(' ')
          : 'Action impossible.',
    );
  return result;
}
async function upload(path: string, body: FormData): Promise<Json> {
  const response = await fetch('/api' + path, {
    method: 'POST',
    credentials: 'include',
    headers: {'X-CSRF-Token': csrf},
    body,
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === 'string'
        ? result.detail
        : 'Import impossible. Vérifiez le fichier et les champs.',
    );
  return result;
}
const euro = (cents: number) =>
  new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(
    cents / 100,
  );
const date = (value: string) =>
  value ? new Date(value).toLocaleString('fr-FR', {dateStyle: 'short', timeStyle: 'short'}) : '—';
const labels: Row = {
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
  simulated: 'Simulé',
  pending: 'À faire',
  completed: 'Terminé',
  cancelled: 'Annulé',
  unknown: 'À vérifier',
  valid: 'Vérifié',
  invalid: 'Invalide',
  risky: 'Risqué',
  simulation: 'Simulation',
  live: 'Réel',
  meeting: 'Rendez-vous',
  proposal: 'Proposition',
  won: 'Gagné',
  lost: 'Perdu',
  phone: 'Appel',
  linkedin: 'LinkedIn',
  review: 'Suivi',
  configured: 'Configuré',
  appropriate: 'Approprié à la fonction',
  direct: 'Ligne directe',
  switchboard: 'Standard',
  confirmed: 'Confirmé',
  signal: 'Signal public',
  absent: 'Absent',
};
function Badge({value}: {value: string}) {
  return <span className={'badge ' + value}>{labels[value] || value}</span>;
}
function Empty({title, children}: {title: string; children?: React.ReactNode}) {
  return (
    <div className="empty">
      <Target size={28} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
function Field({label, children}: {label: string; children: React.ReactNode}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
function Modal({
  title,
  children,
  close,
  className = '',
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} className={className} onCancel={close} aria-labelledby="dialog-title">
      <header>
        <h2 id="dialog-title">{title}</h2>
        <button className="icon" aria-label="Fermer" onClick={close}>
          <X size={20} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
function App() {
  const [user, setUser] = useState<Row | null>(null),
    [ready, setReady] = useState(false);
  const [cohort, setCohort] = useState('');
  const [settingsTab, setSettingsTab] = useState('connections'),
    [journalPage, setJournalPage] = useState(1);
  const reloadId = useRef(0);
  const [actionView, setActionView] = useState('tasks');
  const navigation = useWorkspaceNavigation();
  useFormValidationFeedback();
  const {view, setView} = navigation;
  const entityView = navigation.entityView;
  const listPosition = useRef(0);
  const pageName = pageNameFor(view, entityView);
  useEffect(() => {
    document.title = user ? pageName + ' · Lumelia' : 'Lumelia · Prospection';
  }, [user, pageName]);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  const [fullWorkspaceLoaded, setWorkspaceLoaded] = useState(false);
  const [data, setData] = useState<Row>({}),
    [modal, setModal] = useState(''),
    [selected, setSelected] = useState<Row | null>(null);
  const lightWorkspace = view === 'settings' || view === 'senders';
  const workspaceLoaded = lightWorkspace ? !!data.state : fullWorkspaceLoaded;
  const admin = user?.role === 'admin';
  useEffect(() => {
    (async () => {
      try {
        const me = await api('/auth/me');
        csrf = me.csrf_token;
        setUser(me.user);
      } catch {
        /* Password entry */
      } finally {
        setReady(true);
      }
    })();
  }, []);

  async function reload() {
    const requestId = ++reloadId.current;
    if (lightWorkspace) {
      const [state, expenses, jobs, senders] = await Promise.all([
        api('/state'),
        api('/expenses'),
        api('/jobs'),
        api('/senders'),
      ]);
      if (requestId !== reloadId.current) return;
      setData((previous) => ({...previous, state, expenses, jobs, senders}));
      return;
    }
    const [
      dashboard,
      state,
      contacts,
      campaigns,
      icps,
      tasks,
      opportunities,
      accounts,
      messages,
      expenses,
      jobs,
      senders,
      offers,
      groups,
    ] = await Promise.all([
      api(
        '/dashboard' +
          (cohort
            ? cohort.startsWith('group:')
              ? '?group_id=' + cohort.slice(6)
              : '?campaign_id=' + cohort
            : ''),
      ),
      api('/state'),
      api('/contacts?limit=200'),
      api('/campaigns'),
      api('/icps'),
      api('/tasks'),
      api('/opportunities'),
      api('/accounts'),
      api('/messages'),
      api('/expenses'),
      api('/jobs'),
      api('/senders'),
      api('/offers'),
      api('/campaign-groups'),
    ]);
    if (requestId !== reloadId.current) return;
    setData({
      dashboard,
      state,
      contacts: contacts.items,
      total: contacts.total,
      campaigns,
      icps,
      tasks,
      opportunities,
      accounts,
      messages,
      expenses,
      jobs,
      senders,
      offers,
      groups,
    });
    setWorkspaceLoaded(true);
  }
  useEffect(() => {
    if (!user) return;
    let alive = true;
    const params = new URL(navigation.url, location.origin).searchParams;
    const record = params.get('record');
    if (view === 'pipeline' && record)
      api('/operations/opportunities/' + encodeURIComponent(record))
        .then((row) => {
          if (alive) open('editOpportunity', row);
        })
        .catch((error) => {
          if (alive) setError(error.message);
        });
    if (view === 'settings') {
      const tab = params.get('incoming')
        ? 'phone'
        : params.get('tab') === 'operations'
          ? 'journal'
          : params.get('tab') || 'connections';
      if (['connections', 'warmup', 'phone', 'notifications', 'budget', 'backup', 'journal'].includes(tab))
        setSettingsTab(tab);
      if (tab === 'journal') setJournalPage(1);
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- follows the URL; view is what this effect sets
  }, [user, navigation.url]);
  useEffect(() => {
    if (!user) return;
    let refreshing = false;
    const update = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        await reload();
      } catch (e) {
        setError(errorText(e));
      } finally {
        refreshing = false;
      }
    };
    const refresh = () => {
      if (!document.hidden) void update();
    };
    void update();
    const timer = setInterval(refresh, 15000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- polling restarts with the session or cohort; reload is recreated on every render
  }, [user, cohort, lightWorkspace]);
  useEffect(() => {
    const frame = requestAnimationFrame(() =>
      window.scrollTo({top: view === 'contacts' ? listPosition.current : 0, behavior: 'instant'}),
    );
    return () => cancelAnimationFrame(frame);
  }, [view, navigation.contactId, navigation.accountId, navigation.groupId, navigation.sequenceId]);
  function followContact(event: React.MouseEvent<HTMLAnchorElement>, id: string) {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    listPosition.current = window.scrollY;
    setError('');
    setNotice('');
    navigation.navigate(
      view === 'contacts' ? '/contacts?prospect=' + encodeURIComponent(id) : contactUrl(id),
    );
  }
  async function action(fn: () => Promise<unknown>, success = 'Enregistré.') {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
      await reload();
      setNotice(success);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function downloadBackup() {
    const response = await fetch('/api/backups', {
      method: 'POST',
      credentials: 'include',
      headers: {'X-CSRF-Token': csrf},
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.detail || 'Sauvegarde indisponible.');
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = 'lumelia-' + new Date().toISOString().slice(0, 10) + '.dump.fernet';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function open(name: string, row: Row | null = null) {
    setSelected(row);
    setModal(name);
    setError('');
  }
  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true);
    setError('');
    try {
      const result = await api('/auth/login', 'POST', fields);
      csrf = result.csrf_token;
      setUser(result.user);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  if (!ready) return <main className="loading">Chargement de Lumelia…</main>;
  if (!user)
    return (
      <main className="auth">
        <div className="auth-story">
          <div className="brand">
            l<span>umelia</span>
            <i>•</i>
          </div>
          <div>
            <span className="eyebrow">ESPACE DE PROSPECTION</span>
            <h1>
              Les bonnes entreprises.
              <br />
              La prochaine conversation.
            </h1>
            <p>Une vue commune pour qualifier, prendre contact et suivre chaque opportunité.</p>
          </div>
          <small>Lumelia · Espace interne</small>
        </div>
        <section>
          <form onSubmit={login}>
            <span className="eyebrow">LUMELIA</span>
            <h2>Accéder à l’espace</h2>
            <p>Votre espace de prospection interne.</p>
            {error && (
              <div role="alert" className="alert error">
                {error}
              </div>
            )}
            <Field label="Mot de passe">
              <input name="password" type="password" required autoComplete="current-password" autoFocus />
            </Field>
            <button disabled={busy} className="primary">
              {busy ? 'Ouverture…' : 'Entrer'}
              <ArrowUpRight size={17} />
            </button>
          </form>
        </section>
      </main>
    );

  const activeView = ['contact', 'company', 'qualification', 'quality'].includes(view)
    ? 'contacts'
    : ['campaign', 'sequence', 'analytics', 'dashboard', 'pipeline'].includes(view)
      ? 'campaigns'
      : ['inbox', 'calendar', 'agent', 'alerts'].includes(view)
        ? 'tasks'
        : ['offers', 'senders', 'settings'].includes(view)
          ? 'settings'
          : view;
  const tabs: Row = {
    contacts: [
      ['/contacts', 'Prospects'],
      ['/companies', 'Entreprises'],
    ],
    tasks: [
      ['/tasks', 'Actions'],
      ['/inbox', 'Réponses'],
      ['/calendar', 'Calendrier'],
      ['/agent?view=human', 'Agent & interventions'],
      ['/alerts', 'Alertes'],
    ],
    campaigns: [
      ['/campaigns', 'Campagnes'],
      ['/analytics', 'Résultats'],
      ['/?view=pipeline', 'Opportunités'],
    ],
    settings: [
      ['/settings', 'Réglages'],
      ['/?view=senders', 'Boîtes email'],
      ['/?view=offers', 'Offres'],
    ],
  };
  const dedicated = [
    'contact',
    'company',
    'campaign',
    'sequence',
    'qualification',
    'inbox',
    'calendar',
    'quality',
    'analytics',
    'alerts',
    'agent',
  ].includes(view);
  const state = data.state || {},
    stats = data.dashboard || {};
  const targetJob = new URL(navigation.url, location.origin).searchParams.get('job');
  const journalJobs: Row[] = targetJob
    ? (data.jobs || []).filter((row: Row) => row.id === targetJob)
    : data.jobs || [];
  const contactName = (id: string) => {
    const c = data.contacts?.find((x: Row) => x.id === id);
    return c ? `${c.first_name} ${c.last_name} · ${c.account.name}` : 'Contact';
  };
  function form(handler: (f: Row) => Promise<unknown>) {
    return (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const f = Object.fromEntries(new FormData(event.currentTarget));
      action(async () => {
        await handler(f);
        setModal('');
      });
    };
  }
  return (
    <ListPreferences key={user.id} storageKey={'lumelia-lists-v1:' + user.id}>
      <WorkspaceShell
        user={user}
        view={view}
        entityView={entityView}
        url={navigation.url}
        navigate={navigation.navigate}
        api={api}
        pendingTasks={stats.pending_tasks}
        busy={busy}
        onLogout={() =>
          action(async () => {
            await api('/auth/logout', 'POST');
            setUser(null);
          })
        }
      >
        <main className="main" id="workspace-main" tabIndex={-1}>
          <div className="topbar">
            <div className="sb-topbar-leading">
              <SidebarToggle />
              <span className="sb-breadcrumb">
                <span>Lumelia</span>
                <ChevronRight size={13} />
                <span>{sidebarPageLabel(view, entityView)}</span>
              </span>
            </div>
            <div className="op-top-tools">
              <AgentNotifications api={api} navigate={navigation.navigate} revision={data} />
              <AlertIndicator api={api} navigate={navigation.navigate} revision={data} />
              <span className="system-status">
                <span className={'dot ' + (state.paused ? 'amber' : '')} />
                {!data.state
                  ? 'Chargement du statut…'
                  : state.paused
                    ? 'Envois en pause'
                    : !state.live_actions_enabled
                      ? 'Envois réels désactivés'
                      : 'Envois autorisés'}
              </span>
            </div>
          </div>
          <div
            className={
              'content ' +
              (dedicated ? 'contact-content' : 'workspace-content') +
              (view === 'settings' ? ' settings-page-content' : '')
            }
          >
            {view !== 'settings' && (
              <nav className="workspace-context-tabs" aria-label="Vues de la rubrique">
                {(tabs[activeView] || []).map(([url, label]: string[]) => (
                  <a
                    key={url}
                    className={
                      navigation.url.split('?')[0] === url.split('?')[0] &&
                      (!url.includes('?') || navigation.url === url)
                        ? 'active'
                        : ''
                    }
                    href={url}
                    onClick={(e) => {
                      if (!e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && !e.button) {
                        e.preventDefault();
                        navigation.navigate(url);
                      }
                    }}
                  >
                    {label}
                  </a>
                ))}
              </nav>
            )}
            {!dedicated && (
              <header className="page-header">
                <div>
                  <span className="eyebrow">
                    {new Date().toLocaleDateString('fr-FR', {weekday: 'long', day: 'numeric', month: 'long'})}
                  </span>
                  <h1>{pageName}</h1>
                  <p>
                    {
                      {
                        dashboard: 'L’essentiel pour faire avancer vos conversations.',
                        contacts: 'Qui contacter, comment et à quel moment.',
                        offers: 'Les offres, leurs cibles et leur périmètre.',
                        campaigns: 'La cible, les messages et les résultats de chaque campagne.',
                        tasks: 'Les conversations et actions qui demandent votre attention.',
                        pipeline: 'De la première conversation à l’acompte reçu.',
                        senders: 'Connexion, réception et progression de chaque adresse.',
                        settings: 'Connexions, téléphone et préférences de votre espace.',
                      }[view]
                    }
                  </p>
                </div>
                <div className="actions">
                  {view === 'contacts' && (
                    <>
                      <button onClick={() => open('import')}>Importer CSV</button>
                      <button onClick={() => open('contact')}>
                        <Plus size={16} />
                        Ajouter
                      </button>
                      <button className="primary" onClick={() => open('acquire')}>
                        Récupérer des prospects
                        <ArrowUpRight size={16} />
                      </button>
                    </>
                  )}
                  {view === 'campaigns' && admin && (
                    <>
                      <button onClick={() => open('icp')}>Nouvelle cible</button>
                      <button className="primary" onClick={() => open('campaignGroup')}>
                        <Plus size={16} />
                        Créer une campagne
                      </button>
                    </>
                  )}
                  {view === 'pipeline' && (
                    <button className="primary" onClick={() => open('opportunity')}>
                      <Plus size={16} />
                      Nouvelle opportunité
                    </button>
                  )}
                  {view === 'dashboard' && admin && (
                    <button
                      disabled={busy || !data.state || (state.paused && !state.live_actions_enabled)}
                      title={
                        !state.live_actions_enabled
                          ? 'Les envois réels ne sont pas activés pour cet espace.'
                          : undefined
                      }
                      onClick={() => action(() => api('/state', 'PATCH', {paused: !state.paused}))}
                    >
                      {state.paused ? <Play size={16} /> : <Pause size={16} />}{' '}
                      {state.paused ? 'Autoriser les envois' : 'Suspendre les envois'}
                    </button>
                  )}
                </div>
              </header>
            )}
            {error && !modal && (
              <div className="alert error" role="alert">
                {error}
                <button className="icon" aria-label="Masquer l’erreur" onClick={() => setError('')}>
                  <X size={16} />
                </button>
              </div>
            )}
            {notice && (
              <div className="alert success" role="status">
                <Check size={16} />
                {notice}
              </div>
            )}
            {(view === 'settings' ? !data.state : !workspaceLoaded) ? (
              <div className="loading">Chargement des données…</div>
            ) : (
              <>
                {view === 'contact' && (
                  <ContactPage
                    id={navigation.contactId}
                    tab={navigation.tab}
                    data={data}
                    admin={admin}
                    api={api}
                    upload={upload}
                    onOpen={open}
                    onRefresh={reload}
                    navigate={navigation.navigate}
                    onDirtyChange={navigation.setDirty}
                    discardVersion={navigation.discardVersion}
                  />
                )}
                {view === 'company' && (
                  <CompanyPage
                    id={navigation.accountId}
                    section={navigation.section}
                    data={data}
                    api={api}
                    navigate={navigation.navigate}
                    onOpen={open}
                    onRefresh={reload}
                    onDirtyChange={navigation.setDirty}
                    discardVersion={navigation.discardVersion}
                    admin={admin}
                    busy={busy}
                    onAction={action}
                  />
                )}
                {view === 'campaign' && (
                  <CampaignPage
                    id={navigation.groupId}
                    section={navigation.section}
                    data={data}
                    api={api}
                    navigate={navigation.navigate}
                    onOpen={open}
                    onRefresh={reload}
                    admin={admin}
                    busy={busy}
                    onAction={action}
                  />
                )}
                {view === 'sequence' && (
                  <SequenceEditor
                    id={navigation.sequenceId}
                    api={api}
                    navigate={navigation.navigate}
                    onRefresh={reload}
                    onDirtyChange={navigation.setDirty}
                    discardVersion={navigation.discardVersion}
                    admin={admin}
                  />
                )}
                {view === 'qualification' && (
                  <QualificationDesk
                    url={navigation.url}
                    data={data}
                    api={api}
                    navigate={navigation.navigate}
                    onRefresh={reload}
                    onDirtyChange={navigation.setDirty}
                    discardVersion={navigation.discardVersion}
                    admin={admin}
                  />
                )}
                {view === 'inbox' && (
                  <InboxPage
                    id={navigation.inboxId}
                    url={navigation.url}
                    data={data}
                    api={api}
                    navigate={navigation.navigate}
                    onRefresh={reload}
                    onDirtyChange={navigation.setDirty}
                    discardVersion={navigation.discardVersion}
                    onOpen={open}
                  />
                )}

                {view === 'agent' && (
                  <AgentDesk
                    api={api}
                    data={data}
                    url={navigation.url}
                    navigate={navigation.navigate}
                    onRefresh={reload}
                  />
                )}
                {view === 'calendar' && (
                  <CalendarPage
                    api={api}
                    data={data}
                    url={navigation.url}
                    navigate={navigation.navigate}
                    onOpen={open}
                    onRefresh={reload}
                  />
                )}
                {view === 'quality' && (
                  <QualityPage
                    api={api}
                    data={data}
                    url={navigation.url}
                    navigate={navigation.navigate}
                    onOpen={open}
                    onRefresh={reload}
                  />
                )}
                {view === 'analytics' && (
                  <AnalyticsPage
                    api={api}
                    data={data}
                    url={navigation.url}
                    navigate={navigation.navigate}
                    onOpen={open}
                    onRefresh={reload}
                  />
                )}
                {view === 'alerts' && (
                  <AlertsPage
                    api={api}
                    data={data}
                    url={navigation.url}
                    navigate={navigation.navigate}
                    onOpen={open}
                    onRefresh={reload}
                  />
                )}
                {view === 'dashboard' && (
                  <>
                    <div className="actions">
                      <Field label="Campagne ou sous-campagne">
                        <select value={cohort} onChange={(e) => setCohort(e.target.value)}>
                          <option value="">Toutes les campagnes</option>
                          {data.groups.map((g: Row) => (
                            <optgroup key={g.id} label={g.name}>
                              <option value={'group:' + g.id}>Toutes les niches · {g.name}</option>
                              {data.campaigns
                                .filter((c: Row) => c.group_id === g.id)
                                .map((c: Row) => (
                                  <option key={c.id} value={c.id}>
                                    {c.niche} · {c.name}
                                  </option>
                                ))}
                            </optgroup>
                          ))}
                        </select>
                      </Field>
                      <p>
                        Résultats cumulés depuis la création · un contact compte une fois par type de réponse.
                      </p>
                    </div>
                    <div className="metrics">
                      {[
                        [
                          'Contacts prêts à contacter',
                          stats.approved_contacts,
                          'sur ' + stats.contacts + ' contacts',
                        ],
                        [
                          'Réponses positives',
                          stats.positive_replies,
                          stats.sent + ' emails réellement envoyés',
                        ],
                        [
                          'RDV qualifiés tenus',
                          stats.qualified_meetings,
                          stats.meetings_held + ' rendez-vous tenus',
                        ],
                        ['Acomptes reçus', euro(stats.deposits_cents), stats.won + ' opportunités gagnées'],
                      ].map(([label, value, sub]) => (
                        <section className="metric" key={label}>
                          <span>{label}</span>
                          <strong>{value}</strong>
                          <small>{sub}</small>
                        </section>
                      ))}
                    </div>
                    <section className="next-actions panel">
                      <header>
                        <div>
                          <h2>Préparer le lancement</h2>
                          <p>
                            {state.paused
                              ? 'Envois suspendus · avancez sur les conditions ci-dessous.'
                              : 'Vérifiez les conditions avant d’envoyer.'}
                          </p>
                        </div>
                      </header>
                      <div className="next-action-grid">
                        <button onClick={() => setView('contacts')}>
                          <strong>
                            {data.contacts.filter((c: Row) => c.status === 'candidate' && !c.paused).length}{' '}
                            contacts à qualifier
                          </strong>
                          <span>Vérifier la cible et les coordonnées</span>
                          <ArrowUpRight size={17} />
                        </button>
                        <button onClick={() => setView('campaigns')}>
                          <strong>Recherche et séquences</strong>
                          <span>Configurer chaque sous-campagne</span>
                          <ArrowUpRight size={17} />
                        </button>
                        <button onClick={() => setView('senders')}>
                          <strong>
                            {data.senders.length} boîtes ·{' '}
                            {data.senders.reduce((n: number, r: Row) => n + (r.available_24h || 0), 0)}{' '}
                            disponibles maintenant
                          </strong>
                          <span>Contrôler réception et cadence</span>
                          <ArrowUpRight size={17} />
                        </button>
                      </div>
                    </section>
                    <Autopilot api={api} admin={admin} onConfigure={() => setView('campaigns')} />
                    <div className="dashboard-grid">
                      <section className="panel">
                        <header>
                          <div>
                            <h2>À faire maintenant</h2>
                            <p>Les prochaines actions de l’équipe</p>
                          </div>
                          <button className="text" onClick={() => setView('tasks')}>
                            Tout voir <ArrowUpRight size={16} />
                          </button>
                        </header>
                        {data.tasks.filter((x: Row) => x.status === 'pending').length ? (
                          data.tasks
                            .filter((x: Row) => x.status === 'pending')
                            .slice(0, 5)
                            .map((x: Row) => (
                              <div className="list-row" key={x.id}>
                                <span className="channel-icon">
                                  {x.channel === 'phone' ? (
                                    <Phone size={18} />
                                  ) : x.channel === 'linkedin' ? (
                                    <Linkedin size={18} />
                                  ) : (
                                    <Mail size={18} />
                                  )}
                                </span>
                                <div>
                                  <strong>{x.title}</strong>
                                  <small>
                                    {contactName(x.contact_id)} · {date(x.due_at)}
                                  </small>
                                </div>
                                <button onClick={() => open('task', x)}>Traiter</button>
                              </div>
                            ))
                        ) : (
                          <Empty title="Votre file d’actions est vide">
                            Les réponses et les conversations à reprendre apparaîtront ici.
                          </Empty>
                        )}
                      </section>
                      <section className="panel">
                        <header>
                          <div>
                            <h2>Activité et budget</h2>
                            <p>Les chiffres utiles à la décision</p>
                          </div>
                        </header>
                        <div className="control-list">
                          <div>
                            <span>Coût d’acquisition / entreprise gagnée</span>
                            <strong>
                              {stats.cost_per_customer_cents == null
                                ? 'Non calculable'
                                : euro(stats.cost_per_customer_cents)}
                            </strong>
                          </div>
                          <div>
                            <span>Dépenses engagées / estimées</span>
                            <strong>{euro(stats.cost_cents)}</strong>
                          </div>
                          <div>
                            <span>Opérations à vérifier</span>
                            <button className="text" onClick={() => navigation.navigate('/alerts')}>
                              {stats.failed_jobs} · Voir les alertes
                            </button>
                          </div>
                        </div>
                        <details className="op-dashboard-details">
                          <summary>Détail des indicateurs et du calcul</summary>
                          <div className="control-list">
                            <div>
                              <span>Emails simulés</span>
                              <strong>{stats.simulated}</strong>
                            </div>
                            <div>
                              <span>Contacts avec email vérifié / total</span>
                              <strong>
                                {stats.valid_emails} / {stats.contacts}
                              </strong>
                            </div>
                            <div>
                              <span>Appels réalisés</span>
                              <strong>{stats.calls_completed}</strong>
                            </div>
                            <div>
                              <span>Tâches en retard</span>
                              <strong>{stats.overdue_tasks}</strong>
                            </div>
                            <div>
                              <span>Dépenses engagées / estimées</span>
                              <strong>{euro(stats.cost_cents)}</strong>
                            </div>
                            <div>
                              <span>Budget réservé</span>
                              <strong>{euro(stats.reserved_cents)}</strong>
                            </div>
                            <div>
                              <span>Coût par RDV qualifié</span>
                              <strong>
                                {stats.cost_per_qualified_meeting_cents === null
                                  ? '—'
                                  : euro(stats.cost_per_qualified_meeting_cents)}
                              </strong>
                            </div>
                            <div>
                              <span>Entreprises avec une affaire gagnée</span>
                              <strong>{stats.customers_won}</strong>
                            </div>
                            <div>
                              <span>Coût d’acquisition / entreprise gagnée</span>
                              <strong>
                                {stats.cost_per_customer_cents == null
                                  ? 'Non calculable'
                                  : euro(stats.cost_per_customer_cents)}
                              </strong>
                            </div>
                            <div>
                              <span>Opérations à vérifier</span>
                              <strong>{stats.failed_jobs}</strong>
                            </div>
                          </div>
                          <p className="padded">
                            Coût d’acquisition calculé sur les dépenses enregistrées, y compris les
                            estimations et les campagnes sans vente. Une entreprise compte une fois.
                            {cohort ? (
                              <>
                                {' '}
                                Frais partagés exclus de cette campagne : {euro(stats.shared_cost_cents)} à
                                répartir.
                              </>
                            ) : (
                              <> Frais partagés inclus : {euro(stats.shared_cost_cents)}.</>
                            )}{' '}
                            Pensez à saisir outils, boîtes, coordonnées, temps et commissions.
                          </p>
                          <div className="callout">
                            Le mode simulation ne transmet aucun email. Les résultats réels restent comptés
                            séparément.
                          </div>
                        </details>
                      </section>
                    </div>
                    <section className="panel">
                      <header>
                        <div>
                          <h2>Vos campagnes</h2>
                          <p>Aperçu · {data.campaigns.length} sous-campagnes dans l’espace</p>
                        </div>
                        <button className="text" onClick={() => setView('campaigns')}>
                          Gérer les campagnes <ArrowUpRight size={16} />
                        </button>
                      </header>
                      {data.campaigns.length ? (
                        data.campaigns.slice(0, 3).map((c: Row) => (
                          <div className="list-row" key={c.id}>
                            <span className="channel-icon">
                              <Target size={18} />
                            </span>
                            <div>
                              <strong>{c.name}</strong>
                              <small>
                                {data.offers.find((o: Row) => o.id === c.offer_id)?.name} ·{' '}
                                {c.sequence.length} étapes · {c.daily_limit} emails / jour maximum
                              </small>
                            </div>
                            <Badge value={c.mode} />
                            <Badge value={c.status} />
                          </div>
                        ))
                      ) : (
                        <Empty title="Votre première campagne commence ici">
                          Définissez votre ICP, ajoutez des contacts puis préparez vos messages.
                        </Empty>
                      )}
                    </section>
                  </>
                )}
                {view === 'contacts' && (
                  <>
                    {entityView === 'companies' ? (
                      <CompaniesList
                        data={data}
                        api={api}
                        onOpen={open}
                        onContact={followContact}
                        onCompany={(event, id) => {
                          if (
                            event.metaKey ||
                            event.ctrlKey ||
                            event.shiftKey ||
                            event.altKey ||
                            event.button
                          )
                            return;
                          event.preventDefault();
                          listPosition.current = window.scrollY;
                          navigation.navigate('/companies/' + id);
                        }}
                      />
                    ) : (
                      <UnifiedProspects data={data} api={api} upload={upload} onContact={followContact} />
                    )}
                  </>
                )}
                {view === 'offers' && (
                  <OfferLibrary
                    offers={data.offers}
                    campaigns={data.campaigns}
                    onCampaign={(offer) => {
                      setView('campaigns');
                      open('campaignGroup', {offer_id: offer.id, name: offer.name});
                    }}
                  />
                )}
                {view === 'campaigns' && (
                  <>
                    <CampaignGroups
                      onSelectGroup={(id) => navigation.navigate('/campaigns/' + id)}
                      groups={data.groups}
                      campaigns={data.campaigns}
                      offers={data.offers}
                      icps={data.icps}
                      admin={admin}
                      busy={busy}
                      onAdd={(group) =>
                        open('campaign', {
                          group_id: group.id,
                          offer_id: group.offer_id,
                          niche: group.niche || '',
                        })
                      }
                      onVersion={(row) => open('campaign', row)}
                      onSourcing={(row) => open('sourcing', row)}
                      onEdit={(group) => open('campaignGroup', group)}
                      onAction={(path) => action(() => api(path, 'POST'))}
                      onResults={(scope) => {
                        setCohort(scope);
                        setView('dashboard');
                      }}
                    />
                    <details className="panel campaign-targets">
                      <summary>Profils cibles · {data.icps.length}</summary>
                      <div className="cards">
                        {data.icps.map((i: Row) => (
                          <section className="panel icp" key={i.id}>
                            <span className="eyebrow">ICP · VERSION {i.version}</span>
                            <h2>{i.name}</h2>
                            <p>{i.offer}</p>
                            <div className="tags">
                              {Object.entries(i.criteria).map(([key, val]) => (
                                <span key={key}>
                                  {{
                                    country: 'Pays',
                                    min_employees: 'Effectif min.',
                                    max_employees: 'Effectif max.',
                                    buyer_roles: 'Fonctions',
                                    process: 'Processus',
                                    crm: 'CRM',
                                    niche: 'Niche',
                                    niche_definition: 'Périmètre',
                                  }[key] || key}{' '}
                                  : {Array.isArray(val) ? val.join(', ') : String(val)}
                                </span>
                              ))}
                            </div>
                            <p>{i.notes}</p>
                            <small>Exclusions : {i.exclusions.join(' · ') || 'À préciser'}</small>
                          </section>
                        ))}
                      </div>
                    </details>
                  </>
                )}
                {view === 'tasks' && (
                  <>
                    <div className="entity-tabs" role="group" aria-label="Afficher les actions ou les emails">
                      <button aria-pressed={actionView === 'tasks'} onClick={() => setActionView('tasks')}>
                        Actions à réaliser
                      </button>
                      <button
                        aria-pressed={actionView === 'messages'}
                        onClick={() => setActionView('messages')}
                      >
                        Emails & historique
                      </button>
                    </div>
                    {actionView === 'tasks' ? (
                      <>
                        <TasksList data={data} api={api} onOpen={open} onContact={followContact} />
                        <details className="panel list-preparation">
                          <summary>Préparation automatique des campagnes</summary>
                          <Autopilot api={api} admin={admin} actionsOnly />
                        </details>
                      </>
                    ) : (
                      <MessagesList data={data} api={api} onOpen={open} onContact={followContact} />
                    )}
                  </>
                )}
                {view === 'pipeline' && (
                  <OpportunitiesList data={data} api={api} onOpen={open} onContact={followContact} />
                )}
                {view === 'senders' && (
                  <>
                    <SenderAccounts api={api} admin={admin} />
                    <details className="panel capacity-panel list-preparation">
                      <summary>
                        Cadence globale · {state.effective_daily_limit} emails maximum sur 24 h
                      </summary>
                      <form
                        className="padded"
                        onSubmit={form((f) =>
                          api('/state', 'PATCH', {daily_limit_mode: f.mode, daily_limit: Number(f.daily)}),
                        )}
                      >
                        <div className="form-grid">
                          <Field label="Calcul du plafond global">
                            <select
                              name="mode"
                              defaultValue={state.daily_limit_mode || 'manual'}
                              disabled={!admin}
                            >
                              <option value="mailboxes">Suivre les paliers des boîtes connectées</option>
                              <option value="manual">Plafond manuel commun</option>
                            </select>
                          </Field>
                          <Field label="Plafond manuel (si ce mode est choisi)">
                            <input
                              name="daily"
                              type="number"
                              min="1"
                              max="10000"
                              defaultValue={state.daily_limit}
                              required
                              disabled={!admin}
                            />
                          </Field>
                        </div>
                        <p>
                          Les limites de campagne, de domaine et de fournisseur continuent de s’appliquer. Une
                          pause ou une condition non remplie peut ramener la disponibilité à zéro.
                        </p>
                        {admin && <button disabled={busy}>Enregistrer la cadence globale</button>}
                      </form>
                    </details>
                  </>
                )}
                {view === 'settings' && (
                  <SettingsWorkspace
                    current={settingsTab}
                    onChange={(tab) => navigation.navigate('/settings?tab=' + tab)}
                  >
                    <div className="settings-content">
                      {settingsTab === 'warmup' && <NativeWarmup api={api} admin={admin} refresh={reload} />}
                      <div hidden={!['phone', 'notifications'].includes(settingsTab)}>
                        <RuntimePanel
                          api={api}
                          admin={admin}
                          section={settingsTab === 'notifications' ? 'notifications' : 'phone'}
                          senders={data.senders || data.sending_mailboxes || []}
                        />
                      </div>
                      {settingsTab === 'backup' && (
                        <section className="panel">
                          <header>
                            <h2>Sauvegarde de l’espace</h2>
                          </header>
                          <div className="padded">
                            <p>
                              Téléchargez une copie chiffrée de la base, à conserver hors de l’hébergement. La
                              restauration nécessite la clé de chiffrement du serveur.
                            </p>
                            <button
                              disabled={busy}
                              onClick={() => action(downloadBackup, 'Sauvegarde chiffrée téléchargée.')}
                            >
                              Télécharger une sauvegarde
                            </button>
                          </div>
                        </section>
                      )}
                      {settingsTab === 'budget' && (
                        <section className="panel">
                          <header>
                            <div>
                              <h2>Budget de l’espace</h2>
                              <p>Plafonds partagés par l’équipe</p>
                            </div>
                          </header>
                          <form
                            className="padded"
                            onSubmit={form((f) =>
                              api('/state', 'PATCH', {
                                monthly_limit_cents: Math.round(Number(f.budget) * 100),
                              }),
                            )}
                          >
                            <Field label="Budget mensuel maximal (€)">
                              <input
                                name="budget"
                                type="number"
                                min="0"
                                defaultValue={state.monthly_limit_cents / 100}
                                disabled={!admin}
                              />
                            </Field>
                            <p>La cadence des emails se règle dans Boîtes & cadence.</p>
                            <p>Dernier passage du worker : {date(state.worker_seen_at)}</p>
                            {admin && (
                              <button disabled={busy} className="primary">
                                Enregistrer les limites
                              </button>
                            )}
                          </form>
                        </section>
                      )}
                      <div hidden={settingsTab !== 'connections'}>
                        <Integrations api={api} admin={admin} />
                      </div>
                      {settingsTab === 'journal' && (
                        <>
                          <section className="panel">
                            <header>
                              <h2>Dépenses</h2>
                              {admin && <button onClick={() => open('expense')}>Ajouter</button>}
                            </header>
                            {data.expenses.length ? (
                              data.expenses.map((e: Row) => (
                                <div className="list-row" key={e.id}>
                                  <div>
                                    <strong>{e.note}</strong>
                                    <small>
                                      {e.category} · {e.status}
                                    </small>
                                  </div>
                                  <strong>{euro(e.amount_cents)}</strong>
                                </div>
                              ))
                            ) : (
                              <Empty title="Aucune dépense enregistrée">
                                Ajoutez vos abonnements et votre temps de prospection pour mesurer le coût
                                complet.
                              </Empty>
                            )}
                          </section>
                          <section className="panel">
                            <header>
                              <h2>Journal des opérations</h2>
                              {targetJob && (
                                <button onClick={() => navigation.navigate('/?view=settings&tab=operations')}>
                                  Tout le journal
                                </button>
                              )}
                            </header>
                            {journalJobs.length ? (
                              journalJobs.slice((journalPage - 1) * 10, journalPage * 10).map((j: Row) => (
                                <div className="list-row" key={j.id}>
                                  <div>
                                    <strong>{j.kind}</strong>
                                    <small>
                                      {date(j.created_at)}
                                      {j.error && ' · ' + j.error}
                                    </small>
                                  </div>
                                  <Badge value={j.status} />
                                  {admin &&
                                    ['uncertain', 'failed'].includes(j.status) &&
                                    (['send', 'reply'].includes(j.kind) ||
                                      (j.kind === 'enrich' && j.provider === 'fullenrich')) && (
                                      <button onClick={() => open('reconcile', j)}>Rapprocher</button>
                                    )}
                                </div>
                              ))
                            ) : (
                              <Empty title="Aucune opération">
                                Les traitements automatiques seront visibles ici.
                              </Empty>
                            )}
                            {journalJobs.length > 10 && (
                              <div className="ws-pagination padded">
                                <button
                                  disabled={journalPage <= 1}
                                  onClick={() => setJournalPage((value) => value - 1)}
                                >
                                  Précédent
                                </button>
                                <span>
                                  {journalPage} / {Math.ceil(journalJobs.length / 10)}
                                </span>
                                <button
                                  disabled={journalPage * 10 >= journalJobs.length}
                                  onClick={() => setJournalPage((value) => value + 1)}
                                >
                                  Suivant
                                </button>
                              </div>
                            )}
                          </section>
                        </>
                      )}
                    </div>
                  </SettingsWorkspace>
                )}
              </>
            )}
          </div>
        </main>
        {view === 'contacts' && new URLSearchParams(location.search).get('prospect') && (
          <ProspectDrawer
            id={new URLSearchParams(location.search).get('prospect')!}
            api={api}
            upload={upload}
            revision={data}
            onRefresh={reload}
            navigate={navigation.navigate}
            onDirty={navigation.setDirty}
            close={() => navigation.navigate('/contacts')}
            onOpen={open}
          />
        )}
        {navigation.pending && (
          <Modal title="Quitter la modification ?" className="contact-dialog" close={navigation.keepEditing}>
            <p className="cp-discard-copy">Les modifications en cours ne sont pas encore enregistrées.</p>
            <div className="cp-discard-actions">
              <button onClick={navigation.keepEditing}>Continuer la modification</button>
              <button className="primary" onClick={navigation.discardAndNavigate}>
                Quitter sans enregistrer
              </button>
            </div>
          </Modal>
        )}
        {modal && (
          <Modal
            className={view === 'contact' ? 'contact-dialog' : ''}
            title={
              {
                contact: 'Ajouter un contact',
                import: 'Importer des contacts',
                icp: 'Définir un ICP',
                campaign: 'Définir une sous-campagne',
                campaignGroup: 'Campagne principale',
                detail: 'Fiche contact',
                task: 'Traiter une action',
                opportunity: 'Nouvelle opportunité',
                editOpportunity: 'Suivi de l’opportunité',
                expense: 'Enregistrer une dépense',
                enrich: 'Enrichir les coordonnées',
                resume: 'Reprendre après revue',
                editMessage: 'Modifier le message',
                newTask: 'Planifier une action',
                evidence: 'Ajouter une preuve',
                discovery: 'Rechercher avec Apollo',
                retention: 'Conservation de la fiche',
                erase: 'Effacer une fiche',
                account: 'Modifier l’entreprise',
                reconcile: 'Rapprocher une opération',
                acquire: 'Récupérer des prospects',
                sourcing: 'Filtres de la collecte',
                preview: 'Aperçu de l’email complet',
                candidate: 'Revoir un candidat',
              }[modal] || modal
            }
            close={() => setModal('')}
          >
            {error && (
              <div className="alert error" role="alert">
                {error}
              </div>
            )}
            {modal === 'contact' && (
              <form
                onSubmit={form(async (f) => {
                  const account = await api('/accounts', 'POST', {
                    name: f.company,
                    domain: f.domain,
                    country: f.country,
                  });
                  await api('/contacts', 'POST', {
                    account_id: account.id,
                    first_name: f.first_name,
                    last_name: f.last_name,
                    email: f.email || null,
                    phone: f.phone,
                    role: f.role,
                    campaign_id: f.campaign_id || null,
                    sourcing_note: f.sourcing_note,
                    professional_relevance: f.professional_relevance,
                    source_url: f.source_url,
                  });
                })}
              >
                <div className="form-grid">
                  <MarketField value={selected?.account?.country || 'FR'} />
                  <Field label="Entreprise">
                    <input name="company" defaultValue={selected?.account?.name || ''} required />
                  </Field>
                  <Field label="Domaine de l’entreprise">
                    <input
                      name="domain"
                      defaultValue={selected?.account?.domain || ''}
                      placeholder="exemple.fr"
                      required
                    />
                  </Field>
                  <Field label="Prénom">
                    <input name="first_name" required />
                  </Field>
                  <Field label="Nom">
                    <input name="last_name" required />
                  </Field>
                  <Field label="Fonction">
                    <input name="role" />
                  </Field>
                  <Field label="Email professionnel">
                    <input name="email" type="email" />
                  </Field>
                  <Field label="Téléphone">
                    <input name="phone" type="tel" />
                  </Field>
                  <Field label="Campagne">
                    <select name="campaign_id">
                      <option value="">Sans campagne</option>
                      {data.campaigns.map((c: Row) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Field label="Source (URL)">
                  <input name="source_url" type="url" />
                </Field>
                <Field label="Provenance et date de vérification">
                  <textarea name="sourcing_note" />
                </Field>
                <Field label="Pourquoi votre offre concerne sa fonction">
                  <textarea name="professional_relevance" />
                </Field>
                <button disabled={busy} className="primary">
                  Créer la fiche
                </button>
              </form>
            )}
            {modal === 'import' && (
              <form
                onSubmit={async (event) => {
                  event.preventDefault();
                  const f = new FormData(event.currentTarget);
                  const file = f.get('csv') as File;
                  action(async () => {
                    const content = await file.text();
                    const response = await fetch(
                      '/api/import/csv' +
                        (f.get('campaign_id') ? '?campaign_id=' + f.get('campaign_id') : ''),
                      {
                        method: 'POST',
                        headers: {'X-CSRF-Token': csrf, 'Content-Type': 'text/csv'},
                        credentials: 'include',
                        body: content,
                      },
                    );
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.detail);
                    setSelected(result);
                  }, 'Import terminé. Consultez le bilan ci-dessous.');
                }}
              >
                <p>
                  CSV UTF-8, 1 000 lignes et 2 Mo maximum. Colonnes : company, domain, first_name, last_name,
                  email, phone, role, source_url, sourcing_note, professional_relevance.
                </p>
                <Field label="Fichier CSV">
                  <input type="file" name="csv" accept=".csv,text/csv" required />
                </Field>
                <Field label="Campagne">
                  <select name="campaign_id">
                    <option value="">Sans campagne</option>
                    {data.campaigns.map((c: Row) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {selected && (
                  <div className="callout">
                    {selected.created} créés · {selected.duplicates} doublons · {selected.errors?.length}{' '}
                    erreurs
                    {selected.errors?.map((e: Row) => (
                      <p key={e.line}>
                        Ligne {e.line} : {e.error}
                      </p>
                    ))}
                  </div>
                )}
                <button className="primary" disabled={busy}>
                  Importer
                </button>
              </form>
            )}
            {modal === 'icp' && (
              <ICPForm
                offers={data.offers}
                busy={busy}
                onSubmit={(payload) =>
                  action(async () => {
                    await api('/icps', 'POST', payload);
                    setModal('');
                  })
                }
              />
            )}
            {modal === 'campaignGroup' && (
              <CampaignGroupForm
                offers={data.offers}
                selected={selected}
                busy={busy}
                onSubmit={(payload) =>
                  action(async () => {
                    const saved = await api(
                      selected?.id ? '/campaign-groups/' + selected.id : '/campaign-groups',
                      selected?.id ? 'PATCH' : 'POST',
                      payload,
                    );
                    if (selected?.id) setModal('');
                    else open('campaign', {group_id: saved.id, offer_id: saved.offer_id});
                  })
                }
              />
            )}
            {modal === 'acquire' && <LeadAcquisition api={api} data={data} onRefresh={reload} />}
            {modal === 'sourcing' && selected && (
              <SourcingForm
                api={api}
                campaign={selected}
                onSaved={() => {
                  setModal('');
                  action(async () => {}, 'Filtres de recherche enregistrés.');
                }}
              />
            )}
            {modal === 'campaign' && (
              <CampaignForm
                capacity={state.effective_daily_limit || 0}
                offers={data.offers}
                icps={data.icps}
                groups={data.groups}
                selected={selected}
                busy={busy}
                onSubmit={(payload) =>
                  action(async () => {
                    await api(
                      selected?.id ? '/campaigns/' + selected.id + '/versions' : '/campaigns',
                      'POST',
                      payload,
                    );
                    setModal('');
                  })
                }
              />
            )}
            {modal === 'task' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/tasks/' + selected.id + '/complete', 'POST', {
                    outcome: f.outcome,
                    notes: f.notes,
                    next_at: f.next_at ? new Date(f.next_at).toISOString() : null,
                  }),
                )}
              >
                <h3>{selected.title}</h3>
                <p>
                  {selected.contact.first_name} {selected.contact.last_name} · {selected.contact.account.name}
                </p>
                <p>{selected.contact.professional_relevance || 'Pertinence à documenter'}</p>
                <small>
                  Numéro : {labels[selected.contact.phone_type] || selected.contact.phone_type} ·{' '}
                  {labels[selected.contact.phone_status] || selected.contact.phone_status}
                </small>
                <div className="checks">
                  {selected.contact.fit.checks.map((c: Row) => (
                    <div key={c.criterion}>
                      <span>{c.criterion}</span>
                      <strong>{labels[c.evidence] || c.evidence}</strong>
                    </div>
                  ))}
                </div>
                <p>
                  Emails transmis :{' '}
                  {selected.sent_count ??
                    data.messages.filter(
                      (m: Row) => m.contact_id === selected.contact_id && m.status === 'sent',
                    ).length}
                </p>
                {selected.channel === 'phone' && (
                  <p>
                    <a href={'tel:' + selected.contact.phone}>{selected.contact.phone}</a>
                  </p>
                )}
                {selected.channel === 'linkedin' && selected.contact.linkedin_url && (
                  <a href={selected.contact.linkedin_url} target="_blank" rel="noreferrer">
                    Ouvrir le profil LinkedIn
                  </a>
                )}
                <div className="callout preserve">{selected.notes || 'Aucune consigne supplémentaire.'}</div>
                <Field label="Résultat">
                  <select name="outcome">
                    <option value="done">Action réalisée</option>
                    <option value="not_reached">Non joint</option>
                    <option value="switchboard">Standard</option>
                    <option value="wrong_number">Mauvais numéro</option>
                    <option value="wrong_person">Mauvais interlocuteur</option>
                    <option value="callback">Rappel demandé</option>
                    <option value="no_need">Pas de besoin</option>
                    <option value="meeting">Rendez-vous confirmé</option>
                    <option value="stop">Ne plus contacter</option>
                  </select>
                </Field>
                <Field label="Date du rappel (si demandé)">
                  <input name="next_at" type="datetime-local" />
                </Field>
                <Field label="Compte rendu">
                  <textarea name="notes" required />
                </Field>
                <button className="primary" disabled={busy}>
                  Terminer l’action
                </button>
              </form>
            )}
            {modal === 'opportunity' && (
              <OpportunityForm
                initialContactId={selected?.contact?.id || selected?.contact_id || ''}
                contacts={
                  selected?.contact
                    ? [
                        selected.contact,
                        ...data.contacts.filter((row: Row) => row.id !== selected.contact.id),
                      ]
                    : data.contacts
                }
                campaigns={data.campaigns}
                offers={data.offers}
                busy={busy}
                onSubmit={(payload) =>
                  action(async () => {
                    await api('/opportunities', 'POST', payload);
                    setModal('');
                  })
                }
              />
            )}
            {modal === 'editOpportunity' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/opportunities/' + selected.id, 'PATCH', {
                    stage: f.stage,
                    deposit_cents: Math.round(Number(f.deposit) * 100),
                    next_action: f.next_action,
                    meeting_at: f.meeting_at ? new Date(f.meeting_at).toISOString() : null,
                    next_action_at: f.next_action_at ? new Date(f.next_action_at).toISOString() : null,
                    meeting_held: f.meeting_held === 'on',
                    qualified: f.qualified === 'on',
                    lost_reason: f.lost_reason,
                  }),
                )}
              >
                <h3>{selected.title}</h3>
                <Field label="Étape">
                  <select name="stage" defaultValue={selected.stage}>
                    {['conversation', 'meeting', 'proposal', 'won', 'lost'].map((x) => (
                      <option key={x} value={x}>
                        {labels[x]}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Acompte reçu (€)">
                  <input type="number" name="deposit" min="0" defaultValue={selected.deposit_cents / 100} />
                </Field>
                <label className="checkbox">
                  <input type="checkbox" name="meeting_held" defaultChecked={selected.meeting_held} />
                  Rendez-vous tenu
                </label>
                <label className="checkbox">
                  <input type="checkbox" name="qualified" defaultChecked={selected.qualified} />
                  Opportunité qualifiée
                </label>
                <Field label="Prochaine action">
                  <textarea name="next_action" defaultValue={selected.next_action} />
                </Field>
                <div className="form-grid">
                  <Field label="Date du rendez-vous">
                    <input
                      type="datetime-local"
                      name="meeting_at"
                      defaultValue={
                        selected.meeting_at
                          ? new Date(
                              new Date(selected.meeting_at).getTime() -
                                new Date(selected.meeting_at).getTimezoneOffset() * 60000,
                            )
                              .toISOString()
                              .slice(0, 16)
                          : ''
                      }
                    />
                  </Field>
                  <Field label="Date de prochaine action">
                    <input
                      type="datetime-local"
                      name="next_action_at"
                      defaultValue={
                        selected.next_action_at
                          ? new Date(
                              new Date(selected.next_action_at).getTime() -
                                new Date(selected.next_action_at).getTimezoneOffset() * 60000,
                            )
                              .toISOString()
                              .slice(0, 16)
                          : ''
                      }
                    />
                  </Field>
                </div>
                <Field label="Raison de perte (si perdue)">
                  <textarea name="lost_reason" defaultValue={selected.lost_reason} />
                </Field>
                <button className="primary" disabled={busy}>
                  Enregistrer
                </button>
              </form>
            )}
            {modal === 'expense' && (
              <form
                onSubmit={form((f) =>
                  api('/expenses', 'POST', {
                    category: f.category,
                    amount_cents: Math.round(Number(f.amount) * 100),
                    note: f.note,
                    campaign_id: f.campaign_id || null,
                  }),
                )}
              >
                <Field label="Catégorie">
                  <select name="category">
                    {[
                      'enrichment',
                      'software',
                      'mailbox',
                      'hosting',
                      'phone',
                      'labor',
                      'commission',
                      'other',
                    ].map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Montant (€)">
                  <input name="amount" type="number" step="0.01" min="0" required />
                </Field>
                <Field label="Campagne">
                  <select name="campaign_id">
                    <option value="">Dépense partagée</option>
                    {data.campaigns.map((c: Row) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Description">
                  <textarea name="note" required />
                </Field>
                <button disabled={busy} className="primary">
                  Enregistrer
                </button>
              </form>
            )}
            {modal === 'editMessage' && selected && (
              <form onSubmit={form((f) => api('/messages/' + selected.id, 'PATCH', f))}>
                <Field label="Objet">
                  <input name="subject" defaultValue={selected.subject} required />
                </Field>
                <Field label="Message">
                  <textarea rows={8} name="body" defaultValue={selected.body} required />
                </Field>
                <p>La modification impose une nouvelle validation.</p>
                <button disabled={busy} className="primary">
                  Enregistrer le brouillon
                </button>
              </form>
            )}
            {modal === 'candidate' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/discoveries/' + selected.job_id + '/accept', 'POST', {...f, index: selected.index}),
                )}
              >
                <p>
                  Vérifiez l’identité, le domaine et la fonction. La fiche restera à qualifier et n’aura
                  aucune coordonnée marquée comme vérifiée.
                </p>
                <Field label="Entreprise">
                  <input name="company" defaultValue={selected.company} required />
                </Field>
                <Field label="Domaine">
                  <input name="domain" defaultValue={selected.domain} required />
                </Field>
                <div className="form-grid">
                  <Field label="Prénom">
                    <input name="first_name" defaultValue={selected.first_name} />
                  </Field>
                  <Field label="Nom complet vérifié">
                    <input name="last_name" defaultValue={selected.last_name} />
                  </Field>
                </div>
                <Field label="Fonction">
                  <input name="role" defaultValue={selected.role} />
                </Field>
                <Field label="Profil LinkedIn">
                  <input name="linkedin_url" type="url" defaultValue={selected.linkedin_url} />
                </Field>
                <Field label="Source et vérification effectuée">
                  <textarea name="review_note" minLength={10} required />
                </Field>
                <button disabled={busy} className="primary">
                  Ajouter après revue
                </button>
              </form>
            )}
            {modal === 'preview' && selected && (
              <section className="message-preview">
                <h3>{selected.subject}</h3>
                {selected.from_email && (
                  <small>
                    De : {selected.from_email} · Réponse : {selected.reply_to}
                  </small>
                )}
                {!selected.complete && <p className="callout">{selected.note}</p>}
                <p>{selected.body}</p>
              </section>
            )}
            {modal === 'reconcile' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/jobs/' + selected.id + '/reconcile', 'POST', {
                    ...f,
                    external_id: f.external_id || null,
                  }),
                )}
              >
                <p>
                  Vérifiez d’abord le journal du fournisseur. Une issue incertaine ne doit pas déclencher une
                  répétition automatique.
                </p>
                <Field label="Résultat vérifié">
                  <select name="outcome">
                    {['send', 'reply'].includes(selected.kind) ? (
                      <>
                        <option value="accepted">SMTP a accepté l’email</option>
                        <option value="not_sent">Email non transmis</option>
                      </>
                    ) : (
                      <option value="received">Identifiant FullEnrich retrouvé</option>
                    )}
                  </select>
                </Field>
                {!['send', 'reply'].includes(selected.kind) && (
                  <Field label="Identifiant externe">
                    <input name="external_id" required />
                  </Field>
                )}
                <Field label="Preuve du rapprochement">
                  <textarea name="evidence" minLength={15} required />
                </Field>
                <button disabled={busy} className="primary">
                  Enregistrer le résultat vérifié
                </button>
              </form>
            )}
            {modal === 'account' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/accounts/' + selected.id, 'PUT', {
                    ...f,
                    siren: f.siren || null,
                    employee_count: f.employee_count ? Number(f.employee_count) : null,
                  }),
                )}
              >
                <Field label="Entreprise">
                  <input name="name" defaultValue={selected.name} required />
                </Field>
                <Field label="Domaine">
                  <input name="domain" defaultValue={selected.domain} required />
                </Field>
                <div className="form-grid">
                  <Field label="SIREN">
                    <input name="siren" defaultValue={selected.siren || ''} pattern="[0-9]{9}" />
                  </Field>
                  <Field label="Pays (code ISO)">
                    <input name="country" defaultValue={selected.country} pattern="[A-Z]{2}" required />
                  </Field>
                  <Field label="Secteur">
                    <input name="industry" defaultValue={selected.industry} />
                  </Field>
                  <Field label="Effectif">
                    <input
                      name="employee_count"
                      type="number"
                      min="0"
                      defaultValue={selected.employee_count ?? ''}
                    />
                  </Field>
                  <Field label="HubSpot commercial">
                    <select name="hubspot_status" defaultValue={selected.hubspot_status}>
                      <option value="unknown">À vérifier</option>
                      <option value="signal">Signal public</option>
                      <option value="confirmed">Confirmé en conversation</option>
                      <option value="absent">Absent</option>
                    </select>
                  </Field>
                  <Field label="Relation">
                    <select name="lifecycle" defaultValue={selected.lifecycle}>
                      <option value="prospect">Prospect</option>
                      <option value="customer">Client</option>
                      <option value="excluded">Exclu</option>
                    </select>
                  </Field>
                </div>
                <Field label="Notes et preuves">
                  <textarea name="notes" defaultValue={selected.notes} />
                </Field>
                <button className="primary" disabled={busy}>
                  Enregistrer et revoir les contacts
                </button>
              </form>
            )}
            {modal === 'retention' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/contacts/' + selected.id + '/retention', 'POST', {
                    retention_until: new Date(f.retention_until).toISOString(),
                    reason: f.reason,
                  }),
                )}
              >
                <p>
                  À cette échéance, les prises de contact seront bloquées jusqu’à une nouvelle revue.
                  L’effacement reste une action distincte.
                </p>
                <Field label="Échéance de conservation">
                  <input name="retention_until" type="datetime-local" required />
                </Field>
                <Field label="Motif et politique appliquée">
                  <textarea name="reason" minLength={10} required />
                </Field>
                <button disabled={busy} className="primary">
                  Enregistrer l’échéance
                </button>
              </form>
            )}
            {modal === 'erase' && selected && (
              <form
                onSubmit={form(async (f) => {
                  await api('/contacts/' + selected.id + '/erase', 'POST', f);
                  navigation.navigate('/contacts');
                })}
              >
                <p>
                  Cette action supprime la fiche, ses messages, ses tâches et son historique. Les identifiants
                  minimaux d’opposition restent conservés pour empêcher une nouvelle prospection. Les copies
                  externes, notes libres et sauvegardes se traitent séparément.
                </p>
                <Field label="Saisissez EFFACER pour confirmer">
                  <input name="confirmation" pattern="EFFACER" required />
                </Field>
                <button disabled={busy} className="primary">
                  Effacer cette fiche
                </button>
              </form>
            )}
            {modal === 'discovery' && (
              <form
                onSubmit={form((f) =>
                  api('/discoveries', 'POST', {
                    campaign_id: f.campaign_id,
                    kind: f.kind,
                    filters: {
                      organization_locations: [f.country],
                      ...(f.roles
                        ? {
                            person_titles: f.roles
                              .split(',')
                              .map((x: string) => x.trim())
                              .filter(Boolean),
                          }
                        : {}),
                    },
                    limit: Number(f.limit),
                    page: Number(f.page),
                  }),
                )}
              >
                <Field label="Campagne réelle et budget">
                  <select name="campaign_id" required>
                    {data.campaigns
                      .filter((c: Row) => c.mode === 'live')
                      .map((c: Row) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                  </select>
                </Field>
                <Field label="Recherche">
                  <select name="kind">
                    <option value="people">Décideurs</option>
                    <option value="companies">Entreprises</option>
                  </select>
                </Field>
                <Field label="Pays des entreprises">
                  <input name="country" defaultValue="France" required />
                </Field>
                <Field label="Fonctions, séparées par des virgules">
                  <input name="roles" placeholder="CEO, Sales Director" />
                </Field>
                <div className="form-grid">
                  <Field label="Résultats maximum">
                    <input name="limit" type="number" min="1" max="100" defaultValue="20" required />
                  </Field>
                  <Field label="Page">
                    <input name="page" type="number" min="1" max="500" defaultValue="1" required />
                  </Field>
                </div>
                <p>
                  Le plafond par opération Apollo sera réservé. Les résultats resteront à vérifier avant tout
                  ajout aux contacts ; un nom peut être partiellement masqué.
                </p>
                <button disabled={busy} className="primary">
                  Planifier la recherche
                </button>
              </form>
            )}
            {modal === 'newTask' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/tasks', 'POST', {
                    contact_id: selected.id,
                    channel: f.channel,
                    title: f.title,
                    due_at: new Date(f.due_at).toISOString(),
                    notes: f.notes,
                  }),
                )}
              >
                <Field label="Canal">
                  <select name="channel">
                    <option value="review">Suivi interne</option>
                    <option value="phone">Appel</option>
                    <option value="linkedin">LinkedIn manuel</option>
                  </select>
                </Field>
                <Field label="Action">
                  <input name="title" required />
                </Field>
                <Field label="Échéance">
                  <input name="due_at" type="datetime-local" required />
                </Field>
                <Field label="Consignes">
                  <textarea name="notes" />
                </Field>
                <button className="primary" disabled={busy}>
                  Planifier
                </button>
              </form>
            )}
            {modal === 'evidence' && selected && (
              <form onSubmit={form((f) => api('/contacts/' + selected.id + '/evidence', 'POST', f))}>
                <Field label="Fait documenté">
                  <input name="field" required placeholder="Ex. Usage commercial de HubSpot" />
                </Field>
                <Field label="Observation">
                  <textarea name="value" required />
                </Field>
                <Field label="Source">
                  <select name="source_type">
                    <option value="official_website">Site officiel</option>
                    <option value="conversation">Conversation</option>
                    <option value="manual">Recherche manuelle</option>
                  </select>
                </Field>
                <Field label="URL de la source">
                  <input name="source_url" type="url" />
                </Field>
                <Field label="Niveau de preuve">
                  <select name="confidence">
                    <option value="unverified">À vérifier</option>
                    <option value="signal">Signal public</option>
                    <option value="confirmed">Confirmé</option>
                  </select>
                </Field>
                <button className="primary" disabled={busy}>
                  Enregistrer la preuve
                </button>
              </form>
            )}
            {modal === 'resume' && selected && (
              <form onSubmit={form((f) => api('/contacts/' + selected.id + '/resume', 'POST', f))}>
                <p>
                  Les oppositions et opportunités actives bloquent la reprise. Les messages reviennent en
                  brouillon pour une nouvelle validation.
                </p>
                <Field label="Motif de reprise après vérification">
                  <textarea name="reason" minLength={10} required />
                </Field>
                <button disabled={busy} className="primary">
                  Reprendre la revue
                </button>
              </form>
            )}
            {modal === 'enrich' && selected && (
              <form
                onSubmit={form((f) =>
                  api('/enrichments', 'POST', {
                    contact_ids: selected.contact_ids || [selected.id],
                    provider: f.provider,
                    fields: f.fields.split(','),
                  }),
                )}
              >
                <p>
                  {selected.contact_ids
                    ? selected.contact_ids.length + ' fiches sélectionnées'
                    : selected.first_name + ' ' + selected.last_name + ' · ' + selected.account.name}
                </p>
                <Field label="Fournisseur">
                  <select name="provider">
                    <option value="fullenrich">FullEnrich</option>
                    <option value="apollo">Apollo (email)</option>
                    <option value="hunter">Hunter (vérifier un email existant)</option>
                  </select>
                </Field>
                <Field label="Coordonnées recherchées">
                  <select name="fields">
                    <option value="email,phone">Email et téléphone</option>
                    <option value="email">Email</option>
                    <option value="phone">Téléphone</option>
                  </select>
                </Field>
                <div className="callout">
                  Cette opération peut consommer des crédits. Le serveur vérifie les plafonds de campagne, de
                  fournisseur et du mois. Un budget validé et une campagne en mode réel sont nécessaires.
                </div>
                <button disabled={busy} className="primary">
                  Demander l’enrichissement
                </button>
              </form>
            )}
          </Modal>
        )}
      </WorkspaceShell>
    </ListPreferences>
  );
}
createRoot(document.getElementById('root')!).render(<App />);
