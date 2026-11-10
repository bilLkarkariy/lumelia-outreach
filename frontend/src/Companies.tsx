import {useRef, useState} from 'react';
import {ChevronDown, ChevronUp, ExternalLink, Search} from 'lucide-react';
import {errorText, type Api, type Row} from './api';

const states: Record<string, string> = {prospect: 'À explorer', customer: 'Client', excluded: 'Exclue'};
const confidence: Record<string, string> = {
  confirmed: 'Confirmé par la source',
  signal: 'Indice',
  unverified: 'À vérifier',
};
const hubspot: Record<string, string> = {
  unknown: 'Inconnu',
  signal: 'Signal public',
  confirmed: 'Confirmé en échange',
  absent: 'Absent',
};

export function Companies({accounts, api, onEdit}: {accounts: Row[]; api: Api; onEdit: (row: Row) => void}) {
  const [query, setQuery] = useState(''),
    [filter, setFilter] = useState('all');
  const [details, setDetails] = useState<Record<string, Row>>({}),
    [expanded, setExpanded] = useState('');
  const [loading, setLoading] = useState(''),
    [error, setError] = useState('');
  const requestRef = useRef(0);
  const rows = accounts.filter(
    (row) =>
      (filter === 'all' || row.lifecycle === filter) &&
      (row.name + ' ' + row.domain).toLowerCase().includes(query.toLowerCase()),
  );
  async function show(row: Row) {
    const request = ++requestRef.current;
    if (expanded === row.id) {
      setExpanded('');
      setLoading('');
      return;
    }
    setExpanded(row.id);
    setError('');
    setLoading(row.id);
    try {
      const detail = await api('/accounts/' + row.id);
      if (request === requestRef.current) setDetails((previous) => ({...previous, [row.id]: detail}));
    } catch (e) {
      if (request === requestRef.current) setError(errorText(e));
    } finally {
      if (request === requestRef.current) setLoading('');
    }
  }
  return (
    <section className="panel company-panel">
      <header>
        <div className="search">
          <Search size={17} />
          <input
            aria-label="Rechercher une entreprise"
            placeholder="Entreprise ou domaine…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <select
          aria-label="Filtrer les entreprises"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">Toutes les entreprises</option>
          <option value="prospect">À explorer</option>
          <option value="excluded">Exclues</option>
          <option value="customer">Clients</option>
        </select>
        <small>
          {rows.length} / {accounts.length} entreprises
        </small>
      </header>
      {!rows.length && (
        <div className="padded">
          <p>
            {query || filter !== 'all'
              ? 'Aucune entreprise ne correspond à ce filtre.'
              : 'Les entreprises découvertes apparaîtront ici, même avant la recherche de leurs interlocuteurs.'}
          </p>
        </div>
      )}
      {rows.map((row) => (
        <article className="company-row" key={row.id}>
          <div className="company-summary">
            <div>
              <strong>{row.name}</strong>
              <small>
                {row.domain} · {row.industry || 'Activité à vérifier'}
              </small>
            </div>
            <div className="company-facts">
              <span>{states[row.lifecycle] || row.lifecycle}</span>
              <small>
                {row.contact_count || 0} contact(s) · {row.evidence_count || 0} preuve(s)
              </small>
            </div>
            <button
              aria-label={(expanded === row.id ? 'Fermer' : 'Ouvrir') + ' la fiche de ' + row.name}
              aria-expanded={expanded === row.id}
              aria-controls={'company-' + row.id}
              onClick={() => show(row)}
            >
              {expanded === row.id ? 'Fermer' : 'Ouvrir'}{' '}
              {expanded === row.id ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            </button>
          </div>
          {expanded === row.id && (
            <div className="company-detail padded" id={'company-' + row.id}>
              {loading === row.id ? (
                <p>Chargement de la fiche…</p>
              ) : error ? (
                <p role="alert" className="alert error">
                  {error}
                </p>
              ) : (
                details[row.id] && (
                  <>
                    <dl className="company-attributes">
                      <div>
                        <dt>Pays enregistré</dt>
                        <dd>{row.country}</dd>
                      </div>
                      <div>
                        <dt>Effectif renseigné</dt>
                        <dd>{row.employee_count ?? 'Inconnu'}</dd>
                      </div>
                      <div>
                        <dt>HubSpot commercial</dt>
                        <dd>{hubspot[row.hubspot_status] || 'Inconnu'}</dd>
                      </div>
                    </dl>
                    {row.outreach_paused && (
                      <p className="callout">Prospection suspendue · {row.pause_reason}</p>
                    )}
                    <p style={{whiteSpace: 'pre-wrap'}}>{row.notes || 'Aucune note complémentaire.'}</p>
                    <h3>Sources et preuves</h3>
                    {details[row.id].evidence.length ? (
                      details[row.id].evidence.map((entry: Row) => (
                        <div className="company-evidence" key={entry.id}>
                          <strong>{entry.field}</strong>
                          <p>{entry.value}</p>
                          <small>
                            {confidence[entry.confidence] || entry.confidence} ·{' '}
                            {new Date(entry.observed_at).toLocaleDateString('fr-FR')}
                          </small>
                          {/^https?:\/\//i.test(entry.source_url) && (
                            <a href={entry.source_url} target="_blank" rel="noopener noreferrer">
                              Consulter la source <ExternalLink size={13} />
                            </a>
                          )}
                        </div>
                      ))
                    ) : (
                      <p>Aucune preuve attachée à cette entreprise.</p>
                    )}
                    {details[row.id].contacts.length > 0 && (
                      <>
                        <h3>Interlocuteurs connus</h3>
                        <ul>
                          {details[row.id].contacts.map((contact: Row) => (
                            <li key={contact.id}>
                              {contact.first_name} {contact.last_name} · {contact.role || 'Fonction inconnue'}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                    <button onClick={() => onEdit(row)}>Modifier l’entreprise</button>
                  </>
                )
              )}
            </div>
          )}
        </article>
      ))}
    </section>
  );
}
