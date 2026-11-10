import {CallWorkspace} from './CallWorkspace';
import {callBlockedReason} from './callPolicy';
import {MissionPanel} from './MissionPanel';
import {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, Phone, X} from 'lucide-react';
import {
  ListSummary,
  ListTable,
  ListToolbar,
  useListState,
  useServerList,
  type Api,
  type Row,
  type Column,
} from './ListTable';
import type {OpenContact} from './ListPages';
import './unified-prospects.css';
import {errorText, type Upload} from './api';

const date = (value?: string) =>
  value ? new Date(value).toLocaleDateString('fr-FR', {day: 'numeric', month: 'short'}) : 'Jamais';
const stages = {
  pending: 'À qualifier',
  checking: 'Qualification en cours',
  review: 'À approfondir',
  qualified: 'Cible retenue',
  contact_found: 'Interlocuteur identifié',
  ready: 'À appeler',
  not_fit: 'Hors cible',
  excluded: 'Exclus',
};
const proofLabels: Row = {
  company_phone: 'Téléphone de l’entreprise',
  company_email: 'Email de l’entreprise',
  phone: 'Téléphone du contact',
  email: 'Email du contact',
  siren: 'SIREN',
  employee_range: 'Tranche d’effectif',
  employee_count: 'Effectif',
  identity: 'Identité',
  role: 'Fonction',
  b2b: 'Activité B2B',
  hubspot_status: 'Usage du CRM',
  research: 'Recherche',
  relevance: 'Pertinence',
};
const checkLabels: Row = {match: 'Documenté', unknown: 'À vérifier', mismatch: 'Hors cible'};

export function UnifiedProspects({
  api,
  data,
  onContact,
  upload,
}: {
  api: Api;
  data: Row;
  onContact: OpenContact;
  upload: Upload;
}) {
  const [state, change] = useListState('all-prospects-v3', {
    size: 25,
    sort: 'score',
    direction: 'desc',
    compact: true,
    filters: {},
    hidden: [],
  });
  const [selected, setSelected] = useState<string | null>(
      new URLSearchParams(window.location.search).get('company'),
    ),
    [revision, setRevision] = useState(0);
  const server = useServerList(
    'prospects',
    state,
    change,
    api,
    String(data.updated_at || '') + ':' + revision,
  );
  const result = server.result,
    summary = result?.summary || {};
  useEffect(() => {
    const timer = setInterval(() => setRevision((x) => x + 1), 30000);
    return () => clearInterval(timer);
  }, []);
  const columns: Column[] = [
    {
      key: 'name',
      label: 'Prospect / entreprise',
      sort: 'name',
      fixed: true,
      width: 185,
      render: (r) => (
        <div className="lt-cell-stack">
          <button className="prospect-company-link" onClick={() => setSelected(r.id)}>
            {r.name}
          </button>
          <small>{r.domain}</small>
        </div>
      ),
    },
    {
      key: 'person',
      label: 'Interlocuteur',
      width: 145,
      render: (r) =>
        r.contact ? (
          <div className="lt-cell-stack">
            <a href={'/contacts/' + r.contact.id} onClick={(e) => onContact(e, r.contact.id)}>
              {r.contact.first_name} {r.contact.last_name}
            </a>
            <small>{r.contact.role || 'Fonction à vérifier'}</small>
          </div>
        ) : (
          <span className="lt-muted">À identifier</span>
        ),
    },
    {
      key: 'phone',
      label: 'Coordonnées',
      width: 170,
      render: (r) => (
        <div className="lt-cell-stack">
          {r.phone ? (
            r.can_call ? (
              <a href={'tel:' + r.phone}>{r.phone}</a>
            ) : (
              <span>{r.phone}</span>
            )
          ) : (
            <span className="lt-muted">À récupérer</span>
          )}
          <small>{r.phone_label}</small>
          {(r.email || r.company_email) && (
            <small title={r.email ? 'Email du contact' : 'Email publié de l’entreprise'}>
              {r.email || r.company_email}
            </small>
          )}
        </div>
      ),
    },
    {
      key: 'score',
      label: 'Score',
      sort: 'score',
      width: 65,
      render: (r) => (
        <span className={'prospect-score ' + (r.score >= 65 ? 'high' : '')}>
          {r.score}
          <small>/100</small>
        </span>
      ),
    },
    {
      key: 'stage',
      label: 'Qualification',
      width: 125,
      render: (r) => (
        <span
          className={
            'lt-badge lt-badge-' +
            (r.stage === 'ready'
              ? 'qualified'
              : r.stage === 'not_fit' || r.stage === 'excluded'
                ? 'excluded'
                : 'candidate')
          }
        >
          {r.qualification_label || r.stage_label}
        </span>
      ),
    },
    {
      key: 'commercial_status',
      label: 'Statut commercial',
      width: 165,
      render: (r) => (
        <div className="lt-cell-stack">
          <span
            className={
              'lt-badge lt-badge-' +
              (r.commercial_status === 'interested'
                ? 'qualified'
                : r.commercial_status === 'stopped' || r.commercial_status === 'bounced'
                  ? 'excluded'
                  : 'candidate')
            }
          >
            {r.commercial_status_label}
          </span>
          <small>{date(r.commercial_status_at)}</small>
        </div>
      ),
    },
    {
      key: 'activity',
      label: 'Derniers échanges',
      width: 140,
      render: (r) => (
        <div className="lt-cell-stack">
          <span>Appel : {date(r.last_call_at)}</span>
          <small>Email : {date(r.last_email_at)}</small>
        </div>
      ),
    },
    {
      key: 'next',
      label: 'Prochaine action',
      width: 175,
      render: (r) => <span className="lt-muted">{r.next_action}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (r) => (
        <button
          className="lt-row-link"
          aria-label={'Préparer le contact de ' + r.name}
          onClick={() => setSelected(r.id)}
        >
          <ArrowUpRight size={16} />
        </button>
      ),
    },
  ];
  return (
    <>
      <ListSummary
        items={[
          [summary.total, 'prospects dans l’espace'],
          [summary.with_phone, 'avec un téléphone d’entreprise'],
          [summary.with_contact, 'avec un interlocuteur'],
          [summary.checked, 'entreprises examinées'],
        ]}
      />
      <section className="lt-panel">
        <ListToolbar
          state={state}
          change={change}
          columns={columns}
          filters={[
            {key: 'stage', label: 'Qualification', options: stages},
            {
              key: 'campaign_id',
              label: 'Cible de la campagne',
              options: Object.fromEntries((data.campaigns || []).map((c: Row) => [c.id, c.name])),
            },
          ]}
          placeholder="Entreprise, domaine, interlocuteur ou téléphone…"
          searchLabel="Rechercher parmi tous les prospects"
        />
        <ListTable
          title="Prospects"
          rows={result?.items || []}
          total={result?.total || 0}
          overall={summary.total}
          state={state}
          change={change}
          columns={columns}
          loading={server.loading}
          error={server.error}
          onRetry={server.retry}
        />
      </section>
      {selected && (
        <CompanyProspect
          id={selected}
          api={api}
          upload={upload}
          campaignId={state.filters.campaign_id || undefined}
          campaigns={data.campaigns || []}
          onSaved={() => setRevision((x) => x + 1)}
          close={() => setSelected(null)}
          onContact={onContact}
        />
      )}
    </>
  );
}

