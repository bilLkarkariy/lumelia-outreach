import {useEffect, useState} from 'react';
import {errorText, type Api, type Row} from './api';
export function SourcingForm({api, campaign, onSaved}: {api: Api; campaign: Row; onSaved: () => void}) {
  const [data, setData] = useState<Row | null>(null),
    [current, setCurrent] = useState<Row | null>(null),
    [industries, setIndustries] = useState<string[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api('/sourcing')
      .then((d) => {
        if (!active) return;
        setData(d);
        const c = d.campaigns.find((x: Row) => x.campaign_id === campaign.id);
        setCurrent(c);
        setIndustries(c.industries);
      })
      .catch((e) => setError(errorText(e)));
    return () => {
      active = false;
    };
  }, [api, campaign.id]);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.currentTarget));
    const terms = (s: unknown) =>
      String(s || '')
        .split(',')
        .map((x) => x.trim())
        .filter(Boolean);
    setBusy(true);
    setError('');
    try {
      await api('/sourcing/' + campaign.id, 'PUT', {
        enabled: f.enabled === 'on',
        industries,
        keywords: terms(f.keywords),
        excluded_keywords: terms(f.excluded_keywords),
        free_credit_limit: Number(f.limit),
        contacts_per_day: Number(f.contacts),
      });
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit}>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {!data || !current ? (
        <p>Chargement des filtres…</p>
      ) : (
        <>
          <h3>{campaign.name}</h3>
          <p>
            {campaign.niche} · {current.profile.name}
          </p>
          <div className="callout">
            <strong>Recherche Hunter</strong>
            <p>
              Pays : {current.profile.criteria.country || 'FR'} · Effectif :{' '}
              {current.profile.criteria.min_employees ?? 'non limité'} à{' '}
              {current.profile.criteria.max_employees ?? 'non limité'}.
            </p>
            <small>
              Hunter utilise des tranches d’effectif. La taille exacte, le CRM, l’appartenance à la niche et
              le besoin restent à vérifier sur chaque fiche. La recherche retient actuellement les fonctions
              de direction et commerciales.
            </small>
          </div>
          <label className="field">
            <span>Secteurs d’activité (10 maximum)</span>
            <select
              value=""
              onChange={(e) => {
                if (e.target.value && !industries.includes(e.target.value))
                  setIndustries([...industries, e.target.value]);
              }}
              disabled={industries.length >= 10}
            >
              <option value="">Ajouter un secteur Hunter</option>
              {data.industries.map((i: string) => (
                <option key={i} value={i}>
                  {i}
                </option>
              ))}
            </select>
          </label>
          <div className="tags">
            {industries.map((i) => (
              <button
                type="button"
                key={i}
                onClick={() => setIndustries(industries.filter((x) => x !== i))}
                aria-label={'Retirer ' + i}
              >
                {i} ×
              </button>
            ))}
          </div>
          <label className="field">
            <span>Mots-clés inclus, séparés par des virgules</span>
            <input
              name="keywords"
              defaultValue={current.keywords.join(', ')}
              placeholder="Ex. marketing agency, conseil"
            />
          </label>
          <label className="field">
            <span>Mots-clés exclus, séparés par des virgules</span>
            <input name="excluded_keywords" defaultValue={current.excluded_keywords.join(', ')} />
          </label>
          <div className="form-grid">
            <label className="field">
              <span>Crédits gratuits maximum pour cette version</span>
              <input
                name="limit"
                type="number"
                min="0"
                max={data.free_credit_limit}
                defaultValue={current.free_credit_limit}
                required
              />
            </label>
            <label className="field">
              <span>Contacts à trouver maximum par jour</span>
              <input
                name="contacts"
                type="number"
                min="1"
                max="100"
                defaultValue={Math.min(current.contacts_per_day, 100)}
                required
              />
            </label>
          </div>
          <p>
            Cette version : {current.credits_reserved_or_used} crédit(s) réservé(s) ou estimé(s). Réserve
            commune : {data.credits_reserved_or_used} / {data.free_credit_limit}. Les plafonds s’appliquent
            ensemble.
          </p>
          <label className="checkbox">
            <input name="enabled" type="checkbox" defaultChecked={current.enabled} />
            Autoriser la recherche avec ces filtres
          </label>
          {!data.enabled && (
            <p className="callout">
              La recherche globale est suspendue. Les filtres seront enregistrés pour sa prochaine reprise.
            </p>
          )}
          <p>
            Les résultats restent à qualifier. Changer les filtres annule les recherches encore en file pour
            cette version.
          </p>
          <button className="primary" disabled={busy}>
            Enregistrer les filtres
          </button>
        </>
      )}
    </form>
  );
}
