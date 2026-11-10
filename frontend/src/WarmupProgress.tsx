import {useState} from 'react';
import {errorText, type Api} from './api';

export type WarmupProgressData = {
  percent: number;
  complete: boolean;
  active_days: number;
  target_active_days: number;
  received: number;
  target_received: number;
  replies_received: number;
  elapsed_days: number;
  readiness: string;
  can_validate: boolean;
  plan_key: string;
  validation_reasons: string[];
  validation: null | {at: string; note: string; invalidated_at?: string; invalidated_reason?: string};
  checks: Record<string, {valid: boolean; outcome: string | null; observed_at: string | null}>;
};
const readiness: Record<string, string> = {
  not_started: 'Non démarrée',
  in_progress: 'Préparation en cours',
  checks_required: 'Contrôles à compléter',
  awaiting_validation: 'À valider',
  validated: 'Prête techniquement',
};

export function WarmupProgress({
  rowId,
  email,
  progress,
  admin,
  api,
  onChanged,
}: {
  rowId: string;
  email: string;
  progress?: WarmupProgressData;
  admin: boolean;
  api: Api;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false),
    [validating, setValidating] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  if (!progress) return <p className="nw-muted">Progression indisponible : actualisez la page.</p>;
  const p = progress;
  async function submit(path: string, method: string, payload: Record<string, unknown>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api('/mailbox-warmup/' + rowId + path, method, {...payload, expected_plan_key: p.plan_key});
      await onChanged();
      setEditing(false);
      setValidating(false);
      setNotice(
        path === '/plan'
          ? 'Objectif enregistré ; aucun volume quotidien augmenté.'
          : 'Préparation validée. Les campagnes restent soumises à leur propre autorisation.',
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="nw-progress">
      <div className="nw-progress-heading">
        <div>
          <strong>
            {p.percent.toLocaleString('fr-FR', {maximumFractionDigits: 1})}
            <span> %</span>
          </strong>
          <span> du programme de préparation</span>
        </div>
        <span className={'badge ' + (p.readiness === 'validated' ? 'active' : 'draft')}>
          {readiness[p.readiness] || p.readiness}
        </span>
      </div>
      <progress value={p.percent} max={100} aria-label={'Progression du warm-up de ' + email} />
      <div className="nw-progress-goals">
        <span>
          <strong>
            {p.active_days} / {p.target_active_days}
          </strong>{' '}
          journées actives
        </span>
        <span>
          <strong>
            {p.received} / {p.target_received}
          </strong>{' '}
          messages reçus, réponses comprises
        </span>
      </div>
      <p className="nw-muted">
        Le pourcentage suit le moins avancé des deux objectifs. Les jours sans réception ne comptent pas. 100
        % signifie « programme terminé », pas « réputation garantie ».
      </p>
      <details className="nw-validation">
        <summary>Conditions avant utilisation en cold email</summary>
        <ul>
          <li>{p.complete ? '✓' : '○'} Objectifs de durée et de réceptions atteints</li>
          <li>{p.readiness === 'validated' ? '✓' : '○'} Connexion saine et validation humaine enregistrée</li>
        </ul>
        {p.validation_reasons.length > 0 && <p>{p.validation_reasons.join(' · ')}</p>}
        {p.validation && (
          <p>
            Dernière revue : {new Date(p.validation.at).toLocaleString('fr-FR')}
            {p.validation.invalidated_at ? ' · à refaire' : ''}. {p.validation.note}
          </p>
        )}
        <p>
          La validation ne lève aucune pause et n’active aucun envoi. Les règles de campagne, les exclusions
          et les conditions du fournisseur restent applicables.
        </p>
      </details>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {admin && (
        <div className="actions nw-progress-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setEditing(!editing);
              setValidating(false);
            }}
          >
            Modifier l’objectif de {email}
          </button>
          {p.readiness !== 'validated' && (
            <button
              type="button"
              className="primary"
              disabled={busy || !p.can_validate}
              onClick={() => {
                setValidating(!validating);
                setEditing(false);
              }}
            >
              Valider la préparation de {email}
            </button>
          )}
        </div>
      )}
      {editing && (
        <form
          className="sender-policy"
          onSubmit={(event) => {
            event.preventDefault();
            const f = new FormData(event.currentTarget);
            submit('/plan', 'PATCH', {
              target_active_days: Number(f.get('days')),
              target_received: Number(f.get('received')),
            });
          }}
        >
          <p>
            Objectif interne de suivi, modifiable sans achat ni augmentation des plafonds. La durée réelle
            peut être plus longue si le quota ou les réceptions ralentissent les échanges.
          </p>
          <div className="form-grid">
            <label className="field">
              <span>Journées avec réception confirmée</span>
              <input
                name="days"
                type="number"
                min={7}
                max={90}
                defaultValue={p.target_active_days}
                required
              />
            </label>
            <label className="field">
              <span>Messages réellement reçus</span>
              <input
                name="received"
                type="number"
                min={10}
                max={5000}
                defaultValue={p.target_received}
                required
              />
            </label>
          </div>
          <button className="primary" disabled={busy}>
            Enregistrer l’objectif
          </button>
        </form>
      )}
      {validating && (
        <form
          className="sender-policy"
          onSubmit={(event) => {
            event.preventDefault();
            const f = new FormData(event.currentTarget);
            submit('/validate', 'POST', {note: f.get('note')});
          }}
        >
          <p>
            Cette revue porte sur la préparation technique, pas sur une garantie d’arrivée en boîte de
            réception.
          </p>
          <label className="field">
            <span>Note de validation des contrôles</span>
            <textarea name="note" minLength={20} maxLength={2000} required />
          </label>
          <button className="primary" disabled={busy || !p.can_validate}>
            Confirmer la validation technique
          </button>
        </form>
      )}
    </div>
  );
}