function CompanyProspect({
  id,
  api,
  upload,
  campaignId,
  campaigns,
  close,
  onSaved,
  onContact,
}: {
  id: string;
  api: Api;
  upload: Upload;
  campaignId?: string;
  campaigns: Row[];
  close: () => void;
  onSaved: () => void;
  onContact: OpenContact;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    requestId = useRef(crypto.randomUUID());
  const [row, setRow] = useState<Row | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [recording, setRecording] = useState(false),
    [notice, setNotice] = useState('');
  const load = () =>
    api(
      '/prospect-pipeline/accounts/' +
        id +
        (campaignId ? '?campaign_id=' + encodeURIComponent(campaignId) : ''),
    )
      .then(setRow)
      .catch((e) => setError(errorText(e)));
  useEffect(() => {
    dialog.current?.showModal();
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reloads when the selected prospect changes; load is recreated on every render
  }, [id]);
  async function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget));
    setBusy(true);
    try {
      await api('/prospect-pipeline/accounts/' + id + '/calls', 'POST', {
        ...f,
        request_id: requestId.current,
      });
      requestId.current = crypto.randomUUID();
      setRecording(false);
      setNotice('Appel enregistré dans l’historique.');
      await load();
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      className="prospect-drawer company-prospect"
      ref={dialog}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      aria-labelledby="company-prospect-title"
    >
      <header>
        <div>
          <small>PROSPECT</small>
          <h2 id="company-prospect-title">{row?.name || 'Chargement…'}</h2>
          <p>{row?.domain}</p>
        </div>
        <button className="icon" aria-label="Fermer le prospect" onClick={close}>
          <X size={20} />
        </button>
      </header>
      <div className="prospect-drawer-body">
        {error && (
          <p role="alert" className="alert error">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="alert success">
            {notice}
          </p>
        )}
        {row && (
          <>
            <div className="prospect-priority">
              <span className="prospect-score">
                {row.score}
                <small>/100</small>
              </span>
              <div>
                <strong>{row.stage_label}</strong>
                <p>{row.next_action}</p>
                <small>
                  Priorité calculée sur la cible, les coordonnées et la fraîcheur des sources. L’intention
                  d’achat reste à confirmer.
                </small>
              </div>
            </div>
            <CallWorkspace
              key={id}
              initialCallId={new URLSearchParams(window.location.search).get('call') || undefined}
              missionStepId={new URLSearchParams(window.location.search).get('mission_step') || undefined}
              defaultDestination={row.phone || ''}
              blockedReason={callBlockedReason(row)}
              api={api}
              upload={upload}
              accountId={id}
              contactId={row.contact?.id}
              campaignId={campaignId || row.contact?.campaign_id || row.profile?.campaign_id}
              onChanged={() => {
                void load();
                onSaved();
              }}
            />
            <details>
              <summary>Objectifs de ce prospect</summary>
              <MissionPanel api={api} accountId={id} campaignId={campaignId} campaigns={campaigns} />
            </details>
            <div className="prospect-coordinate">
              <Phone size={18} />
              <div>
                <strong>{row.phone || 'Téléphone à récupérer'}</strong>
                <small>{row.phone_label}</small>
              </div>
              {row.can_call && (
                <a className="button" href={'tel:' + row.phone}>
                  Appeler
                </a>
              )}
            </div>
            {row.company_email && !row.email && (
              <p>
                <a href={'mailto:' + row.company_email}>{row.company_email}</a>
                <br />
                <small>Email publié de l’entreprise · non vérifié</small>
              </p>
            )}
            <section>
              <h3>Interlocuteur</h3>
              {row.contact ? (
                <p>
                  <a
                    href={'/contacts/' + row.contact.id}
                    onClick={(e) => {
                      close();
                      onContact(e, row.contact.id);
                    }}
                  >
                    {row.contact.first_name} {row.contact.last_name}
                  </a>
                  <br />
                  <small>
                    {row.contact.role} · {row.email || 'Email nominatif à rechercher'}
                  </small>
                </p>
              ) : (
                <p className="muted">
                  L’entreprise est importée. L’identité du dirigeant ou du responsable commercial reste à
                  documenter.
                </p>
              )}
            </section>
            <details>
              <summary>Qualification et critères</summary>
              {row.profile?.facts?.checks?.length ? (
                <ul className="prospect-checks">
                  {row.profile.facts.checks.map((c: Row) => (
                    <li key={c.key}>
                      <span>{c.label}</span>
                      <strong>{checkLabels[c.status]}</strong>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">
                  Analyse du site officiel et rapprochement Sirene dans la file automatique.
                </p>
              )}
              {row.profile?.facts?.registry && (
                <small>
                  Effectif publié : {row.profile.facts.size_label} ·{' '}
                  {row.profile.facts.registry.size_year || 'année inconnue'} · SIREN {row.siren}
                </small>
              )}
              {row.profile?.facts?.agent_reason && <p>{row.profile.facts.agent_reason}</p>}
            </details>
            <section>
              <h3>Préparer l’appel</h3>
              <p className="prospect-call-brief">
                {row.profile?.call_brief ||
                  'Identifier le bon interlocuteur, vérifier le contexte et convenir de la prochaine action.'}
              </p>
              {row.can_call && (
                <button className="primary" onClick={() => setRecording(!recording)}>
                  Consigner un appel effectué
                </button>
              )}
            </section>
            {recording && (
              <form className="prospect-call-form" onSubmit={save}>
                <label className="field">
                  <span>Résultat de l’appel</span>
                  <select name="outcome">
                    <option value="not_reached">Non joint</option>
                    <option value="switchboard">Passage au standard</option>
                    <option value="identified">Interlocuteur identifié</option>
                    <option value="no_need">Pas de besoin</option>
                    <option value="wrong_number">Numéro incorrect</option>
                  </select>
                </label>
                <label className="field">
                  <span>Notes et suite convenue</span>
                  <textarea name="notes" rows={3} required maxLength={5000} />
                </label>
                <button className="primary" disabled={busy}>
                  Enregistrer
                </button>
              </form>
            )}
            <details>
              <summary>Sources et preuves ({row.evidence?.length || 0})</summary>
              {row.evidence?.map((e: Row) => (
                <div className="prospect-proof" key={e.id}>
                  <strong>{proofLabels[e.field] || e.field.replace(/_/g, ' ')}</strong>
                  <p>{e.value}</p>
                  <a href={e.source_url} target="_blank" rel="noreferrer">
                    Consulter la source
                  </a>
                  <small> · {date(e.observed_at)}</small>
                </div>
              ))}
            </details>
            <a className="button" href={'/companies/' + id}>
              Ouvrir la fiche complète
            </a>
          </>
        )}
      </div>
    </dialog>
  );
}
