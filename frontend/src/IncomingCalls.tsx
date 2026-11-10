import {useEffect, useRef, useState} from 'react';
import {Link2, PhoneIncoming, RefreshCw, Search, UserRoundCheck, UserRoundX} from 'lucide-react';
import {CallDetail} from './CallWorkspace';
import {Blank, Feedback, useRecord, when, type Api, type Row} from './WorkspaceUI';
import {errorText} from './api';

const inboundStates: Row = {
  dispatching: 'Transfert en cours',
  connecting: 'Connexion',
  operator_ringing: 'Votre portable sonne',
  awaiting_operator: 'Acceptation attendue',
  ringing: 'Sonnerie',
  in_progress: 'En cours',
  completed: 'Terminé',
  failed: 'Échec',
  canceled: 'Annulé',
  uncertain: 'À vérifier',
  missed: 'Manqué',
};

function callerNumber(row: Row) {
  return row.caller_number || row.from_number || row.source_number || row.destination || 'Numéro masqué';
}

function identity(row: Row) {
  if (row.contact_name && row.account_name) return `${row.contact_name} · ${row.account_name}`;
  return row.contact_name || row.account_name || '';
}

export function IncomingCalls({api, retentionDays = 30}: {api: Api; retentionDays?: number}) {
  const incomingId = useRef(new URLSearchParams(window.location.search).get('incoming') || '').current;
  const [revision, setRevision] = useState(0),
    [selected, setSelected] = useState(incomingId),
    [open, setOpen] = useState(Boolean(incomingId));
  const [targeted, setTargeted] = useState<Row | null>(null);
  const [query, setQuery] = useState(''),
    [candidates, setCandidates] = useState<Row[]>([]);
  const [searching, setSearching] = useState(false),
    [linking, setLinking] = useState('');
  const [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const calls = useRecord('/calls?source=inbound&limit=20', api, revision);
  const rows: Row[] = calls.record?.items || [];
  const visibleRows = targeted && !rows.some((row) => row.id === targeted.id) ? [targeted, ...rows] : rows;
  const selectedRow = visibleRows.find((row) => row.id === selected);

  useEffect(() => {
    if (!incomingId) return;
    let alive = true;
    api('/calls/' + incomingId)
      .then((row) => {
        if (!alive) return;
        if (row.source !== 'inbound') throw new Error('Cet appel ne fait pas partie des appels entrants.');
        setTargeted(row);
      })
      .catch((reason: unknown) => {
        if (alive) setError(errorText(reason) || 'Impossible de charger cet appel entrant.');
      });
    return () => {
      alive = false;
    };
  }, [incomingId, revision, api]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => setRevision((value) => value + 1), 5000);
    return () => window.clearInterval(timer);
  }, [open]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setCandidates([]);
      setSearching(false);
      return;
    }
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await api('/calls/link-candidates?' + new URLSearchParams({q: query.trim()}));
        if (alive) {
          setCandidates(result.items || []);
          setError('');
        }
      } catch (reason) {
        if (alive) {
          setCandidates([]);
          setError(errorText(reason) || 'La recherche de contacts a échoué.');
        }
      } finally {
        if (alive) setSearching(false);
      }
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, api]);

  async function linkContact(contactId: string) {
    if (!selectedRow || selectedRow.contact_id) return;
    setLinking(contactId);
    setError('');
    setNotice('');
    try {
      const response = await api('/calls/' + selectedRow.id + '/link', 'POST', {contact_id: contactId});
      const linked = response.call || response;
      const processingStatus = linked.processing_status || response.processing_status;
      const noAudio =
        processingStatus === 'not_recorded' ||
        response.has_audio === false ||
        response.recording_available === false ||
        linked.has_audio === false ||
        linked.recording_available === false;
      const processingContinues =
        response.report_resumed === true ||
        ['queued', 'processing', 'transcribing', 'transcribed'].includes(processingStatus);
      setNotice(
        noAudio
          ? 'Appel rattaché. Aucun compte rendu audio n’est attendu pour cet appel sans enregistrement.'
          : processingContinues
            ? 'Appel rattaché. Le traitement du compte rendu se poursuit.'
            : 'Appel rattaché.',
      );
      if (linked.id === selectedRow.id) setTargeted(linked);
      setQuery('');
      setCandidates([]);
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(errorText(reason) || 'Le rattachement a échoué.');
    } finally {
      setLinking('');
    }
  }

  return (
    <details
      className="wf-incoming-calls"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span>
          <PhoneIncoming size={15} />
          <strong>Appels entrants</strong>
        </span>
        <small>
          {visibleRows.length
            ? `${visibleRows.length} récent${visibleRows.length > 1 ? 's' : ''}`
            : calls.loading
              ? 'Chargement…'
              : 'Aucun appel récent'}
        </small>
      </summary>
      <div className="wf-incoming-body">
        <div className="wf-incoming-tools">
          <small>Actualisation automatique pendant l’ouverture</small>
          <button type="button" onClick={() => setRevision((value) => value + 1)} disabled={calls.loading}>
            <RefreshCw size={13} />
            Actualiser
          </button>
        </div>
        <Feedback error={error || calls.error} notice={notice} />
        {visibleRows.length ? (
          <ol className="wf-incoming-list">
            {visibleRows.map((row) => {
              const known = Boolean(row.contact_id || row.account_id || identity(row));
              return (
                <li key={row.id} className={selected === row.id ? 'active' : ''}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelected(selected === row.id ? '' : row.id);
                      setQuery('');
                      setCandidates([]);
                      setError('');
                      setNotice('');
                    }}
                    aria-expanded={selected === row.id}
                  >
                    <span className="wf-incoming-icon">
                      {known ? <UserRoundCheck size={15} /> : <UserRoundX size={15} />}
                    </span>
                    <span>
                      <strong>{callerNumber(row)}</strong>
                      <small>
                        {identity(row) || 'Appelant inconnu · rattachement nécessaire'} ·{' '}
                        {when(row.started_at || row.created_at)}
                      </small>
                    </span>
                    <span className={'wf-state ' + row.status}>
                      {inboundStates[row.status] || row.status}
                    </span>
                  </button>
                  {selected === row.id && (
                    <div className="wf-incoming-expanded">
                      {!row.contact_id && (
                        <form className="wf-link-call" onSubmit={(event) => event.preventDefault()}>
                          <label className="ws-field">
                            <span>Rattacher à un contact</span>
                            <div className="wf-contact-search">
                              <Search size={14} />
                              <input
                                type="search"
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="Nom, entreprise ou téléphone"
                                minLength={2}
                                maxLength={120}
                                autoComplete="off"
                              />
                            </div>
                          </label>
                          {query.trim().length > 0 && query.trim().length < 2 && (
                            <small>Saisissez au moins 2 caractères.</small>
                          )}
                          {searching && <p>Recherche des contacts…</p>}
                          {!searching && query.trim().length >= 2 && !candidates.length && !error && (
                            <p>Aucun contact correspondant.</p>
                          )}
                          {candidates.length > 0 && (
                            <ul className="wf-link-candidates">
                              {candidates.map((candidate) => (
                                <li key={candidate.id}>
                                  <div>
                                    <strong>{candidate.name}</strong>
                                    <small>
                                      {candidate.account_name || 'Sans entreprise'}
                                      {candidate.phone ? ` · ${candidate.phone}` : ''}
                                    </small>
                                  </div>
                                  <button
                                    type="button"
                                    disabled={Boolean(linking)}
                                    onClick={() => linkContact(candidate.id)}
                                  >
                                    <Link2 size={13} />
                                    {linking === candidate.id ? 'Rattachement…' : 'Rattacher'}
                                  </button>
                                </li>
                              ))}
                            </ul>
                          )}
                        </form>
                      )}
                      <CallDetail api={api} id={row.id} revision={revision} retentionDays={retentionDays} />
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        ) : (
          <Blank title={calls.loading ? 'Chargement des appels entrants…' : 'Aucun appel entrant'}>
            {calls.loading
              ? 'Les derniers appels arrivent.'
              : 'Les appels reçus, reconnus ou inconnus, apparaîtront ici.'}
          </Blank>
        )}
      </div>
    </details>
  );
}
