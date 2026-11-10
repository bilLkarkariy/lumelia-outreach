import {useEffect, useState} from 'react';
import {Play, Pause, Workflow} from 'lucide-react';
import {errorText, type Api, type Row} from './api';
const stages: Row = {
  discover: 'Entreprises',
  domain: 'Interlocuteurs',
  verify: 'Vérification email',
  email: 'Email prévu',
};
export function Autopilot({
  api,
  admin,
  actionsOnly = false,
  onConfigure,
}: {
  api: Api;
  admin: boolean;
  actionsOnly?: boolean;
  onConfigure?: () => void;
}) {
  const [data, setData] = useState<Row | null>(null),
    [sources, setSources] = useState<Row | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  async function load() {
    const [auto, source] = await Promise.all([api('/autopilot'), api('/sourcing')]);
    setData(auto);
    setSources(source);
  }
  useEffect(() => {
    let active = true;
    const refresh = () => {
      if (active && !document.hidden) load().catch((e) => setError(errorText(e)));
    };
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- polling starts once on mount; load is recreated on every render
  }, []);
  async function configure() {
    if (!data) return;
    setBusy(true);
    setError('');
    try {
      await api('/sourcing/control', 'POST', {enabled: !sources?.enabled});
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const queued: Row[] = sources?.next_actions || [];
  const grouped = Array.from(new Set(queued.map((r) => r.wait_reason || r.status))).map((reason) => ({
    reason,
    rows: queued.filter((r) => (r.wait_reason || r.status) === reason),
  }));
  const counts = sources?.messages || {};
  const configured: Row[] = sources?.campaigns.filter((c: Row) => c.configured) || [];
  return (
    <section className="panel autopilot-panel">
      <header>
        <div>
          <h2>
            <Workflow size={18} />
            {actionsOnly ? 'Actions de recherche' : 'Recherche de contacts'}
          </h2>
          <p>
            {!data
              ? 'Chargement…'
              : !sources?.enabled
                ? 'Préparation suspendue'
                : configured.some((c) => c.last_error)
                  ? 'Préparation en attente · voir les conditions'
                  : 'Préparation activée'}
          </p>
        </div>
        {admin && !actionsOnly && (
          <button disabled={busy || !data} onClick={configure}>
            {sources?.enabled ? <Pause size={16} /> : <Play size={16} />}{' '}
            {sources?.enabled ? 'Suspendre la recherche' : 'Autoriser la recherche'}
          </button>
        )}
      </header>
      <div className="padded">
        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}
        {!data || !sources ? (
          <p>Chargement du suivi…</p>
        ) : (
          <>
            {!actionsOnly && (
              <>
                <div className="sender-counters">
                  <span>
                    <strong>{counts.drafts}</strong> brouillons actuels
                  </span>
                  <span>
                    <strong>{counts.validated}</strong> messages validés ou planifiés
                  </span>
                  <span>
                    <strong>{counts.cancelled}</strong> messages annulés
                  </span>
                  <span>
                    <strong>
                      {sources.credits_reserved_or_used} / {sources.free_credit_limit}
                    </strong>{' '}
                    crédits gratuits réservés ou estimés
                  </span>
                </div>
                <p>
                  {configured.filter((c) => c.enabled).length} sous-campagne(s) autorisée(s) à rechercher. Le
                  plafond de crédits est commun à toutes les niches.
                </p>
                <p>
                  Les nouvelles sous-campagnes préparent des fiches à qualifier. Configurez leurs secteurs et
                  mots-clés depuis leurs versions.
                </p>
                {onConfigure && <button onClick={onConfigure}>Configurer les recherches par niche</button>}
              </>
            )}
            <details open={actionsOnly}>
              <summary>Suivi et conditions de recherche · {queued.length} opération(s)</summary>
              {configured.map((c) => (
                <div className="list-row" key={c.campaign_id}>
                  <div>
                    <strong>{c.campaign_name}</strong>
                    <small>
                      {c.last_error ||
                        (!c.enabled
                          ? 'Recherche suspendue'
                          : 'Filtres enregistrés · en attente du prochain passage')}
                    </small>
                  </div>
                  <span>
                    {c.credits_reserved_or_used} / {c.free_credit_limit} crédits
                  </span>
                </div>
              ))}
              {grouped.map((g) => (
                <details key={g.reason}>
                  <summary>
                    {g.rows.length} opération(s) ·{' '}
                    {g.reason === 'queued' ? 'À traiter' : g.reason === 'running' ? 'En cours' : g.reason}
                  </summary>
                  {g.rows.map((r) => (
                    <div className="list-row" key={r.id}>
                      <div>
                        <strong>{stages[r.kind] || r.kind}</strong>
                        <small>{r.subject}</small>
                      </div>
                    </div>
                  ))}
                </details>
              ))}
              {!queued.length && (
                <p>
                  Aucune opération planifiée. La recherche dépend de ses filtres, de son plafond et des
                  crédits disponibles.
                </p>
              )}
            </details>
            <small>Ces crédits sont une estimation interne, pas le solde en temps réel du fournisseur.</small>
          </>
        )}
      </div>
    </section>
  );
}
