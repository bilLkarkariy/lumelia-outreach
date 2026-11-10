import {useEffect, useRef, useState, type FormEvent} from 'react';
import {ChevronLeft, ChevronRight, CircleCheck, Clock3, Pause, Play, Plus, RotateCcw, X} from 'lucide-react';
import {Blank, Feedback, Field, useRecord, when, type Api, type Row} from './WorkspaceUI';
import './workflow.css';
import {errorText} from './api';

const missionStates: Row = {
  pending: 'À démarrer',
  queued: 'À démarrer',
  active: 'En cours',
  running: 'En cours',
  paused: 'Suspendue',
  waiting_human: 'Décision attendue',
  completed: 'Terminée',
  cancelled: 'Annulée',
  failed: 'À reprendre',
};
const stepStates: Row = {
  pending: 'À faire',
  waiting_data: 'Données en attente',
  unresolved: 'À résoudre',
  queued: 'À faire',
  running: 'En cours',
  waiting_external: 'Confirmation en attente',
  waiting_human: 'À vous',
  completed: 'Terminé',
  cancelled: 'Annulé',
  failed: 'Échec',
  uncertain: 'Résultat incertain',
};
const stepKinds: Row = {
  research: 'Recherche',
  qualification: 'Qualification',
  callback: 'Appel',
  call: 'Appel',
  human_decision: 'Décision',
  review: 'Examen',
};

