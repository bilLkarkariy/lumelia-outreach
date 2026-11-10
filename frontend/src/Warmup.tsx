import {useState} from 'react';
import {errorText, type Api, type Row} from './api';
const outcomes: Row = {inbox: 'En réception', spam: 'Dans les indésirables', missing: 'Non reçu'};
export function Warmup({
  api,
  admin,
  row,
  refresh,
}: {
  api: Api;
  admin: boolean;
  row: Row;
  refresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [editing, setEditing] = useState(false);
  async function run(path: string, payload?: Row) {
    setBusy(true);
    setError('');
    try {
      await api('/senders/' + row.id + path, 'POST', payload);
      await refresh();
      setEditing(false);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const ramp = row.ramp || {};
  return (
    <div className="mailbox-readiness">
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      <h4>Cadence des campagnes</h4>
      <p>
        Ce suivi concerne uniquement les emails de prospection, pas les tests de préparation gratuite
        ci-dessus.
      </p>
      <div className="sender-counters">
        <span>
          <strong>{ramp.accepted_7d || 0}</strong> emails acceptés par SMTP / 7 j
        </span>
        <span>
          <strong>{ramp.replies_7d || 0}</strong> interlocuteurs ayant répondu
        </span>
        <span>
          <strong>{(ramp.bounces_7d || 0) + (ramp.failed_7d || 0)}</strong> rejets ou erreurs / 7 j
        </span>
      </div>
      <div className="delivery-checks">
        {['gmail', 'outlook'].map((provider) => {
          const check = ramp.delivery_checks?.[provider];
          const fresh =
            check?.observed_at && Date.now() - new Date(check.observed_at).getTime() <= 7 * 86400000;
          return (
            <div key={provider}>
              <strong>{provider === 'gmail' ? 'Gmail' : 'Outlook'}</strong>
              <span className={'badge ' + (fresh && check.outcome === 'inbox' ? 'active' : 'draft')}>
                {check ? outcomes[check.outcome] : 'À tester'}
                {check && !fresh ? ' · à renouveler' : ''}
              </span>
              <small>
                {check
                  ? new Date(check.observed_at).toLocaleString('fr-FR', {
                      dateStyle: 'short',
                      timeStyle: 'short',
                    })
                  : 'Aucun résultat enregistré'}
              </small>
            </div>
          );
        })}
      </div>
      <p>
        {ramp.accepted_7d ? 'Activité observée' : 'En attente des premiers envois de campagne'} ·{' '}
        {ramp.healthy_days_in_step || 0} / 3 journées suffisamment actives et sans incident pour le prochain
        palier.
      </p>
      {ramp.cooldown_until && (
        <p className="error-text">
          Reprise au plus tôt le {new Date(ramp.cooldown_until).toLocaleString('fr-FR')}.
        </p>
      )}
      <details>
        <summary>Conditions de progression</summary>
        <p>
          Départ à 5 emails sur 24 h. Après trois journées suffisamment actives et sans incident, une hausse
          de 5 peut être revue. Les tests Gmail et Outlook doivent avoir été observés en réception depuis
          moins de sept jours. Chaque hausse nécessite ensuite une validation.
        </p>
        <p>
          Les jours sans envoi ne font pas progresser le plafond. Plus de sept jours d’inactivité le ramènent
          à 5. Les tests ponctuels ne garantissent pas la réception des prochains emails.
        </p>
      </details>
      {admin && (
        <div className="actions">
          <button disabled={busy} onClick={() => setEditing(!editing)}>
            {editing ? 'Fermer le résultat' : 'Enregistrer un test de réception'}
          </button>
          {!ramp.enabled && (
            <button disabled={busy} onClick={() => run('/ramp')}>
              Activer le suivi de cadence
            </button>
          )}
          {ramp.review_required && (
            <button
              disabled={busy || !ramp.external_checks_current || !!ramp.blockers?.length}
              onClick={() => run('/approve-ramp-step')}
            >
              Valider le palier de {ramp.next_limit} / 24 h
            </button>
          )}
        </div>
      )}
      {editing && (
        <form
          className="sender-policy"
          onSubmit={(event) => {
            event.preventDefault();
            const f = Object.fromEntries(new FormData(event.currentTarget));
            run('/delivery-check', {
              provider: f.provider,
              outcome: f.outcome,
              observed_at: new Date(String(f.observed_at)).toISOString(),
              note: f.note,
            });
          }}
        >
          <p>
            Renseignez le résultat d’un email réellement reçu ou recherché. Ce formulaire n’envoie aucun test.
          </p>
          <div className="form-grid">
            <label className="field">
              <span>Messagerie testée</span>
              <select name="provider">
                <option value="gmail">Gmail</option>
                <option value="outlook">Outlook</option>
              </select>
            </label>
            <label className="field">
              <span>Résultat observé</span>
              <select name="outcome" required defaultValue="">
                <option value="" disabled>
                  Choisir après vérification
                </option>
                <option value="inbox">Boîte de réception</option>
                <option value="spam">Indésirables</option>
                <option value="missing">Non reçu</option>
              </select>
            </label>
            <label className="field">
              <span>Date d’observation</span>
              <input name="observed_at" type="datetime-local" required />
            </label>
          </div>
          <label className="field">
            <span>Preuve : destinataire, objet ou identifiant et vérification</span>
            <textarea name="note" required minLength={10} maxLength={1500} />
          </label>
          <button className="primary" disabled={busy}>
            Enregistrer le résultat observé
          </button>
        </form>
      )}
    </div>
  );
}
