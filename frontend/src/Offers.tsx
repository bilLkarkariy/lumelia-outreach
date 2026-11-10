import {SequenceComposer} from './SequenceEditor';
import {useRef, useState, type FormEvent, type ReactNode} from 'react';
import {ArrowLeft, ArrowUpRight} from 'lucide-react';
import {MarketField, languages, timezones} from './Locales';
import {OfferDirectory} from './ListDirectories';
import {type Row} from './api';

const statusLabels: Row = {
  primary: 'Prospection principale',
  network: 'Réseau',
  historical: 'Archivée',
  experimental: 'À tester',
};
const money = (cents: number) =>
  new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(
    cents / 100,
  );
const Field = ({label, children}: {label: string; children: ReactNode}) => (
  <label className="field">
    <span>{label}</span>
    {children}
  </label>
);

export function OfferLibrary({
  offers,
  campaigns,
  onCampaign,
}: {
  offers: Row[];
  campaigns: Row[];
  onCampaign: (offer: Row) => void;
}) {
  const [expanded, setExpanded] = useState('');
  if (!expanded)
    return (
      <OfferDirectory
        offers={offers}
        campaigns={campaigns}
        onSelect={(id) => {
          setExpanded(id);
          window.scrollTo({top: 0, behavior: 'instant'});
        }}
      />
    );
  return (
    <div className="offer-library">
      <button className="lt-detail-back" onClick={() => setExpanded('')}>
        <ArrowLeft size={16} />
        Toutes les offres
      </button>
      {offers
        .filter((offer) => offer.id === expanded)
        .map((offer) => {
          const brief = offer.brief;
          const count = campaigns.filter((c) => c.offer_id === offer.id).length;
          const open = expanded === offer.id;
          return (
            <section className="panel offer-card" key={offer.id}>
              <header>
                <div>
                  <span className={'badge offer-status ' + offer.status}>
                    {statusLabels[offer.status] || offer.status}
                  </span>
                  <h2>{offer.name}</h2>
                  <p>{offer.summary}</p>
                </div>
                <button
                  aria-expanded={open}
                  aria-controls={'offer-' + offer.id}
                  onClick={() => setExpanded(open ? '' : offer.id)}
                >
                  {open ? 'Réduire' : 'Voir l’offre'}
                </button>
              </header>
              <div className="offer-overview">
                <div>
                  <small>DÉLAI</small>
                  <strong>{brief.duration || 'À définir'}</strong>
                </div>
                <div>
                  <small>CAMPAGNES</small>
                  <strong>{count}</strong>
                </div>
                <div className="offer-use">
                  <p>{brief.usage}</p>
                </div>
              </div>
              {open && (
                <div className="offer-detail padded" id={'offer-' + offer.id}>
                  <div className="form-grid">
                    <div>
                      <h3>Pour qui</h3>
                      <p>{brief.audience}</p>
                      <p>Interlocuteur : {brief.buyer}</p>
                    </div>
                    <div>
                      <h3>Livrables</h3>
                      <ul>
                        {brief.deliverables?.map((item: string) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                  <h3>Périmètre</h3>
                  <ul>
                    {brief.boundaries?.map((item: string) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                  <h3>Tarifs internes · HT</h3>
                  <div className="offer-pricing">
                    {offer.pricing.map((tier: Row) => (
                      <div key={tier.name}>
                        <div>
                          <strong>{tier.name}</strong>
                          <small>{tier.note}</small>
                        </div>
                        <b>
                          {money(tier.min_cents)}
                          {tier.max_cents !== tier.min_cents ? ' à ' + money(tier.max_cents) : ''}
                          {tier.period === 'month' ? ' / mois' : ''}
                        </b>
                      </div>
                    ))}
                  </div>
                  <p className="offer-source">
                    Source : {offer.source.title}
                    <br />
                    <small>{offer.source.reference}</small>
                  </p>
                </div>
              )}
              {offer.status !== 'historical' && (
                <footer>
                  <span>{brief.buyer}</span>
                  <button onClick={() => onCampaign(offer)}>
                    {offer.status === 'network' ? 'Créer un suivi réseau' : 'Créer une campagne'}
                    <ArrowUpRight size={15} />
                  </button>
                </footer>
              )}
            </section>
          );
        })}
    </div>
  );
}

export function CampaignForm({
  offers,
  icps,
  groups,
  selected,
  busy,
  capacity,
  onSubmit,
}: {
  capacity: number;
  offers: Row[];
  icps: Row[];
  groups: Row[];
  selected: Row | null;
  busy: boolean;
  onSubmit: (payload: Row) => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const [stage, setStage] = useState(0),
    [summary, setSummary] = useState<Row>({});
  const stages = ['Cible', 'Séquence', 'Cadence', 'Vérification'];
  function validate(index: number) {
    const inputs = formRef.current?.querySelectorAll<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >(
      `fieldset[data-stage="${index}"] input, fieldset[data-stage="${index}"] select, fieldset[data-stage="${index}"] textarea`,
    );
    const invalid = Array.from(inputs || []).find((input) => !input.checkValidity());
    if (invalid) {
      setStage(index);
      requestAnimationFrame(() => {
        invalid.closest('details')?.setAttribute('open', '');
        invalid.reportValidity();
      });
      return false;
    }
    return true;
  }
  function go(next: number) {
    if (next > stage && !validate(stage)) return;
    if (formRef.current) setSummary(Object.fromEntries(new FormData(formRef.current)));
    setStage(next);
    requestAnimationFrame(() =>
      formRef.current?.querySelector('fieldset:not([hidden]) h3')?.scrollIntoView({block: 'nearest'}),
    );
  }
  const [offerId, setOfferId] = useState(selected?.offer_id || '');
  const [groupId, setGroupId] = useState(selected?.group_id || '');
  const [niche, setNiche] = useState(selected?.niche || '');
  const [dedicated, setDedicated] = useState(!selected?.id);
  const [icpId, setIcpId] = useState(
    selected?.icp_id ||
      icps.find(
        (i) =>
          i.offer_id === selected?.offer_id &&
          (selected?.niche ? i.criteria.niche === selected.niche : !i.criteria.niche),
      )?.id ||
      '',
  );
  const [steps, setSteps] = useState<Row[]>(selected?.sequence || []);
  const [later, setLater] = useState(!selected?.sequence?.length);
  const [language, setLanguage] = useState(selected?.config?.language || '');
  const [timezone, setTimezone] = useState(selected?.timezone || 'Europe/Paris');
  const [purpose, setPurpose] = useState(
    selected?.purpose ||
      (offers.find((o) => o.id === selected?.offer_id)?.status === 'network' ? 'network' : 'cold_outreach'),
  );
  const [changed, setChanged] = useState(false);
  const offer = offers.find((o) => o.id === offerId);
  const targets = icps.filter((i) => i.offer_id === offerId);
  const template = icps.find((i) => i.id === icpId);
  const criteria = template?.criteria || offer?.brief.criteria || {};
  function chooseOffer(value: string) {
    setOfferId(value);
    setGroupId(groups.find((g) => g.offer_id === value)?.id || '');
    setIcpId(icps.find((i) => i.offer_id === value)?.id || '');
    setPurpose(offers.find((o) => o.id === value)?.status === 'network' ? 'network' : 'cold_outreach');
    if (steps.length) {
      setSteps([]);
      setLater(true);
      setChanged(true);
    }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (stage < 3) {
      go(stage + 1);
      return;
    }
    for (let i = 0; i < 3; i++) if (!validate(i)) return;
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    const target = dedicated
      ? {
          target_profile: {
            name: String(fields.name).slice(0, 149) + ' — ciblage',
            offer_id: offerId,
            criteria: {
              country: fields.country,
              niche,
              niche_definition: fields.niche_definition,
              ...(fields.min ? {min_employees: Number(fields.min)} : {}),
              ...(fields.max ? {max_employees: Number(fields.max)} : {}),
              ...(fields.crm ? {crm: fields.crm} : {}),
              buyer_roles: String(fields.roles || '')
                .split(',')
                .map((x) => x.trim())
                .filter(Boolean),
              process: fields.process,
            },
            exclusions: template?.exclusions || [],
            notes: fields.target_notes || '',
          },
        }
      : {icp_id: icpId};
    onSubmit({
      name: fields.name,
      offer_id: offerId,
      ...target,
      group_id: groupId,
      niche,
      purpose,
      mode: fields.mode,
      transport: 'smtp',
      budget_cents: Math.round(Number(fields.budget) * 100),
      daily_limit: Number(fields.daily),
      language,
      localized_footer: fields.localized_footer || '',
      timezone,
      start_hour: Number(fields.start_hour),
      end_hour: Number(fields.end_hour),
      weekdays: selected?.weekdays || [0, 1, 2, 3, 4],
      sequence: later ? [] : steps,
    });
  }
  return (
    <form ref={formRef} className="campaign-wizard" onSubmit={submit} noValidate>
      <ol className="wizard-steps" aria-label="Étapes de création">
        {stages.map((label, index) => (
          <li key={label}>
            <button
              type="button"
              aria-current={stage === index ? 'step' : undefined}
              disabled={index > stage}
              onClick={() => go(index)}
            >
              <span>{index + 1}</span>
              {label}
            </button>
          </li>
        ))}
      </ol>
      <fieldset data-stage="0" hidden={stage !== 0}>
        <h3>Définir la cible</h3>
        <div className="wizard-context">
          <strong>{offer?.name || 'Offre à choisir'}</strong>
          <span>{groups.find((g) => g.id === groupId)?.name || 'Campagne principale à choisir'}</span>
        </div>
        <details className="wizard-options">
          <summary>Changer l’offre ou la campagne principale</summary>
          <Field label="Offre">
            <select value={offerId} onChange={(e) => chooseOffer(e.target.value)} required>
              <option value="">Choisir une offre</option>
              {offers.map((o) => (
                <option key={o.id} value={o.id} disabled={o.status === 'historical'}>
                  {o.name} · {statusLabels[o.status]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Campagne principale">
            <select value={groupId} onChange={(e) => setGroupId(e.target.value)} required>
              <option value="">Choisir la campagne principale</option>
              {groups
                .filter((g) => g.offer_id === offerId)
                .map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
            </select>
          </Field>
        </details>
        <Field label="Niche">
          <input
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            required
            maxLength={140}
            placeholder="Ex. Agences marketing B2B"
          />
        </Field>
        <Field label="Langue de la séquence">
          <select value={language} onChange={(e) => setLanguage(e.target.value)} required>
            <option value="">Choisir la langue de cette cible</option>
            {Object.entries(languages).map(([code, label]) => (
              <option key={code} value={code}>
                {String(label)}
              </option>
            ))}
          </select>
        </Field>
        <p className="muted">
          Séparez les sous-campagnes par marché, langue et fuseau d’envoi. La langue de chaque contact sera
          confirmée avant préparation.
        </p>
        <Field label="Nom de la sous-campagne">
          <input
            name="name"
            defaultValue={selected?.id ? selected.name + ' — nouvelle version' : ''}
            required
            maxLength={180}
            placeholder="Ex. Agences marketing · première approche"
          />
        </Field>
        <details className="wizard-options">
          <summary>Profil de départ et usage</summary>
          <Field label="Usage">
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              disabled={offer?.status === 'network'}
            >
              <option value="cold_outreach">Prospection</option>
              <option value="network">Suivi du réseau</option>
            </select>
          </Field>
          <Field label={dedicated ? 'Partir de ce profil' : 'Profil cible'}>
            <select
              value={icpId}
              onChange={(e) => setIcpId(e.target.value)}
              required={!dedicated}
              disabled={!offerId}
            >
              <option value="">Définir un nouveau profil</option>
              {targets.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name} · v{i.version}
                </option>
              ))}
            </select>
          </Field>
          <label className="checkbox">
            <input type="checkbox" checked={dedicated} onChange={(e) => setDedicated(e.target.checked)} />
            Définir un profil dédié à cette sous-campagne
          </label>
        </details>
        {dedicated && (
          <div key={offerId + icpId} className="niche-target">
            <Field label="Entreprises recherchées et limites de la niche">
              <textarea
                name="niche_definition"
                defaultValue={criteria.niche_definition || ''}
                required
                maxLength={2000}
                placeholder="Activité, clientèle, modèle de vente et cas à écarter"
              />
            </Field>
            <div className="form-grid">
              <MarketField
                value={criteria.country || 'FR'}
                onChange={(country) => setTimezone(timezones[country] || 'Europe/Paris')}
              />
              <Field label="CRM requis (facultatif)">
                <input name="crm" defaultValue={criteria.crm || ''} />
              </Field>
              <Field label="Effectif min. (facultatif)">
                <input name="min" type="number" min="0" defaultValue={criteria.min_employees ?? ''} />
              </Field>
              <Field label="Effectif max. (facultatif)">
                <input name="max" type="number" min="0" defaultValue={criteria.max_employees ?? ''} />
              </Field>
            </div>
            <details className="wizard-options">
              <summary>Fonctions, processus et hypothèses</summary>
              <Field label="Fonctions ciblées">
                <input name="roles" defaultValue={criteria.buyer_roles?.join(', ') || ''} />
              </Field>
              <Field label="Processus concerné">
                <input name="process" defaultValue={criteria.process || ''} />
              </Field>
              <Field label="Hypothèses à vérifier">
                <textarea name="target_notes" defaultValue={template?.notes || ''} />
              </Field>
            </details>
          </div>
        )}
        {offerId && !targets.length && <p>Définissez le profil cible de cette sous-campagne ci-dessus.</p>}
      </fieldset>
      <fieldset data-stage="1" hidden={stage !== 1}>
        {' '}
        <h3>Préparer la séquence</h3>
        <label className="checkbox">
          <input type="checkbox" checked={later} onChange={(e) => setLater(e.target.checked)} />
          Préparer la rédaction plus tard
        </label>
        {changed && <p>Une nouvelle offre a été choisie. Sa séquence reste à préparer.</p>}
        {!later && <SequenceComposer steps={steps} onChange={setSteps} disabled={busy} />}
        {!later && language && language !== 'fr' && (
          <Field label="Pied d’email dans la langue de la séquence : identité, motif et opposition">
            <textarea
              name="localized_footer"
              required
              minLength={20}
              maxLength={5000}
              defaultValue={selected?.config?.localized_footer || ''}
            />
          </Field>
        )}
      </fieldset>
      <fieldset data-stage="2" hidden={stage !== 2}>
        <h3>Fixer le budget et la cadence</h3>
        <div className="callout">
          Plafond actuel de l’espace : <strong>{capacity} emails sur 24 h</strong>. La limite de cette
          sous-campagne s’ajoute aux plafonds de chaque boîte, domaine et fournisseur.
        </div>
        <Field label="Mode">
          <select name="mode" defaultValue="simulation">
            <option value="simulation">Simulation</option>
            <option value="live">Réel</option>
          </select>
        </Field>
        <div className="form-grid">
          <Field label="Budget de prospection (€)">
            <input
              name="budget"
              type="number"
              step="0.01"
              min="0"
              defaultValue={selected?.budget_cents / 100 || 0}
              required
            />
          </Field>
          <Field label="Emails sur 24 heures maximum">
            <input
              name="daily"
              type="number"
              min="1"
              max="5000"
              defaultValue={selected?.daily_limit || 10}
              required
            />
          </Field>
        </div>
        <Field label="Fuseau horaire de la cible">
          <select value={timezone} onChange={(e) => setTimezone(e.target.value)} required>
            {Array.from(
              new Set([
                timezone,
                ...Object.values(timezones),
                'America/Chicago',
                'America/Denver',
                'America/Los_Angeles',
                'Pacific/Honolulu',
                'America/Anchorage',
              ]),
            ).map((zone) => (
              <option key={String(zone)} value={String(zone)}>
                {String(zone)}
              </option>
            ))}
          </select>
        </Field>
        <div className="form-grid">
          <Field label="Début d’envoi (heure locale)">
            <input
              name="start_hour"
              type="number"
              min="0"
              max="23"
              defaultValue={selected?.start_hour ?? 9}
              required
            />
          </Field>
          <Field label="Fin d’envoi (heure locale)">
            <input
              name="end_hour"
              type="number"
              min="1"
              max="24"
              defaultValue={selected?.end_hour ?? 18}
              required
            />
          </Field>
        </div>
        <p>
          La simulation permet de vérifier le parcours. Le mode réel reste soumis à une activation distincte
          et aux pauses de l’espace.
        </p>
      </fieldset>
      <fieldset data-stage="3" hidden={stage !== 3}>
        <h3>Vérifier avant de créer</h3>
        <dl className="review-grid">
          <dt>Offre</dt>
          <dd>{offer?.name}</dd>
          <dt>Niche</dt>
          <dd>{niche}</dd>
          <dt>Sous-campagne</dt>
          <dd>{summary.name}</dd>
          <dt>Profil</dt>
          <dd>
            {dedicated ? `${summary.country || 'FR'} · ${summary.niche_definition || ''}` : template?.name}
          </dd>
          <dt>Langue et horaire</dt>
          <dd>
            {languages[language]} · {timezone} · {summary.start_hour} h – {summary.end_hour} h
          </dd>
          <dt>Séquence</dt>
          <dd>
            {later ? 'Rédaction prévue plus tard' : `${steps.length} étape${steps.length > 1 ? 's' : ''}`}
          </dd>
          <dt>Mode et cadence</dt>
          <dd>
            {summary.mode === 'live' ? 'Réel' : 'Simulation'} · {summary.daily} emails / 24 h maximum
          </dd>
          <dt>Budget</dt>
          <dd>{summary.budget || '0'} €</dd>
        </dl>
        <div className="callout">
          La sous-campagne sera créée en brouillon. Vous pourrez ensuite configurer sa recherche de contacts
          et relire sa séquence.
        </div>
      </fieldset>
      <footer className="wizard-footer">
        <span>Étape {stage + 1} sur 4</span>
        <div className="actions">
          {stage > 0 && (
            <button type="button" disabled={busy} onClick={() => go(stage - 1)}>
              Retour
            </button>
          )}
          {stage < 3 ? (
            <button
              key="continue"
              type="button"
              className="primary"
              onClick={(event) => {
                event.preventDefault();
                go(stage + 1);
              }}
            >
              Continuer
            </button>
          ) : (
            <button
              key="create"
              type="submit"
              className="primary"
              disabled={busy || !offerId || !groupId || !niche.trim() || (!dedicated && !icpId)}
            >
              {selected?.id ? 'Créer la version' : 'Créer la sous-campagne'}
            </button>
          )}
        </div>
      </footer>
    </form>
  );
}

export function ICPForm({
  offers,
  busy,
  onSubmit,
}: {
  offers: Row[];
  busy: boolean;
  onSubmit: (payload: Row) => void;
}) {
  const [offerId, setOfferId] = useState('');
  const offer = offers.find((o) => o.id === offerId);
  const criteria = offer?.brief.criteria || {};
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.currentTarget));
    onSubmit({
      name: f.name,
      offer_id: offerId,
      criteria: {
        country: f.country,
        ...(f.min ? {min_employees: Number(f.min)} : {}),
        ...(f.max ? {max_employees: Number(f.max)} : {}),
        ...(f.crm ? {crm: f.crm} : {}),
        buyer_roles: String(f.roles)
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean),
        process: f.process,
      },
      exclusions: String(f.exclusions).split('\n').filter(Boolean),
      notes: f.notes,
    });
  }
  return (
    <form onSubmit={submit}>
      <Field label="Offre">
        <select value={offerId} onChange={(e) => setOfferId(e.target.value)} required>
          <option value="">Choisir une offre</option>
          {offers
            .filter((o) => o.status !== 'historical')
            .map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
        </select>
      </Field>
      <div key={offerId}>
        <Field label="Nom du profil cible">
          <input name="name" defaultValue={offer ? offer.name + ' — France' : ''} required maxLength={160} />
        </Field>
        <div className="form-grid">
          <MarketField value={criteria.country || 'FR'} />
          <Field label="CRM requis (facultatif)">
            <input name="crm" defaultValue={criteria.crm || ''} placeholder="Aucun CRM imposé" />
          </Field>
        </div>
        <div className="form-grid">
          <Field label="Effectif minimum (facultatif)">
            <input name="min" type="number" min="0" defaultValue={criteria.min_employees ?? ''} />
          </Field>
          <Field label="Effectif maximum (facultatif)">
            <input name="max" type="number" min="0" defaultValue={criteria.max_employees ?? ''} />
          </Field>
        </div>
        <Field label="Fonctions ciblées, séparées par une virgule">
          <input name="roles" defaultValue={criteria.buyer_roles?.join(', ') || ''} />
        </Field>
        <Field label="Processus concerné">
          <input name="process" defaultValue={criteria.process || ''} />
        </Field>
        <Field label="Exclusions, une par ligne">
          <textarea name="exclusions" defaultValue={'Client existant\nOpposition\nActivité B2C'} />
        </Field>
        <Field label="Hypothèses à vérifier">
          <textarea name="notes" defaultValue={offer?.brief.usage || ''} />
        </Field>
      </div>
      <button className="primary" disabled={busy || !offerId}>
        Créer ce profil
      </button>
    </form>
  );
}

export function OpportunityForm({
  contacts,
  campaigns,
  offers,
  busy,
  onSubmit,
  initialContactId = '',
}: {
  contacts: Row[];
  campaigns: Row[];
  offers: Row[];
  busy: boolean;
  onSubmit: (payload: Row) => void;
  initialContactId?: string;
}) {
  const [contactId, setContactId] = useState(initialContactId);
  const contact = contacts.find((c) => c.id === contactId);
  const campaign = campaigns.find((c) => c.id === contact?.campaign_id);
  const offer = offers.find((o) => o.id === campaign?.offer_id);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const f = Object.fromEntries(new FormData(event.currentTarget));
        onSubmit({contact_id: contactId, title: f.title, amount_cents: Math.round(Number(f.amount) * 100)});
      }}
    >
      <Field label="Contact">
        <select value={contactId} onChange={(e) => setContactId(e.target.value)} required>
          <option value="">Choisir</option>
          {contacts.map((c) => (
            <option key={c.id} value={c.id}>
              {c.first_name} {c.last_name} · {c.account.name}
            </option>
          ))}
        </select>
      </Field>
      <div key={contactId}>
        <Field label="Titre">
          <input name="title" defaultValue={offer?.name || ''} required />
        </Field>
        <Field label="Montant HT (€)">
          <input
            name="amount"
            type="number"
            step="0.01"
            min="0"
            defaultValue={offer?.pricing?.[0]?.min_cents / 100 || 0}
            required
          />
        </Field>
      </div>
      <p>La création met les séquences de cette entreprise en pause.</p>
      <button className="primary" disabled={busy}>
        Créer l’opportunité
      </button>
    </form>
  );
}