export function MissionPanel({
  api,
  campaignId,
  groupId,
  accountId,
  campaigns = [],
}: {
  api: Api;
  campaignId?: string;
  groupId?: string;
  accountId?: string;
  campaigns?: Row[];
}) {
  const [page, setPage] = useState(1),
    [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [selectedCampaign, setSelectedCampaign] = useState(campaignId || campaigns[0]?.id || '');
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => setPage(1), [campaignId, accountId]);
  useEffect(() => {
    if (campaignId) setSelectedCampaign(campaignId);
    else if (!selectedCampaign && campaigns[0]?.id) setSelectedCampaign(campaigns[0].id);
  }, [campaignId, campaigns, selectedCampaign]);
  const parameters = new URLSearchParams({page: String(page)});
  if (campaignId) parameters.set('campaign_id', campaignId);
  if (groupId) parameters.set('group_id', groupId);
  if (accountId) parameters.set('account_id', accountId);
  const missions = useRecord('/missions?' + parameters.toString(), api, revision);
  const runtime = useRecord('/runtime', api, revision);
  const rows = missions.record?.items || [],
    total = missions.record?.total || 0;
  const counts = missions.record?.counts || {};
  const pages = Math.max(1, Math.ceil(total / 20));

  async function createMission(event: FormEvent<HTMLFormElement>) {
    const form = event.currentTarget;
    event.preventDefault();
    const values = new FormData(form);
    const due = String(values.get('due_at') || '');
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api('/missions', 'POST', {
        request_id: requestId.current,
        title: String(values.get('title') || '').trim(),
        objective: String(values.get('objective') || '').trim(),
        campaign_id: selectedCampaign,
        target_count: Number(values.get('target_count')),
        ...(accountId ? {account_ids: [accountId]} : {}),
        due_at: due ? new Date(due).toISOString() : null,
      });
      requestId.current = crypto.randomUUID();
      form.reset();
      setCreating(false);
      setPage(1);
      setRevision((value) => value + 1);
      setNotice('Mission créée. Sa progression apparaîtra ici.');
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="wf-missions ws-page" aria-label="Missions">
      <header className="wf-section-heading">
        <div>
          <span className="eyebrow">MISSIONS</span>
          <h2>Objectifs et progression</h2>
          <p>Suivez les résultats acceptés pour chaque mission.</p>
        </div>
        <button className="primary" onClick={() => setCreating((value) => !value)} aria-expanded={creating}>
          <Plus size={15} />
          {creating ? 'Fermer' : 'Nouvelle mission'}
        </button>
      </header>
      <Feedback error={error || missions.error} notice={notice} />
      {missions.record?.counts && (
        <div className="wf-mission-counts" aria-label="État des missions">
          <span>
            <strong>{counts.active ?? counts.running ?? 0}</strong> en cours
          </span>
          <span>
            <strong>{counts.waiting_human || 0}</strong> à décider
          </span>
          <span>
            <strong>{counts.completed || 0}</strong> terminées
          </span>
        </div>
      )}
      {creating && (
        <form className="wf-create-mission" onSubmit={createMission}>
          <div className="wf-create-fields">
            <Field label="Mission">
              <input
                name="title"
                required
                minLength={5}
                maxLength={220}
                placeholder="Qualifier les comptes prioritaires"
              />
            </Field>
            <Field label="Résultat attendu">
              <input
                name="objective"
                required
                minLength={10}
                maxLength={2000}
                placeholder="Identifier les contacts prêts pour un appel"
              />
            </Field>
            <Field label="Cible">
              <input
                name="target_count"
                type="number"
                min={1}
                max={500}
                defaultValue={accountId ? 1 : 20}
                required
              />
            </Field>
            {!campaignId && (
              <Field label="Sous-campagne">
                <select
                  value={selectedCampaign}
                  required
                  onChange={(event) => setSelectedCampaign(event.target.value)}
                >
                  <option value="">Choisir…</option>
                  {campaigns.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Échéance">
              <input name="due_at" type="datetime-local" />
            </Field>
          </div>
          <div className="wf-form-actions">
            <small>
              {accountId
                ? 'Cette mission portera sur l’entreprise ouverte.'
                : 'La mission respecte les contacts et limites déjà autorisés.'}
            </small>
            <button className="primary" disabled={busy || !selectedCampaign}>
              {busy ? 'Création…' : 'Créer la mission'}
            </button>
          </div>
        </form>
      )}
      <div className="wf-mission-list" aria-busy={missions.loading}>
        {rows.length ? (
          rows.map((mission: Row) => (
            <MissionItem
              key={mission.id}
              api={api}
              mission={mission}
              runtime={runtime.record}
              revision={revision}
              onChanged={() => setRevision((value) => value + 1)}
            />
          ))
        ) : (
          <Blank title={missions.loading ? 'Chargement des missions…' : 'Aucune mission'}>
            {missions.loading
              ? 'Les objectifs en cours vont apparaître.'
              : 'Créez une mission courte avec un résultat mesurable.'}
          </Blank>
        )}
      </div>
      {total > 20 && (
        <nav className="wf-pages" aria-label="Pagination des missions">
          <button disabled={missions.loading || page <= 1} onClick={() => setPage((value) => value - 1)}>
            <ChevronLeft size={14} />
            Précédent
          </button>
          <span>
            Page {page} sur {pages} · {total} missions
          </span>
          <button disabled={missions.loading || page >= pages} onClick={() => setPage((value) => value + 1)}>
            Suivant
            <ChevronRight size={14} />
          </button>
        </nav>
      )}
    </section>
  );
}

function MissionItem({
  api,
  mission,
  runtime,
  revision,
  onChanged,
}: {
  api: Api;
  mission: Row;
  runtime: Row | null;
  revision: number;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const pilotRequest = useRef(crypto.randomUUID());
  const accepted = Math.max(0, Number(mission.accepted_count || 0));
  const target = Math.max(1, Number(mission.target_count || 1));
  const progress = Math.min(100, Math.round((accepted / target) * 100));
  async function update(action: 'pause' | 'resume' | 'cancel') {
    setBusy(true);
    setError('');
    try {
      await api('/missions/' + mission.id, 'PATCH', {action, expected_updated_at: mission.updated_at});
      onChanged();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="wf-mission">
      <div className="wf-mission-summary">
        <button className="wf-mission-open" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          <span>
            <strong>{mission.title}</strong>
            <small>{mission.objective}</small>
          </span>
          <span className={'wf-state ' + mission.status}>
            {missionStates[mission.status] || mission.status}
          </span>
        </button>
        <div className="wf-progress-row">
          <div
            className="wf-progress"
            role="progressbar"
            aria-label={'Résultats acceptés pour ' + mission.title}
            aria-valuemin={0}
            aria-valuemax={target}
            aria-valuenow={accepted}
          >
            <span style={{width: progress + '%'}} />
          </div>
          <strong>
            {accepted} / {target} accepté{accepted === 1 ? '' : 's'}
          </strong>
        </div>
        <div className="wf-mission-meta">
          <span>{mission.due_at ? 'Échéance ' + when(mission.due_at) : 'Sans échéance'}</span>
          <div>
            {['active', 'running'].includes(mission.status) && (
              <button disabled={busy} onClick={() => update('pause')}>
                <Pause size={13} />
                Suspendre
              </button>
            )}
            {mission.status === 'paused' && (
              <button disabled={busy} onClick={() => update('resume')}>
                <Play size={13} />
                Reprendre
              </button>
            )}
            {!['completed', 'cancelled'].includes(mission.status) && (
              <button disabled={busy} onClick={() => update('cancel')}>
                <X size={13} />
                Annuler
              </button>
            )}
          </div>
        </div>
        {error && (
          <p className="wf-inline-error" role="alert">
            {error}
          </p>
        )}
      </div>
      {open && (
        <MissionDetail
          api={api}
          id={mission.id}
          runtime={runtime}
          pilotRequest={pilotRequest.current}
          revision={revision}
          onChanged={onChanged}
        />
      )}
    </article>
  );
}

function MissionDetail({
  api,
  id,
  runtime,
  pilotRequest,
  revision,
  onChanged,
}: {
  api: Api;
  id: string;
  runtime: Row | null;
  pilotRequest: string;
  revision: number;
  onChanged: () => void;
}) {
  const detail = useRecord('/missions/' + id, api, revision);
  const mission = detail.record;
  const [filter, setFilter] = useState('next'),
    [page, setPage] = useState(1);
  const [pilotBusy, setPilotBusy] = useState(false),
    [pilotError, setPilotError] = useState(''),
    [pilotNotice, setPilotNotice] = useState('');
  const allSteps = mission?.steps || [];
  const steps = allSteps.filter(
    (step: Row) =>
      filter === 'all' ||
      (filter === 'next' ? !['completed', 'cancelled'].includes(step.status) : step.status === filter),
  );
  const pages = Math.max(1, Math.ceil(steps.length / 10)),
    visible = steps.slice((page - 1) * 10, page * 10);
  const pilot = runtime?.pilot || {},
    activePilot = pilot.mission_id === id;
  const pilotStats =
    runtime?.stats || runtime?.jobs?.find((job: Row) => job.kind === 'pilot_snapshot')?.result;
  const canObserve =
    mission?.kind === 'prepare_prospects' &&
    mission.target_count >= 25 &&
    mission.target_count <= 50 &&
    mission.accepted_count >= 25;
  useEffect(() => setPage(1), [filter, id]);
  async function observePilot() {
    setPilotBusy(true);
    setPilotError('');
    setPilotNotice('');
    try {
      await api('/runtime/pilot', 'POST', {request_id: pilotRequest, mission_id: id});
      setPilotNotice(
        'Observation démarrée pour 7 jours effectifs. Les résultats resteront à vérifier pendant toute la période.',
      );
      onChanged();
    } catch (reason) {
      setPilotError(errorText(reason));
    } finally {
      setPilotBusy(false);
    }
  }
  return (
    <div className="wf-mission-detail">
      {detail.error ? (
        <Feedback error={detail.error} />
      ) : !mission ? (
        <p className="wf-loading">Chargement des étapes…</p>
      ) : (
        <>
          {canObserve && (
            <div className="wf-pilot">
              <div>
                <strong>
                  {activePilot ? 'Pilote en observation' : 'Observer ce pilote pendant 7 jours effectifs'}
                </strong>
                <small>
                  {activePilot
                    ? `${Math.floor((pilot.elapsed_seconds || 0) / 86400)} jour(s) écoulé(s) · les résultats restent provisoires.`
                    : pilot.mission_id
                      ? 'Un autre pilote est déjà en observation.'
                      : 'Mesure les appels, suites, interventions et coûts sans présumer du résultat.'}
                </small>
                {activePilot && pilotStats && (
                  <span>
                    {pilotStats.calls_attempted || 0} appels · {pilotStats.calls_with_report || 0} comptes
                    rendus · {pilotStats.human_interventions_needed || 0} intervention(s)
                  </span>
                )}
              </div>
              {!activePilot && !pilot.mission_id && (
                <button className="primary" disabled={pilotBusy} onClick={observePilot}>
                  {pilotBusy ? 'Démarrage…' : 'Observer ce pilote'}
                </button>
              )}
            </div>
          )}
          {pilotError && <Feedback error={pilotError} />} {pilotNotice && <Feedback notice={pilotNotice} />}{' '}
          {allSteps.length ? (
            <>
              <div className="wf-step-controls">
                <label>
                  Afficher
                  <select value={filter} onChange={(event) => setFilter(event.target.value)}>
                    <option value="next">Prochaines actions</option>
                    <option value="all">Toutes les étapes</option>
                    <option value="waiting_human">Décision attendue</option>
                    <option value="running">En cours</option>
                    <option value="pending">À faire</option>
                    <option value="waiting_data">Données en attente</option>
                    <option value="unresolved">À résoudre</option>
                    <option value="completed">Terminées</option>
                    <option value="failed">En échec</option>
                    <option value="cancelled">Annulées</option>
                  </select>
                </label>
                <span>
                  {steps.length} sur {allSteps.length}
                </span>
              </div>
              {visible.length ? (
                <ol className="wf-steps">
                  {visible.map((step: Row) => (
                    <MissionStep key={step.id} api={api} missionId={id} step={step} onChanged={onChanged} />
                  ))}
                </ol>
              ) : (
                <Blank title="Aucune étape dans ce filtre" />
              )}
              {steps.length > 10 && (
                <nav className="wf-pages" aria-label="Pagination des étapes">
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
            </>
          ) : (
            <Blank title="Aucune étape enregistrée">La mission sera détaillée dès sa prise en charge.</Blank>
          )}
        </>
      )}
    </div>
  );
}

function MissionStep({
  api,
  missionId,
  step,
  onChanged,
}: {
  api: Api;
  missionId: string;
  step: Row;
  onChanged: () => void;
}) {
  const [note, setNote] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const canResolve = ['waiting_human', 'failed', 'unresolved'].includes(step.status);
  async function decide(action: 'complete' | 'cancel' | 'resume') {
    setBusy(true);
    setError('');
    try {
      await api(`/missions/${missionId}/steps/${step.id}/decision`, 'POST', {
        action,
        note,
        expected_updated_at: step.updated_at,
      });
      setNote('');
      onChanged();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className={'wf-step ' + step.status}>
      <span className="wf-step-icon">
        {step.status === 'completed' ? (
          <CircleCheck size={16} />
        ) : step.status === 'failed' ? (
          <RotateCcw size={15} />
        ) : (
          <Clock3 size={15} />
        )}
      </span>
      <div>
        <header>
          <div>
            <small>{stepKinds[step.kind] || step.kind.replaceAll('_', ' ')}</small>
            <strong>{step.title}</strong>
          </div>
          <span className={'wf-state ' + step.status}>{stepStates[step.status] || step.status}</span>
        </header>
        {step.result?.summary && <p>{step.result.summary}</p>}
        {step.due_at && <small>Échéance {when(step.due_at)}</small>}
        {step.kind === 'callback' && !['completed', 'cancelled'].includes(step.status) && (
          <div className="wf-step-guidance">
            <p>L’étape sera validée par un appel réellement enregistré depuis l’espace d’appel.</p>
            {step.account_id && (
              <a
                href={`/contacts?company=${encodeURIComponent(step.account_id)}&mission_step=${encodeURIComponent(step.id)}`}
              >
                Ouvrir la fiche et appeler
              </a>
            )}
          </div>
        )}
        {canResolve && step.kind !== 'callback' && (
          <div className="wf-step-decision">
            <textarea
              aria-label={'Note pour ' + step.title}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              minLength={5}
              maxLength={3000}
              rows={2}
              placeholder={
                step.kind === 'human_decision'
                  ? 'Décision prise et motif…'
                  : 'Correction effectuée avant reprise…'
              }
            />
            <div>
              {step.kind === 'human_decision' && (
                <button
                  className="primary"
                  disabled={busy || note.trim().length < 5}
                  onClick={() => decide('complete')}
                >
                  <CircleCheck size={13} />
                  Décision prise
                </button>
              )}
              <button disabled={busy || note.trim().length < 5} onClick={() => decide('resume')}>
                <Play size={13} />
                Reprendre
              </button>
              <button disabled={busy || note.trim().length < 5} onClick={() => decide('cancel')}>
                <X size={13} />
                Annuler
              </button>
            </div>
          </div>
        )}
        {error && (
          <p className="wf-inline-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </li>
  );
}
