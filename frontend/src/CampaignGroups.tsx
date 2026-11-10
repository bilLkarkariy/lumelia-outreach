import {useState, type FormEvent} from 'react';
import {ArrowLeft, ChevronDown, Plus} from 'lucide-react';
import {languages, markets} from './Locales';
import {CampaignDirectory} from './ListDirectories';
import {type Row} from './api';

const money = (cents: number) =>
  new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(
    cents / 100,
  );
const labels: Row = {
  draft: 'Brouillon',
  active: 'Active',
  paused: 'En pause',
  simulation: 'Simulation',
  live: 'Réel',
  email: 'Email',
  phone: 'Appel',
  linkedin: 'LinkedIn',
};

export function CampaignGroupForm({
  offers,
  selected,
  busy,
  onSubmit,
}: {
  offers: Row[];
  selected: Row | null;
  busy: boolean;
  onSubmit: (payload: Row) => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(Object.fromEntries(new FormData(event.currentTarget)));
  }
  return (
    <form onSubmit={submit}>
      <p>
        Regroupez plusieurs niches autour d’une offre. Chaque sous-campagne garde son ciblage et sa séquence.
      </p>
      <label className="field">
        <span>Offre</span>
        <select
          name="offer_id"
          defaultValue={selected?.offer_id || ''}
          required
          aria-readonly={!!selected?.id}
        >
          <option value="">Choisir une offre</option>
          {offers
            .filter((o) => o.status !== 'historical')
            .map((o) => (
              <option key={o.id} value={o.id} disabled={!!selected?.id && selected.offer_id !== o.id}>
                {o.name}
              </option>
            ))}
        </select>
      </label>
      <label className="field">
        <span>Nom de la campagne principale</span>
        <input
          name="name"
          defaultValue={selected?.name || ''}
          required
          maxLength={180}
          placeholder="Ex. Devis · exploration des niches"
        />
      </label>
      <label className="field">
        <span>Objectif et notes</span>
        <textarea
          name="notes"
          defaultValue={selected?.notes || ''}
          maxLength={10000}
          placeholder="Ce que vous souhaitez comparer entre les niches"
        />
      </label>
      <button className="primary" disabled={busy}>
        {selected?.id ? 'Enregistrer' : 'Créer la campagne principale'}
      </button>
    </form>
  );
}

