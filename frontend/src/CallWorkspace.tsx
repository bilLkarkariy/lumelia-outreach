import {useEffect, useMemo, useRef, useState, type FormEvent} from 'react';
import type {Call as VoiceCall, Device as VoiceDevice} from '@twilio/voice-sdk';
import {
  ChevronLeft,
  ChevronRight,
  CircleStop,
  FileAudio,
  Headphones,
  Mic,
  Phone,
  PhoneCall,
  PhoneOff,
  Play,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Smartphone,
  Trash2,
  Upload,
} from 'lucide-react';
import {Blank, Feedback, Field, useRecord, when, type Api, type Row} from './WorkspaceUI';
import './workflow.css';
import {errorText, type Upload as CallUpload} from './api';

const callStates: Row = {
  intent: 'Préparation',
  dispatching: 'Lancement',
  connecting: 'Connexion',
  operator_ringing: 'Votre portable sonne',
  awaiting_operator: 'Votre confirmation est attendue',
  ringing: 'Le prospect sonne',
  in_progress: 'En cours',
  completed: 'Terminé',
  failed: 'Échec',
  canceled: 'Annulé',
  uncertain: 'À vérifier',
  missed: 'Manqué',
};
const terminalCallStates = new Set(['completed', 'failed', 'canceled', 'missed']);
const callSourceNames: Row = {
  browser: 'Navigateur',
  mobile: 'Depuis mon portable',
  inbound: 'Entrant',
  upload: 'Audio importé',
};
const processingStates: Row = {
  awaiting_recording: 'Audio en attente',
  not_recorded: 'Sans enregistrement',
  unassigned: 'Rattachement nécessaire',
  queued: 'Compte rendu en attente',
  processing: 'Préparation de l’audio',
  transcribing: 'Transcription en cours',
  transcribed: 'Transcrit · compte rendu en cours',
  failed: 'Traitement en échec',
  uncertain: 'Traitement à vérifier',
  unavailable: 'Traitement indisponible',
};
const followupStates: Row = {
  pending: 'À faire',
  waiting_data: 'Données en attente',
  unresolved: 'À résoudre',
  queued: 'À faire',
  running: 'En cours',
  waiting_human: 'Décision attendue',
  completed: 'Terminée',
  cancelled: 'Annulée',
  failed: 'À reprendre',
};

