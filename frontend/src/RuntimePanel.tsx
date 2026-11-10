import {useEffect, useState, type FormEvent} from 'react';
import {
  ArrowRight,
  Check,
  ChevronDown,
  FileAudio,
  Headphones,
  PhoneForwarded,
  ShieldCheck,
  Smartphone,
} from 'lucide-react';
import {IncomingCalls} from './IncomingCalls';
import {ProviderLogo} from './ProviderLogo';
import {Field, Feedback, useRecord, type Api, type Row} from './WorkspaceUI';
import './workflow.css';
import {errorText} from './api';
function phoneNumber(value: string = '') {
  return /^\+33\d{9}$/.test(value) ? ('0' + value.slice(3)).replace(/(\d{2})(?=\d)/g, '$1 ') : value;
}
export function RuntimePanel({
  api,
  senders,
  admin = false,
  section = 'phone',
}: {
  api: Api;
  senders: Row[];
  admin?: boolean;
  section?: 'phone' | 'notifications';
}) {
  const [revision, setRevision] = useState(0),
    [callRevision, setCallRevision] = useState(0),
    [recipient, setRecipient] = useState(''),
    [senderId, setSenderId] = useState('');
  const [enabled, setEnabled] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [inboundEnabled, setInboundEnabled] = useState(false),
    [routingBusy, setRoutingBusy] = useState(false);
  const runtime = useRecord('/runtime', api, revision),
    calls = useRecord('/calls/status', api, callRevision),
    mailboxes = useRecord('/senders', api, revision);
  const options = Array.isArray(mailboxes.record) ? mailboxes.record : senders;
  useEffect(() => {
    const value = runtime.record?.alerts;
    if (value) {
      setRecipient(value.recipient || '');
      setSenderId(value.sender_id || '');
      setEnabled(Boolean(value.enabled));
    }
  }, [runtime.record?.alerts]);
  useEffect(() => {
    if (typeof calls.record?.inbound_enabled === 'boolean') setInboundEnabled(calls.record.inbound_enabled);
  }, [calls.record?.inbound_enabled]);
  useEffect(() => {
    setError('');
    setNotice('');
  }, [section]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api('/runtime/alerts', 'PUT', {recipient, sender_id: senderId, enabled});
      setRevision((v) => v + 1);
      setNotice('Préférences de notification enregistrées.');
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function toggleRouting() {
    const next = !inboundEnabled;
    setRoutingBusy(true);
    setError('');
    setNotice('');
    try {
      await api('/calls/routing', 'POST', {enabled: next});
      setInboundEnabled(next);
      setCallRevision((v) => v + 1);
      setNotice(
        next
          ? 'Les appels entrants sonneront sur votre portable.'
          : 'Transfert suspendu. Les appels manqués restent dans le suivi.',
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRoutingBusy(false);
    }
  }
  const jobs: Row[] = runtime.record?.jobs || [],
    pilot = runtime.record?.pilot || {},
    status = calls.record;
  const sentNotices = jobs.filter((j) => j.kind === 'critical_notification').slice(0, 5);
  const ready = status?.inbound_configured ?? status?.inbound_call_available;
  return (
    <>
      <div hidden={section !== 'phone'}>
        <section className="settings-panel">
          <header className="settings-panel-heading">
            <div>
              <h2>Votre ligne commerciale</h2>
              <p>Appelez et recevez les appels avec le numéro Lumelia.</p>
            </div>
            <span className={'connection-state ' + (status?.mobile_call_available ? 'connected' : 'pending')}>
              {status?.mobile_call_available ? <Check size={12} /> : <i />}
              {calls.loading ? 'Chargement…' : status?.mobile_call_available ? 'Connectée' : 'À configurer'}
            </span>
          </header>
          <Feedback error={error || calls.error} notice={notice} />
          <div className="phone-line">
            <ProviderLogo provider="twilio" />
            <div>
              <small>Numéro professionnel · Twilio</small>
              <strong>{status?.caller_id ? phoneNumber(status.caller_id) : '—'}</strong>
            </div>
          </div>
          <div className="phone-routing-row">
            <div className="phone-routing-label">
              <PhoneForwarded size={19} />
              <div>
                <h3>Recevoir sur mon portable</h3>
                <p>
                  {ready
                    ? 'Les appels à la ligne Lumelia sont transférés ici.'
                    : 'Le transfert nécessite une configuration du serveur.'}
                </p>
              </div>
            </div>
            <div className="phone-routing-target">
              <ArrowRight size={15} />
              <strong>{phoneNumber(status?.operator_number) || 'Portable à configurer'}</strong>
            </div>
            {admin ? (
              <button
                type="button"
                className={'settings-switch ' + (inboundEnabled ? 'on' : '')}
                role="switch"
                aria-label="Transférer les appels vers mon portable"
                aria-checked={inboundEnabled}
                disabled={routingBusy || !ready}
                onClick={toggleRouting}
              >
                <span aria-hidden="true" />
                <b>{routingBusy ? 'Mise à jour…' : inboundEnabled ? 'Activé' : 'Désactivé'}</b>
              </button>
            ) : (
              <span>{inboundEnabled ? 'Activé' : 'Désactivé'}</span>
            )}
          </div>
          <div className="phone-capabilities">
            {[
              {label: 'Appel navigateur', icon: Headphones, available: status?.browser_call_available},
              {label: 'Appel depuis le portable', icon: Smartphone, available: status?.mobile_call_available},
              {label: 'Import audio', icon: FileAudio, available: status?.audio_upload_available},
            ].map(({label, icon: Icon, available}) => (
              <div key={label}>
                <Icon size={16} />
                <span>{label}</span>
                <small>{calls.loading ? '…' : available ? 'Disponible' : 'À configurer'}</small>
              </div>
            ))}
          </div>
          <div className="phone-transcription">
            <ProviderLogo provider="deepgram" />
            <div>
              <h3>Transcription et compte rendu</h3>
              <p>
                {status?.transcription_available
                  ? 'L’audio rejoint la fiche avec le résumé et les prochaines actions.'
                  : 'La transcription doit être configurée.'}
              </p>
            </div>
            <span
              className={'connection-state ' + (status?.transcription_available ? 'connected' : 'pending')}
            >
              {status?.transcription_available ? 'Actif' : 'À configurer'}
            </span>
          </div>
          <footer className="settings-panel-footer">
            <ShieldCheck size={15} />
            <span>
              <strong>Enregistrement activé par défaut.</strong> Audio privé, conservé{' '}
              {status?.retention_days || 30} jours. Seuls les appels passant par Lumelia sont concernés.
            </span>
          </footer>
        </section>
        <section className="settings-panel phone-incoming-panel">
          <IncomingCalls api={api} retentionDays={status?.retention_days || 30} />
        </section>
      </div>
      <div hidden={section !== 'notifications'}>
        <section className="settings-panel">
          <header className="settings-panel-heading">
            <div>
              <h2>Notifications par email</h2>
              <p>Recevez les demandes d’intervention, même quand Lumelia est fermé.</p>
            </div>
            <span className={'connection-state ' + (enabled ? 'connected' : 'off')}>
              {enabled ? 'Activées' : 'Désactivées'}
            </span>
          </header>
          <Feedback error={error || runtime.error} notice={notice} />
          <form onSubmit={save} className="settings-panel-body notification-form">
            <div className="settings-form-pair">
              <Field label="Recevoir les alertes à">
                <input
                  type="email"
                  required
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  autoComplete="email"
                  placeholder="vous@example.com"
                  disabled={!admin}
                />
              </Field>
              <Field label="Envoyer depuis">
                <select
                  required
                  value={senderId}
                  onChange={(e) => setSenderId(e.target.value)}
                  disabled={!admin}
                >
                  <option value="">Choisir une boîte connectée</option>
                  {options
                    .filter((s: Row) => s.status === 'authenticated')
                    .map((s: Row) => (
                      <option key={s.id} value={s.id}>
                        {s.from_email}
                      </option>
                    ))}
                </select>
              </Field>
            </div>
            <label className="settings-check">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                disabled={!admin}
              />
              <span>
                <strong>Me prévenir lorsqu’une intervention est nécessaire</strong>
                <small>Décisions critiques, connexions interrompues et traitements à reprendre.</small>
              </span>
            </label>
            {admin && (
              <div className="settings-form-actions">
                <button className="primary" disabled={busy || runtime.loading}>
                  {busy ? 'Enregistrement…' : 'Enregistrer les préférences'}
                </button>
              </div>
            )}
          </form>
        </section>
        <section className="settings-panel">
          <details className="settings-disclosure">
            <summary>
              <span>Dernières notifications</span>
              <ChevronDown size={16} />
            </summary>
            <div>
              {sentNotices.length ? (
                sentNotices.map((j) => (
                  <p key={j.id}>
                    {new Date(j.created_at).toLocaleString('fr-FR')} ·{' '}
                    {j.status === 'completed'
                      ? 'Acceptée par le serveur email'
                      : j.status === 'uncertain'
                        ? 'Transmission à vérifier'
                        : j.status === 'failed'
                          ? 'Échec de connexion'
                          : 'En cours'}
                    {j.error && ' · ' + j.error}
                  </p>
                ))
              ) : (
                <p>Aucune notification envoyée pour le moment.</p>
              )}
            </div>
          </details>
          <details className="settings-disclosure">
            <summary>
              <span>Observation du pilote</span>
              <ChevronDown size={16} />
            </summary>
            <div>
              {pilot.started_at ? (
                <>
                  <p>
                    Depuis le {new Date(pilot.started_at).toLocaleDateString('fr-FR')} ·{' '}
                    {Math.min(7, Math.floor(pilot.elapsed_seconds / 86400))} jours observés sur 7.
                  </p>
                  <p>
                    {pilot.observation_complete
                      ? 'Période écoulée : les résultats et incidents restent à examiner.'
                      : 'L’observation se poursuit.'}
                  </p>
                </>
              ) : (
                <p>
                  Créer une mission visant 25 à 50 prospects, puis démarrer son observation dans la fiche de
                  la mission.
                </p>
              )}
              {jobs
                .filter((j) => j.kind === 'pilot_snapshot')
                .slice(0, 1)
                .map((j) => (
                  <p key={j.id}>
                    {j.result.usable_prospects} / {j.result.target_prospects} prospects exploitables ·{' '}
                    {j.result.calls_connected} / {j.result.calls_attempted} appels connectés ·{' '}
                    {j.result.human_interventions_needed} interventions attendues.
                  </p>
                ))}
            </div>
          </details>
        </section>
      </div>
    </>
  );
}