export function CampaignGroups({
  groups,
  campaigns,
  offers,
  icps,
  admin,
  busy,
  onAdd,
  onVersion,
  onEdit,
  onAction,
  onResults,
  onSourcing,
  onSelectGroup,
}: {
  groups: Row[];
  campaigns: Row[];
  offers: Row[];
  icps: Row[];
  admin: boolean;
  busy: boolean;
  onSelectGroup?: (id: string) => void;
  onAdd: (group: Row) => void;
  onVersion: (row: Row) => void;
  onEdit: (group: Row) => void;
  onAction: (path: string) => void;
  onResults: (scope: string) => void;
  onSourcing: (campaign: Row) => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selectedGroup, setSelectedGroup] = useState('');
  function family(row: Row) {
    if (row.family_id) return row.family_id;
    const seen = new Set<string>();
    let cursor = row;
    while (cursor.config?.parent_campaign_id && !seen.has(cursor.id)) {
      seen.add(cursor.id);
      const parent = campaigns.find(
        (c) => c.id === cursor.config.parent_campaign_id && c.group_id === row.group_id,
      );
      if (!parent) break;
      cursor = parent;
    }
    return cursor.id;
  }
  if (!selectedGroup || !groups.some((group) => group.id === selectedGroup))
    return (
      <CampaignDirectory
        groups={groups}
        campaigns={campaigns}
        offers={offers}
        onSelect={(id) => {
          if (onSelectGroup) {
            onSelectGroup(id);
            return;
          }
          setSelectedGroup(id);
          window.scrollTo({top: 0, behavior: 'instant'});
        }}
      />
    );
  return (
    <div className="campaign-groups">
      <button className="lt-detail-back" onClick={() => setSelectedGroup('')}>
        <ArrowLeft size={16} />
        Toutes les campagnes
      </button>
      {groups
        .filter((group) => group.id === selectedGroup)
        .map((group) => {
          const members = campaigns.filter((c) => c.group_id === group.id);
          const families = Array.from(new Set(members.map(family))).map((id) =>
            members
              .filter((c) => family(c) === id)
              .sort((a, b) => (b.config?.sequence_version || 1) - (a.config?.sequence_version || 1)),
          );
          const niches = Array.from(
            new Set(
              families.map((versions) =>
                (versions[0].niche || 'Ciblage à préciser').trim().toLocaleLowerCase('fr'),
              ),
            ),
          ).map((key) => ({
            key,
            families: families.filter(
              (versions) =>
                (versions[0].niche || 'Ciblage à préciser').trim().toLocaleLowerCase('fr') === key,
            ),
          }));
          const offer = offers.find((o) => o.id === group.offer_id);
          return (
            <section className="panel campaign-group" key={group.id}>
              <header>
                <div>
                  <span className="eyebrow">CAMPAGNE PRINCIPALE · {offer?.name}</span>
                  <h2>{group.name}</h2>
                  {group.notes && <p>{group.notes}</p>}
                </div>
                {admin && (
                  <button className="primary" onClick={() => onAdd(group)}>
                    <Plus size={16} />
                    Ajouter une niche
                  </button>
                )}
              </header>
              <div className="group-summary">
                <span>
                  <strong>{niches.length}</strong> niche{niches.length > 1 ? 's' : ''}
                </span>
                <span>
                  <strong>{families.length}</strong> sous-campagne{families.length > 1 ? 's' : ''}
                </span>
                <span>
                  <strong>{group.metrics?.companies || 0}</strong> entreprises
                </span>
                <span>
                  <strong>{group.metrics?.contacts || 0}</strong> contacts
                </span>
                <button onClick={() => onResults('group:' + group.id)}>Voir les résultats cumulés</button>
              </div>
              {!members.length && (
                <div className="padded">
                  <p>
                    Aucune sous-campagne. Ajoutez une niche pour définir sa cible et préparer sa séquence.
                  </p>
                </div>
              )}
              {niches.map((niche) => (
                <section className="niche-section" key={niche.key}>
                  <header className="niche-section-header">
                    <div>
                      <span className="eyebrow">NICHE</span>
                      <h3>{niche.families[0][0].niche || 'Ciblage à préciser'}</h3>
                      <small>
                        {niche.families.length} sous-campagne{niche.families.length > 1 ? 's' : ''}
                      </small>
                    </div>
                    {admin && (
                      <button onClick={() => onAdd({...group, niche: niche.families[0][0].niche})}>
                        <Plus size={16} />
                        Ajouter une sous-campagne
                      </button>
                    )}
                  </header>
                  {niche.families.map((versions) => {
                    const latest = versions[0],
                      root = family(latest),
                      open = expanded[root] ?? false;
                    const metrics = latest.family_metrics || {};
                    return (
                      <div className="niche-block" key={root}>
                        <button
                          className="niche-heading"
                          aria-expanded={open}
                          aria-controls={'niche-' + root}
                          onClick={() => setExpanded({...expanded, [root]: !open})}
                        >
                          <span>
                            <strong>{latest.name}</strong>
                            <small>
                              {versions.length} version{versions.length > 1 ? 's' : ''} ·{' '}
                              {icps.find((i) => i.id === latest.icp_id)?.name}
                            </small>
                          </span>
                          <span className="niche-metrics">
                            <span>
                              <b>{metrics.contacts}</b> contacts
                            </span>
                            <span>
                              <b>{metrics.sent}</b> emails
                            </span>
                            <span>
                              <b>{metrics.replies}</b> réponses
                            </span>
                            <span>
                              <b>{metrics.meetings_held}</b> RDV tenus
                            </span>
                          </span>
                          <ChevronDown size={18} className={open ? 'rotated' : ''} />
                        </button>
                        {open && (
                          <div id={'niche-' + root}>
                            {versions.map((c) => (
                              <section className="campaign niche-version" key={c.id}>
                                <header>
                                  <div>
                                    <h3>
                                      {c.name} · v{c.config?.sequence_version || 1}
                                    </h3>
                                    <p>
                                      {markets[icps.find((i) => i.id === c.icp_id)?.criteria.country] ||
                                        icps.find((i) => i.id === c.icp_id)?.criteria.country}{' '}
                                      · {languages[c.config?.language] || 'Langue à définir'} · {c.timezone} ·{' '}
                                      {c.start_hour} h – {c.end_hour} h · {c.daily_limit} emails / jour
                                    </p>
                                  </div>
                                  <span className={'badge ' + c.mode}>{labels[c.mode]}</span>
                                  <span className={'badge ' + c.status}>{labels[c.status] || c.status}</span>
                                </header>
                                <div className="sequence">
                                  {!c.sequence.length && <p>Rédaction à préparer</p>}
                                  {c.sequence.map((step: Row, index: number) => (
                                    <div key={index}>
                                      <span className="step-number">{index + 1}</span>
                                      <div>
                                        <strong>
                                          J+{step.day} · {labels[step.channel]}
                                        </strong>
                                        <p>{step.subject || step.body.slice(0, 100)}</p>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                                <footer>
                                  <span>
                                    Budget : {money(c.budget_cents)} ·{' '}
                                    {c.budget_approved ? 'validé' : 'à valider'}
                                  </span>
                                  <div className="actions">
                                    <button onClick={() => onResults(c.id)}>
                                      Résultats de cette version
                                    </button>
                                    {admin && (
                                      <>
                                        <button onClick={() => onSourcing(c)}>Recherche de contacts</button>
                                        <button onClick={() => onVersion(c)}>Nouvelle version</button>
                                        <button
                                          disabled={busy}
                                          onClick={() => onAction('/campaigns/' + c.id + '/approve-budget')}
                                        >
                                          Valider le budget
                                        </button>
                                        <button
                                          disabled={busy}
                                          onClick={() =>
                                            onAction(
                                              '/campaigns/' +
                                                c.id +
                                                (c.status === 'active' ? '/pause' : '/activate'),
                                            )
                                          }
                                        >
                                          {c.status === 'active'
                                            ? 'Pause'
                                            : c.mode === 'live'
                                              ? 'Activer les envois'
                                              : 'Activer la simulation'}
                                        </button>
                                      </>
                                    )}
                                  </div>
                                </footer>
                              </section>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </section>
              ))}
              {admin && (
                <footer>
                  <button onClick={() => onEdit(group)}>Modifier la campagne principale</button>
                  <button
                    disabled={busy || !members.some((c) => c.status === 'active')}
                    onClick={() => onAction('/campaign-groups/' + group.id + '/pause')}
                  >
                    Suspendre toutes les niches
                  </button>
                </footer>
              )}
            </section>
          );
        })}
    </div>
  );
}