export function CallWorkspace({
  api,
  upload,
  accountId,
  contactId,
  campaignId,
  missionStepId,
  initialCallId,
  defaultDestination,
  blockedReason,
  onChanged,
}: {
  api: Api;
  upload?: CallUpload;
  accountId: string;
  contactId?: string;
  campaignId?: string;
  missionStepId?: string;
  initialCallId?: string;
  defaultDestination?: string;
  /** Set when the prospect is excluded or paused: explains why calling is unavailable. */
  blockedReason?: string;
  onChanged?: () => void;
}) {
  const [revision, setRevision] = useState(0),
    [selected, setSelected] = useState(initialCallId || '');
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [destination, setDestination] = useState(defaultDestination || ''),
    [informed, setInformed] = useState(false),
    [proof, setProof] = useState('');
  const [browserInformed, setBrowserInformed] = useState(false);
  const [callMethod, setCallMethod] = useState<'browser' | 'mobile'>('browser'),
    [mobileCall, setMobileCall] = useState<Row | null>(null);
  const [liveState, setLiveState] = useState<'idle' | 'connecting' | 'in_progress'>('idle');
  const device = useRef<VoiceDevice | null>(null),
    activeCall = useRef<VoiceCall | null>(null);
  const activeIntent = useRef('');
  const startingCall = useRef(false);
  const callRequest = useRef(crypto.randomUUID()),
    uploadRequest = useRef(crypto.randomUUID());
  const status = useRecord('/calls/status', api, revision);
  const calls = useRecord(
    '/calls?' + new URLSearchParams({account_id: accountId, limit: '50'}),
    api,
    revision,
  );
  const rows: Row[] = useMemo(() => calls.record?.items || [], [calls.record]);
  useEffect(() => {
    if (liveState === 'idle') setDestination(defaultDestination || '');
  }, [accountId, defaultDestination, liveState]);
  useEffect(() => {
    if (initialCallId) setSelected(initialCallId);
  }, [initialCallId]);
  useEffect(() => {
    if (rows[0]?.id && !selected) setSelected(rows[0].id);
    else if (rows[0]?.id && selected !== initialCallId && !rows.some((row: Row) => row.id === selected))
      setSelected(rows[0].id);
    else if (!rows.length && !calls.loading) setSelected('');
  }, [rows, selected, initialCallId, calls.loading]);
  useEffect(() => {
    if (mobileCall) return;
    const recovered = rows.find((row: Row) => row.source === 'mobile' && !terminalCallStates.has(row.status));
    if (recovered) {
      setMobileCall(recovered);
      setCallMethod('mobile');
    }
  }, [rows, mobileCall]);
  useEffect(
    () => () => {
      activeCall.current?.disconnect();
      device.current?.destroy();
    },
    [],
  );
  useEffect(() => {
    if (!mobileCall?.id || terminalCallStates.has(mobileCall.status)) return;
    let alive = true;
    const poll = async () => {
      try {
        const current = await api('/calls/' + mobileCall.id);
        if (!alive) return;
        setMobileCall(current);
        setSelected(current.id);
        if (terminalCallStates.has(current.status)) {
          if (current.status === 'failed') callRequest.current = crypto.randomUUID();
          setNotice(
            current.status === 'completed'
              ? current.processing_status === 'not_recorded'
                ? 'Appel terminé, sans enregistrement.'
                : 'Appel terminé. L’audio sera traité dès sa réception.'
              : current.status === 'missed'
                ? 'Votre portable n’a pas répondu. Le prospect n’a pas été appelé.'
                : current.status === 'canceled'
                  ? 'Appel annulé avant la mise en relation.'
                  : 'La mise en relation a échoué. Vous pouvez démarrer un nouvel appel.',
          );
          refresh(current.id);
        }
      } catch (reason) {
        if (alive)
          setError(
            (errorText(reason) || 'Impossible de vérifier l’état de l’appel.') +
              ' La demande reste identifiable : vérifiez l’historique avant de la relancer.',
          );
      }
    };
    const timer = window.setInterval(poll, 3000);
    void poll();
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- polls only while this call is in flight; refresh is recreated on every render
  }, [mobileCall?.id, mobileCall?.status]);

  function refresh(callId?: string) {
    if (callId) setSelected(callId);
    setRevision((value) => value + 1);
    onChanged?.();
  }
  function resetDevice() {
    activeCall.current = null;
    device.current?.destroy();
    device.current = null;
    setLiveState('idle');
  }
  function finishLive(message: string) {
    const callId = activeIntent.current;
    activeIntent.current = '';
    resetDevice();
    setNotice(message);
    refresh(callId);
  }
  function validateRecordingInformation() {
    if (!informed)
      throw new Error('Confirmez que l’interlocuteur a reçu l’information sur l’enregistrement.');
    if (proof.trim().length < 3) throw new Error('Indiquez comment et quand l’information a été donnée.');
  }

  async function startCall(event: FormEvent) {
    event.preventDefault();
    if (startingCall.current) return;
    startingCall.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      if (callMethod === 'mobile') {
        if (!status.record?.mobile_call_available)
          throw new Error('Les appels depuis votre portable ne sont pas disponibles pour cet espace.');
        if (mobileCall?.id && !terminalCallStates.has(mobileCall.status))
          throw new Error(
            'Une mise en relation est déjà en cours. Annulez-la avant d’en démarrer une autre.',
          );
        const intent = await api('/calls/mobile', 'POST', {
          account_id: accountId,
          contact_id: contactId || null,
          campaign_id: campaignId || null,
          mission_step_id: missionStepId || null,
          destination: destination.trim(),
          recording_information_confirmed: true,
          information_proof:
            'Mode d’enregistrement confirmé par l’opérateur sur le téléphone (touche 1 ou 2)',
          request_id: callRequest.current,
        });
        setMobileCall(intent);
        setSelected(intent.id);
        if (intent.status === 'failed') {
          callRequest.current = crypto.randomUUID();
          setNotice('La mise en relation a échoué avant la sonnerie. Vous pouvez démarrer un nouvel appel.');
        } else if (intent.status === 'uncertain') {
          setNotice(
            'La demande est enregistrée, mais son état reste à vérifier. Vous pouvez la suivre ou l’annuler ici.',
          );
        } else {
          callRequest.current = crypto.randomUUID();
          setNotice(
            terminalCallStates.has(intent.status)
              ? 'Cette demande est déjà terminée. Son résultat est disponible dans l’historique.'
              : 'Demande transmise. Votre portable doit sonner avant que le prospect soit appelé.',
          );
        }
        refresh(intent.id);
        return;
      }
      if (!status.record?.browser_call_available)
        throw new Error('Les appels navigateur ne sont pas disponibles pour cet espace.');
      if (!browserInformed)
        throw new Error('Confirmez que l’interlocuteur a été informé de l’enregistrement.');
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error('Ce navigateur ne permet pas l’accès au microphone.');
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      stream.getTracks().forEach((track) => track.stop());
      setLiveState('connecting');
      const intent = await api('/calls', 'POST', {
        account_id: accountId,
        contact_id: contactId || null,
        campaign_id: campaignId || null,
        mission_step_id: missionStepId || null,
        destination: destination.trim(),
        recording_information_confirmed: true,
        information_proof: 'Information préalable confirmée par opérateur',
        request_id: callRequest.current,
      });
      activeIntent.current = intent.id;
      setSelected(intent.id);
      const credential = await api('/calls/' + intent.id + '/token', 'POST', {});
      const {Device} = await import('@twilio/voice-sdk');
      if (!Device.isSupported)
        throw new Error('Les appels audio ne sont pas pris en charge par ce navigateur.');
      device.current?.destroy();
      const currentDevice = new Device(credential.token, {closeProtection: true});
      device.current = currentDevice;
      currentDevice.on('error', (reason: unknown) => {
        setError(errorText(reason) || 'La connexion téléphonique a échoué.');
        activeIntent.current = '';
        resetDevice();
        refresh(intent.id);
      });
      const connection = await currentDevice.connect({params: {call_id: credential.call_id || intent.id}});
      activeCall.current = connection;
      setBrowserInformed(false);
      connection.on('accept', () => {
        setLiveState('in_progress');
        setNotice('Appel connecté. L’enregistrement est en cours.');
        refresh(intent.id);
      });
      connection.on('disconnect', () => finishLive('Appel terminé. L’audio sera traité dès sa réception.'));
      connection.on('cancel', () => finishLive('Appel annulé avant la connexion.'));
      connection.on('reject', () => {
        setError('L’appel n’a pas été accepté.');
        activeIntent.current = '';
        resetDevice();
        refresh(intent.id);
      });
      connection.on('error', (reason: unknown) => {
        setError(errorText(reason) || 'L’appel a été interrompu.');
        activeIntent.current = '';
        resetDevice();
        refresh(intent.id);
      });
      callRequest.current = crypto.randomUUID();
      refresh(intent.id);
    } catch (reason) {
      setError(
        reason instanceof Error && reason.name === 'NotAllowedError'
          ? 'Autorisez le microphone dans le navigateur pour appeler.'
          : (errorText(reason) || 'La demande d’appel a échoué.') +
              (callMethod === 'mobile'
                ? ' Vous pouvez réessayer : la même demande sera reconnue et ne créera pas un second appel.'
                : ''),
      );
      const callId = activeIntent.current;
      activeIntent.current = '';
      resetDevice();
      if (callId) refresh(callId);
    } finally {
      startingCall.current = false;
      setBusy(false);
    }
  }

  async function cancelMobileCall() {
    if (!mobileCall?.id || terminalCallStates.has(mobileCall.status)) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api('/calls/' + mobileCall.id + '/cancel', 'POST', {});
      setMobileCall({...mobileCall, status: result.status || 'uncertain'});
      if (terminalCallStates.has(result.status)) callRequest.current = crypto.randomUUID();
      setNotice(
        result.status === 'canceled'
          ? 'Appel annulé. Le prospect ne sera pas appelé.'
          : 'Annulation demandée. Vérifiez l’état avant de relancer.',
      );
      refresh(mobileCall.id);
    } catch (reason) {
      setError(
        (errorText(reason) || 'L’annulation a échoué.') + ' Vérifiez l’état de l’appel avant de relancer.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function stopRecording() {
    const mobileRecording =
      mobileCall?.id &&
      mobileCall.status === 'in_progress' &&
      mobileCall.data?.recording_information?.confirmed === true &&
      mobileCall.processing_status !== 'not_recorded';
    const callId = activeIntent.current || (mobileRecording ? mobileCall.id : '');
    if (!callId) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api('/calls/' + callId + '/stop-recording', 'POST', {});
      setNotice(
        result.status === 'succeeded'
          ? 'Enregistrement arrêté.'
          : result.status === 'requested'
            ? 'Arrêt de l’enregistrement demandé.'
            : 'Arrêt à vérifier dans le journal de l’appel.',
      );
      refresh(callId);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function importAudio(event: FormEvent<HTMLFormElement>) {
    const form = event.currentTarget;
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      validateRecordingInformation();
      if (!upload) throw new Error('L’import audio doit être relié à la session sécurisée de l’application.');
      if (!status.record?.audio_upload_available)
        throw new Error('L’import audio n’est pas disponible pour cet espace.');
      const data = new FormData(form),
        file = data.get('audio');
      if (!(file instanceof File) || !file.size) throw new Error('Choisissez un fichier audio.');
      data.set('account_id', accountId);
      if (contactId) data.set('contact_id', contactId);
      else data.delete('contact_id');
      if (campaignId) data.set('campaign_id', campaignId);
      else data.delete('campaign_id');
      if (missionStepId) data.set('mission_step_id', missionStepId);
      else data.delete('mission_step_id');
      data.set('recording_information_confirmed', 'true');
      data.set('information_proof', proof.trim());
      data.set('request_id', uploadRequest.current);
      const occurred = data.get('recorded_at');
      if (typeof occurred === 'string' && occurred) data.set('recorded_at', new Date(occurred).toISOString());
      else data.delete('recorded_at');
      const saved = await upload('/calls/upload', data);
      uploadRequest.current = crypto.randomUUID();
      form.reset();
      setNotice('Audio importé. La transcription sera ajoutée à cet appel.');
      refresh(saved.id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  const callerId = status.record?.caller_id || '';
  const mobileRecording =
    mobileCall?.status === 'in_progress' &&
    mobileCall.data?.recording_information?.confirmed === true &&
    mobileCall.processing_status !== 'not_recorded';
  return (
    <section className="wf-calls ws-page" aria-label="Espace d’appel">
      <header className="wf-section-heading">
        <div>
          <span className="eyebrow">APPELS</span>
          <h2>Appeler et consigner l’échange</h2>
          <p>
            Numéro présenté : <strong>{callerId}</strong> · <strong>Enregistrement activé par défaut</strong>{' '}
            · Audio privé, conservé {status.record?.retention_days || 30} jours.
          </p>
        </div>
        <button onClick={() => refresh()} aria-label="Actualiser les appels">
          <RefreshCw size={14} />
          Actualiser
        </button>
      </header>
      <Feedback error={error || status.error || calls.error} notice={notice} />
      {status.record && !status.record.transcription_available && (
        <p className="wf-processing-notice">
          Transcription à configurer. Les audios restent conservés dans l’attente du traitement.
        </p>
      )}
      {blockedReason ? (
        <div className="wf-call-blocked" role="status">
          <PhoneOff size={18} />
          <div>
            <strong>Appels désactivés pour ce prospect</strong>
            <p>{blockedReason}</p>
            <small>
              L’historique reste consultable, et un appel peut être consigné depuis la fiche contact si
              l’échange a déjà eu lieu.
            </small>
          </div>
        </div>
      ) : (
        <div className="wf-call-composer">
          <form className="wf-browser-call" onSubmit={startCall}>
            <header>
              {callMethod === 'browser' ? <Phone size={17} /> : <Smartphone size={17} />}
              <div>
                <strong>
                  {callMethod === 'browser' ? 'Appel depuis le navigateur' : 'Appel depuis votre portable'}
                </strong>
                <small>
                  {callMethod === 'browser'
                    ? 'Informez l’interlocuteur avant de lancer l’enregistrement'
                    : `Le serveur appelle ${status.record?.operator_number || 'votre numéro vérifié'} avant de joindre le prospect`}
                </small>
              </div>
            </header>
            <div className="wf-call-method" role="group" aria-label="Méthode d’appel">
              <button
                type="button"
                aria-pressed={callMethod === 'browser'}
                onClick={() => setCallMethod('browser')}
                disabled={
                  liveState !== 'idle' ||
                  Boolean(mobileCall?.id && !terminalCallStates.has(mobileCall.status))
                }
              >
                <Mic size={14} />
                Depuis le navigateur
              </button>
              <button
                type="button"
                aria-pressed={callMethod === 'mobile'}
                onClick={() => setCallMethod('mobile')}
                disabled={
                  liveState !== 'idle' ||
                  Boolean(mobileCall?.id && !terminalCallStates.has(mobileCall.status)) ||
                  !status.record?.mobile_call_available
                }
              >
                <Smartphone size={14} />
                Depuis mon portable
              </button>
            </div>
            <Field label="Numéro à appeler">
              <input
                type="tel"
                value={destination}
                onChange={(event) => setDestination(event.target.value)}
                required
                maxLength={50}
                placeholder="+33…"
                disabled={
                  liveState !== 'idle' ||
                  Boolean(mobileCall?.id && !terminalCallStates.has(mobileCall.status))
                }
              />
            </Field>
            {callMethod === 'browser' ? (
              <label className="wf-browser-informed">
                <input
                  type="checkbox"
                  checked={browserInformed}
                  onChange={(event) => setBrowserInformed(event.target.checked)}
                  disabled={liveState !== 'idle'}
                />
                <span>Interlocuteur informé de l’enregistrement</span>
              </label>
            ) : (
              <p className="wf-mobile-help">
                <PhoneCall size={15} />
                <span>
                  Votre portable sonne d’abord : tapez <strong>1</strong> pour accepter l’appel standard
                  enregistré, ou <strong>2</strong> pour appeler sans enregistrement. Sans réponse, le
                  prospect n’est pas appelé.
                </span>
              </p>
            )}
            <div className="wf-call-actions">
              {callMethod === 'mobile' && mobileCall?.id && !terminalCallStates.has(mobileCall.status) ? (
                <>
                  <span className={'wf-live ' + mobileCall.status}>
                    {callStates[mobileCall.status] || mobileCall.status}
                  </span>
                  {mobileRecording && (
                    <button type="button" disabled={busy} onClick={stopRecording}>
                      <CircleStop size={15} />
                      Arrêter l’enregistrement
                    </button>
                  )}
                  <button type="button" className="wf-danger" disabled={busy} onClick={cancelMobileCall}>
                    <PhoneOff size={15} />
                    Annuler
                  </button>
                </>
              ) : liveState === 'idle' ? (
                <button
                  className="primary"
                  disabled={
                    busy ||
                    (callMethod === 'browser'
                      ? !status.record?.browser_call_available || !browserInformed
                      : !status.record?.mobile_call_available)
                  }
                >
                  {callMethod === 'browser' ? <Mic size={15} /> : <PhoneCall size={15} />}{' '}
                  {busy
                    ? 'Connexion…'
                    : callMethod === 'browser'
                      ? 'Démarrer l’appel'
                      : 'Faire sonner mon portable'}
                </button>
              ) : (
                <>
                  <span className={'wf-live ' + liveState}>
                    {liveState === 'connecting' ? 'Connexion en cours' : 'Appel en cours'}
                  </span>
                  <button type="button" disabled={busy} onClick={stopRecording}>
                    <CircleStop size={15} />
                    Arrêter l’enregistrement
                  </button>
                  <button
                    type="button"
                    className="wf-danger"
                    onClick={() => activeCall.current?.disconnect()}
                  >
                    <PhoneOff size={15} />
                    Raccrocher
                  </button>
                </>
              )}
            </div>
          </form>
          <details className="wf-upload-details">
            <summary>Importer un appel externe</summary>
            <RecordingConfirmation
              checked={informed}
              proof={proof}
              setChecked={setInformed}
              setProof={setProof}
            />
            <form className="wf-upload-call" onSubmit={importAudio}>
              <header>
                <FileAudio size={17} />
                <div>
                  <strong>Importer un appel externe</strong>
                  <small>Pour un échange déjà enregistré avec information préalable</small>
                </div>
              </header>
              <Field label="Date de l’appel externe">
                <input name="recorded_at" type="datetime-local" required />
              </Field>
              <Field label="Fichier audio">
                <input
                  name="audio"
                  type="file"
                  accept="audio/*,.mp3,.wav,.m4a,.ogg,.webm"
                  required
                  disabled={!upload || !status.record?.audio_upload_available}
                />
              </Field>
              <button
                disabled={
                  busy ||
                  !upload ||
                  !status.record?.audio_upload_available ||
                  !informed ||
                  proof.trim().length < 3
                }
              >
                <Upload size={15} />
                Importer et transcrire
              </button>
              {!upload && <small>L’import sera disponible une fois relié à la session sécurisée.</small>}
            </form>
          </details>
        </div>
      )}
      {rows.length > 0 || selected ? (
        <div className={'wf-call-layout ' + (selected ? 'has-detail' : '')}>
          <section className="wf-call-list">
            <header>
              <strong>Historique du compte</strong>
              <span>
                {rows.length} appel{rows.length > 1 ? 's' : ''}
              </span>
            </header>
            {rows.length ? (
              <ol>
                {rows.map((row: Row) => (
                  <li key={row.id}>
                    <button
                      className={selected === row.id ? 'active' : ''}
                      onClick={() => setSelected(row.id)}
                    >
                      <span>
                        <strong>{row.destination}</strong>
                        <small>
                          {when(row.source === 'upload' ? row.ended_at : row.started_at || row.created_at)} ·{' '}
                          {callSourceNames[row.source] || row.source}
                        </small>
                      </span>
                      <span className={'wf-state ' + row.status}>{callStates[row.status] || row.status}</span>
                    </button>
                  </li>
                ))}
              </ol>
            ) : (
              <Blank title={calls.loading ? 'Chargement des appels…' : 'Aucun appel enregistré'}>
                {calls.loading
                  ? 'L’historique du compte arrive.'
                  : 'Démarrez un appel ou importez un enregistrement.'}
              </Blank>
            )}
          </section>
          {selected ? (
            <CallDetail
              api={api}
              id={selected}
              revision={revision}
              retentionDays={status.record?.retention_days || 30}
            />
          ) : (
            <div className="wf-call-placeholder">
              <Headphones size={25} />
              <h3>Sélectionnez un appel</h3>
              <p>L’audio, la transcription et le compte rendu apparaîtront ici.</p>
            </div>
          )}
        </div>
      ) : (
        <p className="wf-empty-call">Aucun appel consigné pour cette entreprise.</p>
      )}
    </section>
  );
}

function RecordingConfirmation({
  checked,
  proof,
  setChecked,
  setProof,
}: {
  checked: boolean;
  proof: string;
  setChecked: (value: boolean) => void;
  setProof: (value: string) => void;
}) {
  return (
    <div className="wf-recording-confirmation">
      <label>
        <input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} />
        <span>
          <strong>Information sur l’enregistrement donnée</strong>
          <small>L’interlocuteur a été informé avant le début de l’enregistrement.</small>
        </span>
      </label>
      <textarea
        aria-label="Preuve de l’information sur l’enregistrement"
        value={proof}
        onChange={(event) => setProof(event.target.value)}
        minLength={3}
        maxLength={1000}
        rows={2}
        placeholder="Ex. Annonce orale au début de l’appel, acceptée par l’interlocuteur."
      />
    </div>
  );
}

export function CallDetail({
  api,
  id,
  revision,
  retentionDays,
}: {
  api: Api;
  id: string;
  revision: number;
  retentionDays: number;
}) {
  const detail = useRecord('/calls/' + id, api, revision),
    row = detail.record;
  const audio = useRef<HTMLAudioElement>(null);
  const [audioFailed, setAudioFailed] = useState(false);
  const mediaUrl = row?.recordings?.find((item: Row) => item.media_url)?.media_url;
  useEffect(() => setAudioFailed(false), [id, mediaUrl]);
  const processing = row?.processing_status,
    refreshDetail = detail.refresh;
  useEffect(() => {
    if (!['awaiting_recording', 'queued', 'processing', 'transcribing'].includes(processing)) return;
    const timer = window.setInterval(refreshDetail, 10000);
    return () => window.clearInterval(timer);
  }, [id, processing, refreshDetail]);
  function playAt(seconds: number) {
    if (!audio.current) return;
    audio.current.currentTime = seconds;
    audio.current.play().catch(() => undefined);
  }
  if (detail.error)
    return (
      <section className="wf-call-detail">
        <Feedback error={detail.error} />
      </section>
    );
  if (!row)
    return (
      <section className="wf-call-detail">
        <p className="wf-loading">Chargement de l’appel…</p>
      </section>
    );
  const recording = row.recordings?.find((item: Row) => item.media_url) || row.recordings?.[0];
  const media = recording?.media_url || '';
  const duration = recording?.duration_seconds ?? row.duration_seconds;
  const durationLabel = duration
    ? duration < 60
      ? `${duration} s`
      : `${Math.floor(duration / 60)} min ${duration % 60} s`
    : callStates[row.status] || row.status;
  const displayedNumber =
    row.source === 'inbound'
      ? row.caller_number || row.from_number || row.source_number || row.destination
      : row.destination;
  return (
    <section className="wf-call-detail">
      <header>
        <div>
          <span className="eyebrow">
            APPEL DU{' '}
            {row.ended_at || row.started_at
              ? new Date(
                  row.source === 'upload' ? row.ended_at : row.started_at || row.created_at,
                ).toLocaleDateString('fr-FR')
              : 'DATE À PRÉCISER'}
          </span>
          <h3>{displayedNumber}</h3>
          <p>
            {durationLabel} · {processingStates[row.processing_status] || row.processing_status}
          </p>
        </div>
        <span className={'wf-state ' + row.status}>{callStates[row.status] || row.status}</span>
      </header>
      {row.processing_error && <Feedback error={row.processing_error} />}
      <section className="wf-private-audio">
        <div>
          <ShieldCheck size={16} />
          <span>
            <strong>Enregistrement audio</strong>
            <small>Accès authentifié · suppression après {retentionDays} jours</small>
          </span>
        </div>
        {media ? (
          <>
            <audio
              key={media}
              ref={audio}
              controls
              preload="metadata"
              src={media}
              aria-label="Écouter l’enregistrement de l’appel"
              onError={() => setAudioFailed(true)}
            />
            {audioFailed && (
              <p className="wf-inline-error">
                Le lecteur n’a pas pu charger l’audio.{' '}
                <button
                  type="button"
                  onClick={() => {
                    setAudioFailed(false);
                    audio.current?.load();
                  }}
                >
                  Réessayer
                </button>
              </p>
            )}
            <a className="wf-audio-download" href={media + '?download=true'} download>
              Télécharger l’enregistrement
            </a>
          </>
        ) : (
          <p>
            {row.processing_status === 'not_recorded'
              ? 'Cet appel a continué sans enregistrement à la demande de l’interlocuteur.'
              : row.status === 'missed'
                ? 'Aucun échange n’a été enregistré pour cet appel manqué.'
                : 'L’enregistrement sera disponible dès la fin du transfert.'}
          </p>
        )}
      </section>
      <Transcript transcript={row.transcript} playAt={media ? playAt : undefined} />
      <CallReport
        api={api}
        callId={row.id}
        revision={row.transcript?.updated_at || row.updated_at}
        transcript={row.transcript}
        playAt={media ? playAt : undefined}
      />
    </section>
  );
}

function Transcript({transcript, playAt}: {transcript?: Row | null; playAt?: (seconds: number) => void}) {
  const [page, setPage] = useState(1),
    segments = transcript?.segments || [];
  const pages = Math.max(1, Math.ceil(segments.length / 20)),
    visible = segments.slice((page - 1) * 20, page * 20);
  useEffect(() => setPage(1), [transcript?.id, transcript?.version]);
  if (!transcript || transcript.status !== 'ready')
    return (
      <section className="wf-transcript">
        <h4>Transcription</h4>
        <p>La transcription apparaîtra après le traitement de l’audio.</p>
      </section>
    );
  return (
    <details className="wf-transcript">
      <summary>
        Transcription{' '}
        <span>
          {segments.length} passage{segments.length > 1 ? 's' : ''}
        </span>
      </summary>
      <ol>
        {visible.map((segment: Row, index: number) => (
          <li key={(page - 1) * 20 + index}>
            <button
              disabled={!playAt}
              onClick={() => playAt?.(Number(segment.start || 0))}
              aria-label={'Lire à ' + timestamp(segment.start)}
            >
              <Play size={11} />
              {timestamp(segment.start)}
            </button>
            <div>
              <strong>{speaker(segment.speaker)}</strong>
              <p>{segment.text}</p>
            </div>
          </li>
        ))}
      </ol>
      {segments.length > 20 && (
        <nav className="wf-pages" aria-label="Pagination de la transcription">
          <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
            <ChevronLeft size={13} />
            Précédent
          </button>
          <span>
            {page} / {pages}
          </span>
          <button disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>
            Suivant
            <ChevronRight size={13} />
          </button>
        </nav>
      )}
    </details>
  );
}

function CallReport({
  api,
  callId,
  revision,
  transcript,
  playAt,
}: {
  api: Api;
  callId: string;
  revision: unknown;
  transcript?: Row | null;
  playAt?: (seconds: number) => void;
}) {
  const data = useRecord('/calls/' + callId + '/report', api, revision),
    report = data.record?.report,
    steps = data.record?.steps || [];
  const pending = !data.loading && !data.error && !report,
    refreshReport = data.refresh;
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(refreshReport, 10000);
    return () => window.clearInterval(timer);
  }, [callId, pending, refreshReport]);
  if (data.loading)
    return (
      <section className="wf-report">
        <h4>Compte rendu et suites</h4>
        <p>Chargement du compte rendu…</p>
      </section>
    );
  if (!report)
    return (
      <section className="wf-report">
        <h4>Compte rendu et suites</h4>
        <p>{data.error || 'Le compte rendu sera disponible après la transcription.'}</p>
      </section>
    );
  const groups: [string, string][] = [
    ['needs', 'Besoins'],
    ['objections', 'Objections'],
    ['commitments', 'Engagements'],
    ['uncertainties', 'À confirmer'],
  ];
  return (
    <section className="wf-report">
      <header>
        <div>
          <h4>Compte rendu et suites</h4>
          <small>
            Version {report.version} ·{' '}
            {report.source === 'human' ? 'mise à jour manuelle' : 'généré depuis la transcription'} ·{' '}
            {when(report.created_at)}
          </small>
        </div>
        <span className="wf-state completed">Compte rendu prêt</span>
      </header>
      <p className="wf-report-summary">{report.summary}</p>
      {report.facts?.outcome && (
        <p className="wf-report-outcome">
          <strong>Issue :</strong>{' '}
          {outcomeNames[report.facts.outcome] || String(report.facts.outcome).replaceAll('_', ' ')}
        </p>
      )}
      <div className="wf-report-facts">
        {groups.map(([key, label]) =>
          report.facts?.[key]?.length ? (
            <div key={key}>
              <strong>{label}</strong>
              <ul>
                {report.facts[key].map((item: string, index: number) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null,
        )}
      </div>
      {report.actions?.length ? (
        <div className="wf-report-actions">
          <strong>Actions proposées</strong>
          <ol>
            {report.actions.map((action: Row, index: number) => {
              const step = steps.find((item: Row) => item.id === action.step_id),
                state = step?.status || action.status,
                segment = transcript?.segments?.[action.segment_index];
              return (
                <li key={action.source_action_id || index}>
                  <div>
                    <strong>{action.title}</strong>
                    <p>{action.reason}</p>
                    {action.due_at && <small>Échéance {when(action.due_at)}</small>}
                    {action.quote && (
                      <small>
                        Source {timestamp(action.start ?? segment?.start)} · « {action.quote} »
                      </small>
                    )}
                  </div>
                  <span className={'wf-state ' + state}>{followupStates[state] || state}</span>
                </li>
              );
            })}
          </ol>
        </div>
      ) : (
        <p>Aucune action de suivi enregistrée.</p>
      )}
      <ReportEditor
        key={report.id}
        api={api}
        callId={callId}
        report={report}
        transcript={transcript}
        playAt={playAt}
        onSaved={data.refresh}
      />
    </section>
  );
}

const actionKinds: [string, string][] = [
  ['callback', 'Rappel téléphonique'],
  ['draft_email', 'Préparer un email'],
  ['identify', 'Identifier un contact'],
  ['qualify', 'Qualifier'],
  ['assign_contact', 'Assigner un contact'],
  ['update_opportunity', 'Mettre à jour l’opportunité'],
  ['human_decision', 'Décision humaine'],
];
const outcomeNames: Row = {
  conversation: 'Conversation',
  interested: 'Intérêt confirmé',
  no_need: 'Pas de besoin',
  not_reached: 'Non joint',
  wrong_number: 'Numéro incorrect',
  stop: 'Ne plus contacter',
};
const sourcedOutcomes = new Set(['interested', 'no_need', 'wrong_number', 'stop']);

function ReportEditor({
  api,
  callId,
  report,
  transcript,
  playAt,
  onSaved,
}: {
  api: Api;
  callId: string;
  report: Row;
  transcript?: Row | null;
  playAt?: (seconds: number) => void;
  onSaved: () => void;
}) {
  const segments: Row[] = transcript?.segments || [];
  const [summary, setSummary] = useState(report.summary || '');
  const [needs, setNeeds] = useState(joinLines(report.facts?.needs));
  const [objections, setObjections] = useState(joinLines(report.facts?.objections));
  const [commitments, setCommitments] = useState(joinLines(report.facts?.commitments));
  const [uncertainties, setUncertainties] = useState(joinLines(report.facts?.uncertainties));
  const [outcome, setOutcome] = useState(report.facts?.outcome || 'conversation');
  const [outcomeSegment, setOutcomeSegment] = useState(String(report.facts?.outcome_segment_index ?? ''));
  const [outcomeQuote, setOutcomeQuote] = useState(report.facts?.outcome_quote || '');
  const [actions, setActions] = useState<Row[]>(() =>
    (report.actions || []).map((action: Row, index: number) => ({
      ...action,
      _key: action.source_action_id || `existing-${index}`,
      due_at: localDate(action.due_at),
      depends_on: action.depends_on || [],
      requires_decision: !!action.requires_decision,
    })),
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const requestId = useRef(crypto.randomUUID());
  const outcomeNeedsSource = sourcedOutcomes.has(outcome);

  function updateAction(index: number, patch: Row) {
    setActions((rows) => rows.map((row, position) => (position === index ? {...row, ...patch} : row)));
  }
  function chooseActionSource(index: number, value: string) {
    const segmentIndex = Number(value),
      segment = segments[segmentIndex];
    updateAction(index, {segment_index: segmentIndex, quote: String(segment?.text || '')});
  }
  function addAction() {
    if (!segments.length || actions.length >= 30) return;
    setActions((rows) => [
      ...rows,
      {
        _key: crypto.randomUUID(),
        source_action_id: null,
        kind: 'callback',
        title: '',
        reason: '',
        due_at: '',
        segment_index: 0,
        quote: String(segments[0]?.text || ''),
        depends_on: [],
        requires_decision: false,
      },
    ]);
  }
  function removeAction(index: number) {
    setActions((rows) =>
      rows
        .filter((_, position) => position !== index)
        .map((row) => ({
          ...row,
          depends_on: (row.depends_on || [])
            .filter((dependency: number) => dependency !== index)
            .map((dependency: number) => (dependency > index ? dependency - 1 : dependency)),
        })),
    );
  }
  function chooseOutcomeSource(value: string) {
    setOutcomeSegment(value);
    setOutcomeQuote(value === '' ? '' : String(segments[Number(value)]?.text || ''));
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      if (outcomeNeedsSource && (outcomeSegment === '' || outcomeQuote.length < 5))
        throw new Error('Choisissez le passage qui justifie l’issue de l’appel.');
      if (actions.some((action) => !segments[action.segment_index] || action.quote.trim().length < 5))
        throw new Error('Chaque action doit conserver une source exacte dans la transcription.');
      await api('/calls/' + callId + '/report', 'PUT', {
        request_id: requestId.current,
        expected_version: report.version,
        summary: summary.trim(),
        needs: splitLines(needs),
        objections: splitLines(objections),
        commitments: splitLines(commitments),
        uncertainties: splitLines(uncertainties),
        outcome,
        outcome_segment_index: outcomeNeedsSource ? Number(outcomeSegment) : null,
        outcome_quote: outcomeNeedsSource ? outcomeQuote : '',
        identified_person: null,
        actions: actions.map((action) => ({
          source_action_id: action.source_action_id || null,
          kind: action.kind,
          title: action.title.trim(),
          reason: action.reason.trim(),
          due_at: action.due_at ? new Date(action.due_at).toISOString() : null,
          segment_index: Number(action.segment_index),
          quote: action.quote,
          depends_on: action.depends_on || [],
          requires_decision: !!action.requires_decision,
        })),
      });
      requestId.current = crypto.randomUUID();
      setNotice('Compte rendu mis à jour avec ses sources.');
      onSaved();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="wf-report-editor">
      <summary>Modifier le compte rendu et les prochaines actions</summary>
      <form onSubmit={save}>
        <Feedback error={error} notice={notice} />
        <Field label="Résumé">
          <textarea
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            minLength={10}
            maxLength={20000}
            rows={4}
            required
          />
        </Field>
        <div className="wf-report-editor-grid">
          <Field label="Besoins — un par ligne">
            <textarea value={needs} onChange={(event) => setNeeds(event.target.value)} rows={3} />
          </Field>
          <Field label="Objections — une par ligne">
            <textarea value={objections} onChange={(event) => setObjections(event.target.value)} rows={3} />
          </Field>
          <Field label="Engagements — un par ligne">
            <textarea value={commitments} onChange={(event) => setCommitments(event.target.value)} rows={3} />
          </Field>
          <Field label="À confirmer — un point par ligne">
            <textarea
              value={uncertainties}
              onChange={(event) => setUncertainties(event.target.value)}
              rows={3}
            />
          </Field>
        </div>
        <div className="wf-outcome-editor">
          <Field label="Issue de l’appel">
            <select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
              {Object.entries(outcomeNames).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          {outcomeNeedsSource && (
            <Field label="Passage qui justifie cette issue">
              <select
                value={outcomeSegment}
                onChange={(event) => chooseOutcomeSource(event.target.value)}
                required
              >
                <option value="">Choisir dans la transcription…</option>
                {segments.map((segment, index) => (
                  <option value={index} key={index}>
                    {timestamp(segment.start)} · {speaker(segment.speaker)} ·{' '}
                    {String(segment.text).slice(0, 90)}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {outcomeNeedsSource && outcomeSegment !== '' && (
            <SourceExcerpt segment={segments[Number(outcomeSegment)]} quote={outcomeQuote} playAt={playAt} />
          )}
        </div>
        <div className="wf-action-editor">
          <header>
            <div>
              <strong>Prochaines actions</strong>
              <small>Chaque action reste liée à un passage exact de l’appel.</small>
            </div>
            <button type="button" onClick={addAction} disabled={!segments.length || actions.length >= 30}>
              <Plus size={13} />
              Ajouter
            </button>
          </header>
          {actions.length ? (
            actions.map((action, index) => (
              <fieldset key={action._key}>
                <legend>Action {index + 1}</legend>
                <button
                  type="button"
                  className="wf-action-remove"
                  aria-label={'Supprimer l’action ' + (index + 1)}
                  onClick={() => removeAction(index)}
                >
                  <Trash2 size={13} />
                  Supprimer
                </button>
                <div className="wf-action-fields">
                  <Field label="Type">
                    <select
                      value={action.kind}
                      onChange={(event) => updateAction(index, {kind: event.target.value})}
                    >
                      {actionKinds.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Titre">
                    <input
                      value={action.title}
                      onChange={(event) => updateAction(index, {title: event.target.value})}
                      minLength={5}
                      maxLength={220}
                      required
                    />
                  </Field>
                  <Field label="Échéance">
                    <input
                      type="datetime-local"
                      value={action.due_at}
                      onChange={(event) => updateAction(index, {due_at: event.target.value})}
                    />
                  </Field>
                </div>
                <Field label="Pourquoi cette action">
                  <textarea
                    value={action.reason}
                    onChange={(event) => updateAction(index, {reason: event.target.value})}
                    minLength={5}
                    maxLength={2000}
                    rows={2}
                    required
                  />
                </Field>
                <Field label="Source horodatée">
                  <select
                    value={action.segment_index}
                    onChange={(event) => chooseActionSource(index, event.target.value)}
                  >
                    {segments.map((segment, segmentIndex) => (
                      <option value={segmentIndex} key={segmentIndex}>
                        {timestamp(segment.start)} · {speaker(segment.speaker)} ·{' '}
                        {String(segment.text).slice(0, 90)}
                      </option>
                    ))}
                  </select>
                </Field>
                <SourceExcerpt
                  segment={segments[action.segment_index]}
                  quote={action.quote}
                  playAt={playAt}
                />
                <label className="wf-action-decision">
                  <input
                    type="checkbox"
                    checked={action.requires_decision}
                    onChange={(event) => updateAction(index, {requires_decision: event.target.checked})}
                  />
                  Cette action nécessite une décision humaine
                </label>
              </fieldset>
            ))
          ) : (
            <p>Aucune prochaine action. Ajoutez-en une uniquement si l’appel la justifie.</p>
          )}
        </div>
        <div className="wf-report-save">
          <small>
            La version précédente reste dans l’historique. Les citations sont reprises mot pour mot de la
            transcription.
          </small>
          <button className="primary" disabled={busy || !segments.length}>
            <Save size={14} />
            {busy ? 'Enregistrement…' : 'Enregistrer une nouvelle version'}
          </button>
        </div>
      </form>
    </details>
  );
}

function SourceExcerpt({
  segment,
  quote,
  playAt,
}: {
  segment?: Row;
  quote: string;
  playAt?: (seconds: number) => void;
}) {
  return (
    <div className="wf-source-excerpt">
      <div>
        <span>
          Source {timestamp(segment?.start)} · {speaker(segment?.speaker)}
        </span>
        <button
          type="button"
          disabled={!playAt || !segment}
          onClick={() => playAt?.(Number(segment?.start || 0))}
        >
          <Play size={11} />
          Écouter
        </button>
      </div>
      <blockquote>{quote || 'Sélectionnez un passage de la transcription.'}</blockquote>
    </div>
  );
}

function joinLines(value: unknown) {
  return Array.isArray(value) ? value.join('\n') : '';
}
function splitLines(value: string) {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 30);
}
function localDate(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function timestamp(value?: number | string) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
function speaker(value: unknown) {
  if (value === 'agent') return 'Vous';
  if (value === 'customer' || value === 'contact') return 'Interlocuteur';
  return value === undefined || value === null || value === '' ? 'Conversation' : 'Voix ' + String(value);
}
