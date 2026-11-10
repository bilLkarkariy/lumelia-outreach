import {useEffect, useState, type FormEvent} from 'react';
import {ArrowLeft, Check, ChevronDown, RefreshCw, Settings2, ShieldCheck} from 'lucide-react';
import {ProviderLogo} from './ProviderLogo';
import {Feedback} from './WorkspaceUI';
import {errorText, type Api, type Row} from './api';
const providers = [
  {id: 'apify', name: 'Apify', purpose: 'Collecte de prospects'},
  {id: 'apollo', name: 'Apollo', purpose: 'Recherche de décideurs'},
  {id: 'fullenrich', name: 'FullEnrich', purpose: 'Emails et téléphones'},
  {id: 'hunter', name: 'Hunter', purpose: 'Vérification des emails'},
  {id: 'lemlist', name: 'lemlist', purpose: 'Accès aux campagnes'},
];
export function Integrations({api, admin}: {api: Api; admin: boolean}) {
  const [rows, setRows] = useState<Row[]>([]),
    [provider, setProvider] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [checking, setChecking] = useState(''),
    [notice, setNotice] = useState(''),
    [loading, setLoading] = useState(true);
  async function load() {
    if (admin) setRows(await api('/integrations'));
  }
  useEffect(() => {
    load()
      .catch((e) => setError(errorText(e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load only depends on admin
  }, [admin]);
  async function check(name: string) {
    setChecking(name);
    setError('');
    setNotice('');
    try {
      const r = await api('/integrations/' + name + '/check', 'POST');
      if (r.check?.error) throw new Error(r.check.error);
      if (!r.check?.authenticated) throw new Error('La clé n’a pas permis de s’authentifier.');
      setNotice('Connexion vérifiée et crédits actualisés.');
      await load();
    } catch (e) {
      setError(errorText(e));
      try {
        await load();
      } catch {
        /* Preserve the original provider error. */
      }
    } finally {
      setChecking('');
    }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api('/integrations/' + provider, 'POST', {
        config: {api_key: f.api_key},
        unit_cost_cents: Math.round(Number(f.unit || 0) * 100),
        monthly_limit_cents: Math.round(Number(f.limit || 0) * 100),
      });
      setProvider('');
      setNotice('Connexion enregistrée. Vous pouvez maintenant la vérifier.');
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const selected = providers.find((x) => x.id === provider);
  const connected = rows.filter(
    (x) => providers.some((p) => p.id === x.provider) && x.status === 'authenticated',
  ).length;
  if (!admin)
    return (
      <section className="settings-panel">
        <div className="settings-panel-body">
          <h2>Connexions</h2>
          <p>Les connexions sont gérées par l’administrateur.</p>
        </div>
      </section>
    );
  return (
    <section className="settings-panel integration-panel">
      <header className="settings-panel-heading">
        <div>
          <h2>{selected ? 'Configurer ' + selected.name : 'Services de prospection'}</h2>
          <p>
            {selected
              ? 'Renseignez la clé du compte à utiliser.'
              : 'Vos sources de prospects et de coordonnées, au même endroit.'}
          </p>
        </div>
        {selected ? (
          <button
            type="button"
            className="settings-quiet"
            onClick={() => {
              setProvider('');
              setError('');
            }}
          >
            <ArrowLeft size={15} />
            Services
          </button>
        ) : (
          !loading && (
            <span className="settings-count">
              {connected} / {providers.length} connectés
            </span>
          )
        )}
      </header>
      <Feedback error={error} notice={notice} />
      {loading ? (
        <p className="settings-panel-body" role="status">
          Chargement des connexions…
        </p>
      ) : selected ? (
        <form className="integration-config settings-panel-body" onSubmit={save}>
          <div className="integration-config-brand">
            <ProviderLogo provider={provider} />
            <div>
              <strong>{selected.name}</strong>
              <small>{selected.purpose}</small>
            </div>
          </div>
          <label className="field">
            <span>Clé API</span>
            <input
              name="api_key"
              type="password"
              required
              autoComplete="new-password"
              placeholder="Coller la clé du fournisseur"
            />
          </label>
          {provider === 'apify' ? (
            <p className="settings-help">
              Collecte limitée à 0,50 USD par run et 5 USD par mois. Aucun renouvellement automatique.
            </p>
          ) : (
            <div className="settings-form-pair">
              <label className="field">
                <span>Coût maximal par demande (€)</span>
                <input name="unit" type="number" min="0" step="any" required defaultValue="0" />
              </label>
              <label className="field">
                <span>Plafond mensuel (€)</span>
                <input name="limit" type="number" min="0" step="any" required defaultValue="0" />
              </label>
            </div>
          )}
          <p className="settings-help">
            La nouvelle clé remplace celle enregistrée. Les valeurs existantes ne sont jamais affichées.
          </p>
          <div className="settings-form-actions">
            <button type="button" onClick={() => setProvider('')} disabled={busy}>
              Annuler
            </button>
            <button className="primary" disabled={busy}>
              {busy ? 'Enregistrement…' : 'Enregistrer la connexion'}
            </button>
          </div>
        </form>
      ) : (
        <div className="integration-list">
          {providers.map(({id, name, purpose}) => {
            const row = rows.find((x) => x.provider === id),
              limited = Boolean(row?.state?.connection_error || row?.state?.limitation),
              ok = row?.status === 'authenticated';
            const state = !row
              ? 'off'
              : limited || row.status === 'error'
                ? 'warning'
                : ok
                  ? 'connected'
                  : 'pending';
            const stateName = !row
              ? 'Non connecté'
              : limited
                ? 'Accès limité'
                : row.status === 'error'
                  ? 'À vérifier'
                  : ok
                    ? 'Connecté'
                    : 'À vérifier';
            return (
              <article className="integration-item" key={id}>
                <div className="integration-summary">
                  <ProviderLogo provider={id} />
                  <div className="integration-identity">
                    <h3>{name}</h3>
                    <p>{purpose}</p>
                  </div>
                  <div className="integration-meta">
                    <span className={'connection-state ' + state}>
                      {ok && !limited ? <Check size={12} /> : <i />}
                      {stateName}
                    </span>
                    {row?.state?.quota && (
                      <strong className="integration-quota">{quotaSummary(id, row.state.quota)}</strong>
                    )}
                  </div>
                  <div className="integration-actions">
                    <button
                      type="button"
                      className="settings-quiet"
                      onClick={() => {
                        setProvider(id);
                        setError('');
                        setNotice('');
                      }}
                    >
                      <Settings2 size={14} />
                      {row ? 'Configurer' : 'Connecter'}
                    </button>
                    {row && (
                      <button
                        type="button"
                        className="settings-icon-button"
                        aria-label={'Vérifier ' + name}
                        title={'Vérifier ' + name}
                        disabled={Boolean(checking) || busy}
                        onClick={() => check(id)}
                      >
                        <RefreshCw size={15} className={checking === id ? 'settings-spinning' : ''} />
                      </button>
                    )}
                  </div>
                </div>
                {row && (
                  <details className="integration-detail">
                    <summary>
                      <span>
                        {limited
                          ? 'Voir la restriction'
                          : row.last_checked_at
                            ? 'Vérifié le ' + new Date(row.last_checked_at).toLocaleDateString('fr-FR')
                            : 'Détails du compte'}
                      </span>
                      <ChevronDown size={13} />
                    </summary>
                    <div>
                      {row.state?.connection_error && (
                        <p className="settings-warning">{row.state.connection_error}</p>
                      )}
                      {row.state?.limitation && <p className="settings-warning">{row.state.limitation}</p>}
                      {row.state?.quota && <Quota provider={id} value={row.state.quota} />}
                      <p>
                        La vérification actualise l’accès et les crédits sans lancer de collecte ni d’envoi.
                      </p>
                    </div>
                  </details>
                )}
              </article>
            );
          })}
        </div>
      )}
      <footer className="settings-panel-footer">
        <ShieldCheck size={15} />
        <span>Clés protégées. Les soldes sont partagés avec vos autres applications.</span>
      </footer>
    </section>
  );
}
function quotaSummary(provider: string, value: Row): string {
  if (provider === 'apify')
    return value.used_usd == null
      ? 'Solde non accessible'
      : new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'USD'}).format(Number(value.used_usd)) +
          ' utilisés';
  if (provider === 'apollo') {
    const credits = value.credit_usage_stats?.lead_credit?.left_over;
    return credits == null ? 'Crédits à vérifier' : credits + ' crédits email';
  }
  if (provider === 'hunter') {
    const credits = value.requests?.credits?.remaining;
    return credits == null ? 'Crédits à vérifier' : credits + ' crédits';
  }
  return value.credits == null ? 'Crédits à vérifier' : value.credits + ' crédits';
}
function Quota({provider, value}: {provider: string; value: Row}) {
  if (provider === 'apify') return <p>Collectes Lumelia : 0,50 USD/run · 5 USD/mois maximum.</p>;
  if (provider === 'hunter') {
    const r = value.requests || {};
    return (
      <>
        <p>
          {value.plan_name || 'Forfait à vérifier'}
          {r.searches &&
            ` · ${r.searches.remaining ?? '—'} recherches ou ${r.verifications?.remaining ?? '—'} vérifications`}
        </p>
        {value.reset_date && (
          <p>Renouvellement : {new Date(value.reset_date + 'T12:00:00').toLocaleDateString('fr-FR')}</p>
        )}
      </>
    );
  }
  if (provider === 'apollo') {
    const r = value.credit_usage_stats || {};
    return (
      <>
        <p>{r.direct_dial_credit?.left_over ?? '—'} crédits téléphone · les soldes ne s’additionnent pas.</p>
        {value.current_credit_cycle?.end_date && (
          <p>Fin du cycle : {new Date(value.current_credit_cycle.end_date).toLocaleDateString('fr-FR')}</p>
        )}
      </>
    );
  }
  if (provider === 'lemlist') return <p>Accès API selon le forfait du compte.</p>;
  return null;
}
