import {MissionPanel} from './MissionPanel';
import {useEffect, useState} from 'react';
import {
  ArrowUpRight,
  Check,
  CheckCircle2,
  CircleHelp,
  Pencil,
  Plus,
  Search,
  Target,
  Users,
  X,
} from 'lucide-react';
import {ContactsList} from './ListPages';
import {ListSummary, TableBadge} from './ListTable';
import {languages, markets} from './Locales';
import {
  WorkspaceHeader,
  WorkspaceTabs,
  Panel,
  Field,
  Feedback,
  Blank,
  Events,
  RouteLink,
  useRecord,
  money,
  personName,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {errorText} from './api';
const tabNames: Row = {
  overview: 'Vue d’ensemble',
  niches: 'Niches & cibles',
  contacts: 'Prospects',
  sequences: 'Séquences',
  budget: 'Budget',
  results: 'Résultats',
};
const statusNames: Row = {draft: 'Brouillon', active: 'Active', paused: 'En pause'};
type Props = {
  id: string;
  section: string;
  data: Row;
  api: Api;
  navigate: Navigate;
  onOpen: (name: string, row?: Row) => void;
  onRefresh: () => Promise<void>;
  admin: boolean;
  busy: boolean;
  onAction: (fn: () => Promise<unknown>, success?: string) => Promise<void>;
};
function AssignContacts({
  versions,
  api,
  onSaved,
  onClose,
}: {
  versions: Row[];
  api: Api;
  onSaved: () => Promise<void>;
  onClose: () => void;
}) {
  const [campaignId, setCampaignId] = useState(versions[0]?.id || ''),
    [query, setQuery] = useState(''),
    [rows, setRows] = useState<Row[]>([]),
    [selected, setSelected] = useState<Row[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      api('/lists/contacts?page_size=10&status=candidate&q=' + encodeURIComponent(query))
        .then((result) => {
          if (alive) {
            setRows(result.items);
            setError('');
          }
        })
        .catch((error) => {
          if (alive) setError(errorText(error));
        });
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, query]);
  return (
    <Panel
      title="Affecter des contacts"
      description="Les contacts concernés devront être validés pour cette sous-campagne."
      action={
        <button className="ws-icon" aria-label="Fermer l’affectation" disabled={busy} onClick={onClose}>
          <X size={16} />
        </button>
      }
    >
      <Feedback error={error} />
      <div className="ws-form-grid">
        <Field label="Version de destination">
          <select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}>
            {versions.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name} · v{row.config.sequence_version || 1}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Rechercher un contact à affecter">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Nom, email ou entreprise…"
          />
        </Field>
      </div>
      <ul className="ws-assignment-list">
        {rows.map((row) => (
          <li key={row.id}>
            <label>
              <input
                type="checkbox"
                checked={selected.some((item) => item.id === row.id)}
                disabled={busy || (!selected.some((item) => item.id === row.id) && selected.length >= 100)}
                onChange={(event) =>
                  setSelected((items) =>
                    event.target.checked ? [...items, row] : items.filter((item) => item.id !== row.id),
                  )
                }
              />
              <span>
                <strong>{personName(row)}</strong>
                <small>
                  {row.account.name} · {row.campaign?.name || 'Sans campagne'}
                </small>
              </span>
            </label>
          </li>
        ))}
      </ul>
      {!rows.length && <p>Aucun contact à qualifier ne correspond à cette recherche.</p>}
      <div className="ws-save-bar">
        <span>{selected.length} contact(s) sélectionné(s)</span>
        <button
          className="primary"
          disabled={busy || !campaignId || !selected.length}
          onClick={async () => {
            setBusy(true);
            setError('');
            try {
              await api('/campaigns/' + campaignId + '/contacts', 'POST', {
                contact_ids: selected.map((row) => row.id),
              });
              await onSaved();
              onClose();
            } catch (error) {
              setError(errorText(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Check size={15} />
          {busy ? 'Affectation…' : 'Affecter les contacts'}
        </button>
      </div>
    </Panel>
  );
}
export function CampaignPage({
  id,
  section,
  data,
  api,
  navigate,
  onOpen,
  onRefresh,
  admin,
  busy,
  onAction,
}: Props) {
  const loaded = useRecord('/workspaces/campaigns/' + id, api, data),
    row = loaded.record;
  const tab = tabNames[section] ? section : 'overview',
    base = '/campaigns/' + id;
  const [assign, setAssign] = useState(false),
    [notice, setNotice] = useState('');
  useEffect(() => {
    setAssign(false);
    setNotice('');
  }, [id, tab]);
  const name = row?.name;
  useEffect(() => {
    if (name) document.title = name + ' · Lumelia';
  }, [name]);
  const url = (key: string) => base + (key === 'overview' ? '' : '?tab=' + key);
  if (!row)
    return (
      <div className="ws-page">
        <WorkspaceHeader
          back="/?view=campaigns"
          backLabel="Toutes les campagnes"
          eyebrow="CAMPAGNE PRINCIPALE"
          title={loaded.loading ? 'Chargement de la campagne…' : 'Campagne indisponible'}
          navigate={navigate}
        />
        <Feedback error={loaded.error} />
        {loaded.error && <button onClick={loaded.refresh}>Réessayer</button>}
      </div>
    );
  const versions: Row[] = row.versions,
    metrics = row.metrics;
  const families = [...new Set(versions.map((version) => version.family_id || version.id))].map((id) =>
    versions
      .filter((version) => (version.family_id || version.id) === id)
      .sort((a, b) => (b.config.sequence_version || 1) - (a.config.sequence_version || 1)),
  );
  const latest = families.map((rows) => rows[0]),
    niches = [...new Set(versions.map((version) => version.niche))];
  const sequenceLink = (version: Row, label = 'Modifier la séquence') => (
    <RouteLink url={'/sequences/' + version.id} navigate={navigate} className="ws-button-link">
      {label}
      <ArrowUpRight size={14} />
    </RouteLink>
  );
  function resolve(version: Row, issue: Row) {
    if (issue.action === 'sequence') navigate('/sequences/' + version.id);
    else if (issue.action === 'contacts') {
      navigate(url('contacts'));
      setTimeout(() => setAssign(true), 0);
    } else if (issue.action === 'qualification')
      navigate('/qualification?resource=contacts&campaign=' + version.id);
    else navigate(url('budget'));
  }
  return (
    <article className="ws-page campaign-workspace">
      <WorkspaceHeader
        back="/?view=campaigns"
        backLabel="Toutes les campagnes"
        eyebrow={'CAMPAGNE · ' + (row.offer?.name || 'Offre')}
        title={row.name}
        subtitle={
          <>
            {niches.length} niche(s) · {families.length} sous-campagne(s) · {versions.length} version(s)
          </>
        }
        navigate={navigate}
        actions={
          admin && (
            <>
              <button onClick={() => onOpen('campaignGroup', row)}>
                <Pencil size={15} />
                Modifier
              </button>
              <button
                className="primary"
                onClick={() => onOpen('campaign', {group_id: id, offer_id: row.offer_id, niche: ''})}
              >
                <Plus size={15} />
                Ajouter une niche
              </button>
            </>
          )
        }
      />
      <WorkspaceTabs
        items={Object.entries(tabNames).map(([key, label]) => [key, String(label), url(key)])}
        current={tab}
        navigate={navigate}
        label="Rubriques de la campagne"
      />
      <Feedback error={loaded.error} notice={notice} />
      {tab === 'overview' && (
        <>
          <MissionPanel key={id} api={api} groupId={id} campaigns={latest} />
          <ListSummary
            label="Chiffres de la campagne"
            collapseMobile={false}
            items={[
              [metrics.contacts, 'contacts affectés'],
              [metrics.approved_contacts, 'contacts validés'],
              [metrics.replies, 'contacts ayant répondu'],
              [money(metrics.cost_cents), 'dépenses enregistrées'],
            ]}
          />
          <div className="ws-columns">
            <div>
              <Panel
                title="Préparation des sous-campagnes"
                description="État de la version la plus récente de chaque sous-campagne."
              >
                {latest.length ? (
                  <ul className="ws-readiness">
                    {latest.map((version) => (
                      <li key={version.id}>
                        <header>
                          <div>
                            <strong>{version.name}</strong>
                            <small>
                              {version.niche} · v{version.config.sequence_version || 1}
                            </small>
                          </div>
                          <TableBadge value={version.status}>{statusNames[version.status]}</TableBadge>
                        </header>
                        {version.readiness.length ? (
                          version.readiness.map((issue: Row) => (
                            <button key={issue.key} onClick={() => resolve(version, issue)}>
                              <CircleHelp size={16} />
                              <span>{issue.label}</span>
                              <ArrowUpRight size={14} />
                            </button>
                          ))
                        ) : (
                          <p>
                            <CheckCircle2 size={16} />
                            Ciblage, séquence et validation renseignés
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Blank title="Votre première niche commence ici">
                    Ajoutez une niche, puis définissez sa cible et sa séquence.
                  </Blank>
                )}
              </Panel>
              <Panel title="Dernière activité">
                <Events rows={row.events} />
              </Panel>
            </div>
            <div>
              <Panel title="Objectif de la campagne">
                <p className="ws-preserve">
                  {row.notes || 'Précisez l’objectif et les hypothèses à tester.'}
                </p>
                <dl className="ws-facts single">
                  <div>
                    <dt>Offre</dt>
                    <dd>{row.offer?.name}</dd>
                  </div>
                  <div>
                    <dt>Versions actives</dt>
                    <dd>
                      {versions.filter((version) => version.status === 'active').length} sur {versions.length}
                    </dd>
                  </div>
                  <div>
                    <dt>Cadence globale disponible</dt>
                    <dd>
                      {data.state.paused
                        ? 'Envois en pause'
                        : !data.state.live_actions_enabled
                          ? 'Envois réels désactivés'
                          : data.senders.reduce(
                              (sum: number, box: Row) => sum + (box.available_24h || 0),
                              0,
                            ) + ' emails maintenant'}
                    </dd>
                  </div>
                </dl>
                <RouteLink url="/?view=senders" navigate={navigate} className="ws-button-link">
                  Voir les boîtes et la cadence
                  <ArrowUpRight size={14} />
                </RouteLink>
              </Panel>
              <Panel title="Prochaine étape">
                <div className="ws-shortcuts">
                  <button onClick={() => navigate(url('contacts'))}>
                    <Users size={17} />
                    <span>
                      Examiner les prospects<small>Contacts, qualification et affectation</small>
                    </span>
                    <ArrowUpRight size={14} />
                  </button>
                  <button onClick={() => navigate(url('sequences'))}>
                    <Target size={17} />
                    <span>
                      Relire les séquences<small>Emails, appels et délais</small>
                    </span>
                    <ArrowUpRight size={14} />
                  </button>
                  <button onClick={() => onOpen('sourcing', latest[0])} disabled={!latest.length || !admin}>
                    <Search size={17} />
                    <span>
                      Configurer la recherche<small>Filtres et crédits de la sous-campagne</small>
                    </span>
                    <ArrowUpRight size={14} />
                  </button>
                </div>
              </Panel>
            </div>
          </div>
        </>
      )}
      {tab === 'niches' && (
        <>
          {niches.length ? (
            niches.map((niche) => (
              <Panel
                key={niche}
                title={niche || 'Ciblage à préciser'}
                action={
                  admin && (
                    <button onClick={() => onOpen('campaign', {group_id: id, offer_id: row.offer_id, niche})}>
                      <Plus size={14} />
                      Sous-campagne
                    </button>
                  )
                }
              >
                {families
                  .filter((rows) => rows[0].niche === niche)
                  .map((rows) => {
                    const version = rows[0],
                      profile = row.profiles.find((profile: Row) => profile.id === version.icp_id);
                    return (
                      <div className="ws-target" key={version.id}>
                        <header>
                          <h3>{version.name}</h3>
                          <span>{rows.length} version(s)</span>
                        </header>
                        <p>
                          {profile?.criteria?.niche_definition || profile?.notes || 'Périmètre à documenter.'}
                        </p>
                        <dl className="ws-facts">
                          <div>
                            <dt>Pays</dt>
                            <dd>
                              {markets[profile?.criteria?.country] ||
                                profile?.criteria?.country ||
                                'À définir'}
                            </dd>
                          </div>
                          <div>
                            <dt>Effectif cible</dt>
                            <dd>
                              {profile?.criteria?.min_employees == null &&
                              profile?.criteria?.max_employees == null
                                ? 'Non imposé'
                                : (profile.criteria.min_employees ?? 0) +
                                  ' à ' +
                                  (profile.criteria.max_employees ?? 'plus') +
                                  ' personnes'}
                            </dd>
                          </div>
                          <div>
                            <dt>CRM</dt>
                            <dd>{profile?.criteria?.crm || 'Non imposé'}</dd>
                          </div>
                          <div>
                            <dt>Fonctions</dt>
                            <dd>{profile?.criteria?.buyer_roles?.join(', ') || 'À préciser'}</dd>
                          </div>
                        </dl>
                        <p>
                          <strong>Exclusions :</strong>{' '}
                          {profile?.exclusions?.join(' · ') || 'Aucune exclusion spécifique renseignée.'}
                        </p>
                        <div className="actions">
                          {admin && (
                            <>
                              <button onClick={() => onOpen('campaign', version)}>
                                Créer une version avec un nouveau ciblage
                              </button>
                              <button onClick={() => onOpen('sourcing', version)}>
                                Recherche de contacts
                              </button>
                            </>
                          )}
                          {sequenceLink(version)}
                        </div>
                      </div>
                    );
                  })}
              </Panel>
            ))
          ) : (
            <Blank title="Aucune niche définie" />
          )}
        </>
      )}
      {tab === 'contacts' && (
        <>
          <div className="ws-section-actions">
            <p>{metrics.companies} entreprise(s) représentée(s) dans cette campagne.</p>
            {admin && (
              <button onClick={() => setAssign(!assign)} disabled={!versions.length}>
                <Plus size={15} />
                Affecter des contacts
              </button>
            )}
          </div>
          {assign && (
            <AssignContacts
              versions={versions}
              api={api}
              onClose={() => setAssign(false)}
              onSaved={async () => {
                await onRefresh();
                loaded.refresh();
                setNotice('Contacts affectés. Leur qualification est à valider.');
              }}
            />
          )}
          <ContactsList
            data={{...data, campaigns: versions}}
            api={api}
            onOpen={onOpen}
            onContact={(event, id) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button) return;
              event.preventDefault();
              navigate('/contacts/' + id);
            }}
            scope={{group_id: id}}
            preferenceKey={'contacts:group:' + id}
            admin={admin}
            busy={busy}
            onAction={onAction}
          />
        </>
      )}
      {tab === 'sequences' && (
        <>
          {versions.length ? (
            versions.map((version) => (
              <Panel
                key={version.id}
                title={version.name + ' · v' + (version.config.sequence_version || 1)}
                description={
                  version.niche +
                  ' · ' +
                  (languages[version.config.language] || 'Langue à définir') +
                  ' · ' +
                  version.timezone
                }
                action={sequenceLink(version, admin ? 'Ouvrir l’éditeur' : 'Voir la séquence')}
              >
                <div className="ws-sequence-overview">
                  {version.sequence.length ? (
                    version.sequence.map((step: Row, index: number) => (
                      <div key={index}>
                        <span>J+{step.day}</span>
                        <strong>
                          {{email: 'Email', phone: 'Appel', linkedin: 'LinkedIn'}[step.channel as string] ||
                            step.channel}
                        </strong>
                        <p>{step.subject || step.body.slice(0, 140)}</p>
                      </div>
                    ))
                  ) : (
                    <Blank title="Rédaction à préparer" />
                  )}
                </div>
                <div className="ws-version-footer">
                  <TableBadge value={version.status}>{statusNames[version.status]}</TableBadge>
                  <span>
                    {version.metrics.contacts} contact(s) · {version.metrics.sent} email(s) envoyé(s)
                  </span>
                  {admin && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        onAction(() =>
                          api(
                            '/campaigns/' +
                              version.id +
                              (version.status === 'active' ? '/pause' : '/activate'),
                            'POST',
                          ),
                        )
                      }
                    >
                      {version.status === 'active'
                        ? 'Suspendre cette version'
                        : version.mode === 'simulation'
                          ? 'Activer la simulation'
                          : 'Activer cette version'}
                    </button>
                  )}
                </div>
              </Panel>
            ))
          ) : (
            <Blank title="Aucune séquence">Créez une sous-campagne pour ouvrir son éditeur.</Blank>
          )}
        </>
      )}
      {tab === 'budget' && (
        <>
          <ListSummary
            label="Chiffres de la campagne"
            collapseMobile={false}
            items={[
              [money(metrics.budget_cents), 'budgets de toutes les versions'],
              [money(metrics.cost_cents), 'dépenses enregistrées'],
              [money(metrics.reserved_cents), 'budget réservé'],
              [money(metrics.deposits_cents), 'acomptes reçus'],
            ]}
          />
          <Panel
            title="Budget par version"
            description="Les frais partagés de l’espace ne sont pas répartis dans ces montants."
            action={
              admin && (
                <button onClick={() => onOpen('expense')}>
                  <Plus size={15} />
                  Ajouter une dépense
                </button>
              )
            }
          >
            <div className="ws-comparison">
              <table>
                <thead>
                  <tr>
                    <th>Sous-campagne</th>
                    <th>Budget</th>
                    <th>Dépenses</th>
                    <th>Réservé</th>
                    <th>Validation</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((version) => (
                    <tr key={version.id}>
                      <td>
                        <strong>{version.name}</strong>
                        <small>v{version.config.sequence_version || 1}</small>
                      </td>
                      <td>{money(version.budget_cents)}</td>
                      <td>{money(version.cost_cents)}</td>
                      <td>{money(version.reserved_cents)}</td>
                      <td>
                        {version.budget_approved ? (
                          <span className="ws-positive">Validé</span>
                        ) : admin ? (
                          <button
                            disabled={busy}
                            onClick={() =>
                              onAction(() => api('/campaigns/' + version.id + '/approve-budget', 'POST'))
                            }
                          >
                            Valider le budget
                          </button>
                        ) : (
                          'À valider'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!versions.length && <Blank title="Aucun budget de sous-campagne" />}
          </Panel>
        </>
      )}
      {tab === 'results' && (
        <>
          <div className="ws-section-actions">
            <RouteLink url={'/analytics?group=' + id} navigate={navigate} className="ws-button-link">
              Statistiques par étape et comparaison des versions
              <ArrowUpRight size={15} />
            </RouteLink>
          </div>
          <ListSummary
            label="Chiffres de la campagne"
            collapseMobile={false}
            items={[
              [metrics.sent, 'emails réellement envoyés'],
              [metrics.positive_replies, 'contacts intéressés'],
              [metrics.meetings_held, 'rendez-vous tenus'],
              [metrics.customers_won, 'entreprises gagnées'],
            ]}
          />
          <Panel
            title="Résultats par version"
            description="Résultats cumulés. Une personne compte une fois dans les réponses d’une version ; les simulations sont exclues des envois."
          >
            <div className="ws-comparison">
              <table>
                <thead>
                  <tr>
                    <th>Niche et version</th>
                    <th>Contacts</th>
                    <th>Emails envoyés</th>
                    <th>Réponses</th>
                    <th>RDV tenus</th>
                    <th>Entreprises gagnées</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((version) => (
                    <tr key={version.id}>
                      <td>
                        <strong>{version.niche}</strong>
                        <small>
                          {version.name} · v{version.config.sequence_version || 1}
                        </small>
                      </td>
                      <td>{version.metrics.contacts}</td>
                      <td>{version.metrics.sent}</td>
                      <td>{version.metrics.replies}</td>
                      <td>{version.metrics.meetings_held}</td>
                      <td>{version.metrics.customers_won}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!metrics.sent && (
              <p className="ws-context">Les résultats se rempliront avec l’activité réelle des campagnes.</p>
            )}
          </Panel>
        </>
      )}
    </article>
  );
}
