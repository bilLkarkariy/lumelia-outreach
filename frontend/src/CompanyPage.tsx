import {useEffect, useRef, useState, type FormEvent} from 'react';
import {ArrowUpRight, Building2, Check, FileText, Pencil, Plus, X} from 'lucide-react';
import {markets} from './Locales';
import {ContactsList} from './ListPages';
import {TableBadge} from './ListTable';
import {
  WorkspaceHeader,
  WorkspaceTabs,
  Panel,
  Field,
  Feedback,
  Blank,
  Sources,
  Events,
  RouteLink,
  useRecord,
  money,
  when,
  personName,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {errorText} from './api';

type Props = {
  id: string;
  section: string;
  data: Row;
  api: Api;
  navigate: Navigate;
  onOpen: (name: string, row?: Row) => void;
  onRefresh: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  discardVersion: number;
  admin: boolean;
  busy: boolean;
  onAction: (fn: () => Promise<unknown>, success?: string) => Promise<void>;
};
const sections = {
  overview: 'Vue d’ensemble',
  contacts: 'Interlocuteurs',
  sources: 'Sources & preuves',
  history: 'Historique',
  opportunities: 'Opportunités',
};
const statusNames: Row = {
  prospect: 'À explorer',
  customer: 'Cliente',
  excluded: 'Exclue',
  unknown: 'À vérifier',
  signal: 'Signal public',
  confirmed: 'Confirmé',
  absent: 'Absent',
  conversation: 'En conversation',
  meeting: 'Rendez-vous',
  proposal: 'Proposition',
  won: 'Gagnée',
  lost: 'Perdue',
};
export function CompanyPage({
  id,
  section,
  data,
  api,
  navigate,
  onOpen,
  onRefresh,
  onDirtyChange,
  discardVersion,
  admin,
  busy: globalBusy,
  onAction,
}: Props) {
  const tab = section in sections ? section : 'overview',
    base = '/companies/' + id;
  const loaded = useRecord('/workspaces/accounts/' + id, api, data),
    row = loaded.record;
  const [editor, setEditor] = useState<'note' | 'evidence' | ''>(''),
    [note, setNote] = useState(''),
    [evidence, setEvidence] = useState<Row>({
      field: '',
      value: '',
      source_url: '',
      source_type: 'official_website',
      confidence: 'unverified',
    });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [historyPage, setHistoryPage] = useState(1);
  const requestKey = useRef(crypto.randomUUID());
  const history = useRecord('/workspaces/accounts/' + id + '/events?page=' + historyPage, api, data);
  const dirty =
    editor === 'note'
      ? !!note.trim()
      : editor === 'evidence'
        ? !!(evidence.field || evidence.value || evidence.source_url)
        : false;
  const dirtyRef = useRef(onDirtyChange);
  dirtyRef.current = onDirtyChange;
  useEffect(() => {
    dirtyRef.current(dirty);
    return () => dirtyRef.current(false);
  }, [dirty]);
  useEffect(() => {
    setEditor('');
    setNote('');
    setEvidence({
      field: '',
      value: '',
      source_url: '',
      source_type: 'official_website',
      confidence: 'unverified',
    });
    setHistoryPage(1);
    setError('');
    setNotice('');
  }, [id, discardVersion]);
  const name = row?.name;
  useEffect(() => {
    if (name) document.title = name + ' · Lumelia';
  }, [name]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api(
        editor === 'note' ? '/workspaces/accounts/' + id + '/notes' : '/accounts/' + id + '/evidence',
        'POST',
        editor === 'note' ? {text: note, request_id: requestKey.current} : evidence,
      );
      setNotice(editor === 'note' ? 'Note ajoutée à l’historique.' : 'Source enregistrée.');
      setEditor('');
      setNote('');
      requestKey.current = crypto.randomUUID();
      dirtyRef.current(false);
      loaded.refresh();
      history.refresh();
      await onRefresh();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  function begin(kind: 'note' | 'evidence') {
    setEditor(kind);
    setError('');
    setNotice('');
    requestKey.current = crypto.randomUUID();
    setEvidence({
      field: '',
      value: '',
      source_url: '',
      source_type: 'official_website',
      confidence: 'unverified',
    });
  }
  const url = (tab: string) => base + (tab === 'overview' ? '' : '?tab=' + tab);
  if (!row)
    return (
      <div className="ws-page">
        <WorkspaceHeader
          back="/companies"
          backLabel="Toutes les entreprises"
          eyebrow="ENTREPRISE"
          title={loaded.loading ? 'Chargement de l’entreprise…' : 'Entreprise indisponible'}
          navigate={navigate}
        />
        <Feedback error={loaded.error} />
        {!loaded.loading && <button onClick={loaded.refresh}>Réessayer</button>}
      </div>
    );
  const contacts = row.contacts as Row[],
    opportunities = row.opportunities as Row[];
  return (
    <article className="ws-page company-workspace">
      <WorkspaceHeader
        back="/companies"
        backLabel="Toutes les entreprises"
        eyebrow="FICHE ENTREPRISE"
        title={row.name}
        navigate={navigate}
        subtitle={
          <>
            <Building2 size={15} />
            {row.domain} · {markets[row.country] || row.country}
            <TableBadge value={row.lifecycle}>{statusNames[row.lifecycle]}</TableBadge>
          </>
        }
        actions={
          <>
            <button onClick={() => onOpen('account', row)} disabled={busy || !!editor}>
              <Pencil size={15} />
              Modifier
            </button>
            <button
              className="primary"
              onClick={() => onOpen('contact', {account: row})}
              disabled={busy || !!editor}
            >
              <Plus size={15} />
              Ajouter un interlocuteur
            </button>
          </>
        }
      />
      <WorkspaceTabs
        items={Object.entries(sections).map(([key, label]) => [
          key,
          label +
            (key === 'contacts'
              ? ' · ' + contacts.length
              : key === 'sources'
                ? ' · ' + row.evidence.length
                : ''),
          url(key),
        ])}
        current={tab}
        navigate={navigate}
        label="Rubriques de l’entreprise"
      />
      <Feedback error={error || loaded.error} notice={notice} />
      {editor && (
        <Panel
          title={editor === 'note' ? 'Ajouter une note' : 'Documenter une source'}
          action={
            <button
              className="ws-icon"
              aria-label="Fermer le formulaire"
              onClick={() => {
                setEditor('');
                setNote('');
                dirtyRef.current(false);
              }}
            >
              <X size={17} />
            </button>
          }
        >
          <form className="ws-form" onSubmit={save}>
            {editor === 'note' ? (
              <Field label="Note de recherche ou de conversation">
                <textarea
                  rows={4}
                  autoFocus
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  required
                  maxLength={5000}
                />
              </Field>
            ) : (
              <>
                <div className="ws-form-grid">
                  <Field label="Fait documenté">
                    <input
                      autoFocus
                      value={evidence.field}
                      onChange={(event) => setEvidence({...evidence, field: event.target.value})}
                      required
                      maxLength={100}
                      placeholder="Ex. Effectif ou CRM utilisé"
                    />
                  </Field>
                  <Field label="Niveau de preuve">
                    <select
                      value={evidence.confidence}
                      onChange={(event) => setEvidence({...evidence, confidence: event.target.value})}
                    >
                      <option value="unverified">À vérifier</option>
                      <option value="signal">Indice</option>
                      <option value="confirmed">Confirmé</option>
                    </select>
                  </Field>
                </div>
                <Field label="Observation">
                  <textarea
                    value={evidence.value}
                    onChange={(event) => setEvidence({...evidence, value: event.target.value})}
                    required
                    maxLength={5000}
                  />
                </Field>
                <div className="ws-form-grid">
                  <Field label="Type de source">
                    <select
                      value={evidence.source_type}
                      onChange={(event) => setEvidence({...evidence, source_type: event.target.value})}
                    >
                      <option value="official_website">Site officiel</option>
                      <option value="conversation">Conversation</option>
                      <option value="manual">Recherche manuelle</option>
                    </select>
                  </Field>
                  <Field label="URL de la source">
                    <input
                      type="url"
                      value={evidence.source_url}
                      onChange={(event) => setEvidence({...evidence, source_url: event.target.value})}
                      maxLength={1000}
                    />
                  </Field>
                </div>
              </>
            )}
            <div className="actions">
              <button className="primary" disabled={busy}>
                <Check size={15} />
                {busy ? 'Enregistrement…' : 'Enregistrer'}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setEditor('');
                  setNote('');
                  dirtyRef.current(false);
                }}
              >
                Annuler
              </button>
            </div>
          </form>
        </Panel>
      )}
      {tab === 'overview' && (
        <div className="ws-columns">
          <div>
            <Panel title="L’entreprise en un regard">
              <dl className="ws-facts">
                <div>
                  <dt>Activité</dt>
                  <dd>{row.industry || 'À préciser'}</dd>
                </div>
                <div>
                  <dt>Effectif</dt>
                  <dd>{row.employee_count == null ? 'À vérifier' : row.employee_count + ' personnes'}</dd>
                </div>
                <div>
                  <dt>CRM · HubSpot</dt>
                  <dd>{statusNames[row.hubspot_status]}</dd>
                </div>
                <div>
                  <dt>Identifiant SIREN</dt>
                  <dd>{row.siren || 'Non renseigné'}</dd>
                </div>
              </dl>
              {row.notes && (
                <div className="ws-prose">
                  <h3>Notes de la fiche</h3>
                  <p>{row.notes}</p>
                </div>
              )}
              {row.outreach_paused && (
                <p className="ws-context">Prospection suspendue · {row.pause_reason}</p>
              )}
              {row.review && (
                <div className="ws-review-summary">
                  <strong>
                    {row.review.data.decision === 'qualified'
                      ? 'Retenue après revue'
                      : 'Écartée de la sélection examinée'}
                  </strong>
                  <p>{row.review.summary}</p>
                  <small>
                    {when(row.review.created_at)}
                    {row.review.campaign_id
                      ? ' · ' +
                        (data.campaigns.find((c: Row) => c.id === row.review.campaign_id)?.name ||
                          'Cible liée')
                      : ' · Revue générale'}
                  </small>
                </div>
              )}
            </Panel>
            <Panel
              title="Dernière activité"
              action={
                <button onClick={() => begin('note')} disabled={!!editor}>
                  <Plus size={14} />
                  Note
                </button>
              }
            >
              <Events rows={row.events.slice(0, 5)} />
              {row.events_total > 5 && (
                <button className="ws-text-button" onClick={() => navigate(url('history'))}>
                  Tout l’historique
                  <ArrowUpRight size={14} />
                </button>
              )}
            </Panel>
          </div>
          <div>
            {row.missing.length > 0 && (
              <Panel title="À compléter" description="Les points encore inconnus sur cette fiche.">
                <ul className="ws-missing">
                  {row.missing.map((text: string) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
                <RouteLink
                  url={'/qualification?resource=accounts&record=' + id}
                  navigate={navigate}
                  className="ws-button-link"
                >
                  Examiner cette entreprise
                  <ArrowUpRight size={14} />
                </RouteLink>
              </Panel>
            )}
            <Panel
              title={'Interlocuteurs · ' + contacts.length}
              action={
                <button className="ws-text-button" onClick={() => navigate(url('contacts'))}>
                  Voir tous
                  <ArrowUpRight size={14} />
                </button>
              }
            >
              {contacts.length ? (
                <ul className="ws-people">
                  {contacts.slice(0, 4).map((contact) => (
                    <li key={contact.id}>
                      <RouteLink url={'/contacts/' + contact.id} navigate={navigate}>
                        <strong>{personName(contact)}</strong>
                        <small>{contact.role || 'Fonction à préciser'}</small>
                      </RouteLink>
                      <span>{contact.approved ? 'Validé' : 'À qualifier'}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Blank title="Décideur à identifier">
                  Les contacts ajoutés à cette entreprise apparaîtront ici.
                </Blank>
              )}
            </Panel>
            <Panel
              title="Sources récentes"
              action={
                <button className="ws-text-button" onClick={() => navigate(url('sources'))}>
                  Toutes
                  <ArrowUpRight size={14} />
                </button>
              }
            >
              <Sources rows={row.evidence} limit={2} />
            </Panel>
          </div>
        </div>
      )}
      {tab === 'contacts' && (
        <ContactsList
          data={data}
          api={api}
          onOpen={onOpen}
          onContact={(event, id) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button) return;
            event.preventDefault();
            navigate('/contacts/' + id);
          }}
          scope={{account_id: id}}
          preferenceKey={'contacts:account:' + id}
          admin={admin}
          busy={globalBusy}
          onAction={onAction}
        />
      )}
      {tab === 'sources' && (
        <Panel
          title="Sources & preuves"
          description="Observations de l’entreprise et de ses interlocuteurs."
          action={
            <button onClick={() => begin('evidence')} disabled={!!editor}>
              <Plus size={15} />
              Ajouter une source
            </button>
          }
        >
          <Sources rows={row.evidence} />
        </Panel>
      )}
      {tab === 'history' && (
        <Panel
          title="Historique de l’entreprise"
          action={
            <button onClick={() => begin('note')} disabled={!!editor}>
              <FileText size={15} />
              Ajouter une note
            </button>
          }
        >
          <Feedback error={history.error} />
          <Events rows={history.record?.items || []} />
          {(history.record?.total || 0) > 20 && (
            <div className="ws-pagination">
              <button disabled={historyPage <= 1} onClick={() => setHistoryPage((page) => page - 1)}>
                Précédent
              </button>
              <span>
                Page {history.record?.page || historyPage} sur {Math.ceil((history.record?.total || 0) / 20)}
              </span>
              <button
                disabled={historyPage * 20 >= (history.record?.total || 0)}
                onClick={() => setHistoryPage((page) => page + 1)}
              >
                Suivant
              </button>
            </div>
          )}
        </Panel>
      )}
      {tab === 'opportunities' && (
        <Panel
          title="Opportunités liées"
          action={
            <button
              disabled={!contacts.length}
              onClick={() => onOpen('opportunity', {contact: {...contacts[0], account: row}})}
            >
              <Plus size={15} />
              Nouvelle opportunité
            </button>
          }
        >
          {opportunities.length ? (
            <ul className="ws-deals">
              {opportunities.map((item) => (
                <li key={item.id}>
                  <button onClick={() => onOpen('editOpportunity', item)}>
                    <strong>{item.title}</strong>
                    <small>{item.next_action || 'Prochaine action à préciser'}</small>
                  </button>
                  <TableBadge value={item.stage}>{statusNames[item.stage]}</TableBadge>
                  <strong>{money(item.amount_cents)}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <Blank title="Aucune opportunité">
              {contacts.length
                ? 'Créez une opportunité à partir d’une conversation avec un interlocuteur.'
                : 'Ajoutez un interlocuteur pour suivre une opportunité.'}
            </Blank>
          )}
        </Panel>
      )}
    </article>
  );
}
