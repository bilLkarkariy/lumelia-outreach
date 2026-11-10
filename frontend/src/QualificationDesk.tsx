import {useEffect, useRef, useState, type FormEvent} from 'react';
import {ArrowUpRight, Check, ChevronRight, SkipForward, X} from 'lucide-react';
import {markets} from './Locales';
import {
  WorkspaceHeader,
  Panel,
  Field,
  Feedback,
  Blank,
  Sources,
  RouteLink,
  useRecord,
  personName,
  type Api,
  type Row,
  type Navigate,
} from './WorkspaceUI';
import {errorText} from './api';

type Props = {
  url: string;
  data: Row;
  api: Api;
  navigate: Navigate;
  admin: boolean;
  onRefresh: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  discardVersion: number;
};
export function QualificationDesk(props: Props) {
  const {url, data, api, navigate} = props;
  const params = new URL(url, location.origin).searchParams;
  const resource = params.get('resource') === 'contacts' ? 'contacts' : 'accounts';
  const campaign = params.get('campaign') || '',
    country = params.get('country') || '',
    query = params.get('q') || '';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const [search, setSearch] = useState(query),
    [skipped, setSkipped] = useState<string[]>([]),
    [reviewed, setReviewed] = useState(0),
    [notice, setNotice] = useState('');
  const queue = useRecord(
    '/qualification/queue?' +
      new URLSearchParams({resource, campaign_id: campaign, country, q: query, page: String(page)}),
    api,
    data,
  );
  useEffect(() => {
    setSkipped([]);
    setSearch(query);
  }, [resource, campaign, country, query]);
  useEffect(() => {
    document.title = 'File de qualification · Lumelia';
  }, []);
  function change(patch: Row) {
    if (['resource', 'campaign', 'country', 'q'].some((key) => key in patch)) patch.page = 1;
    const next = new URLSearchParams({
      resource,
      page: String(page),
      ...(campaign ? {campaign} : {}),
      ...(country ? {country} : {}),
      ...(query ? {q: query} : {}),
      ...patch,
    });
    for (const [key, value] of [...next]) if (!value) next.delete(key);
    navigate('/qualification?' + next);
  }
  const rows: Row[] = queue.record?.items || [],
    available = rows.filter((row) => !skipped.includes(row.id));
  const selected = params.get('record') || available[0]?.id || '';
  return (
    <article className="ws-page qualification-workspace">
      <WorkspaceHeader
        back="/companies"
        backLabel="Entreprises & contacts"
        eyebrow="QUALIFICATION"
        title="Une fiche, une décision"
        subtitle="Examinez les sources, documentez votre choix, puis passez à la suivante."
        navigate={navigate}
      />
      <div className="ws-queue-toolbar">
        <div className="entity-tabs" role="group" aria-label="Type de fiche à qualifier">
          <button aria-pressed={resource === 'accounts'} onClick={() => change({resource: 'accounts'})}>
            Entreprises
          </button>
          <button aria-pressed={resource === 'contacts'} onClick={() => change({resource: 'contacts'})}>
            Contacts
          </button>
        </div>
        <span>{reviewed} fiche(s) traitée(s) pendant cette session</span>
      </div>
      <form
        className="ws-filter-bar"
        onSubmit={(event) => {
          event.preventDefault();
          change({q: search});
        }}
      >
        <Field label="Sous-campagne examinée">
          <select value={campaign} onChange={(event) => change({campaign: event.target.value})}>
            <option value="">
              {resource === 'accounts' ? 'Revue générale' : 'Toutes les sous-campagnes'}
            </option>
            {data.campaigns.map((row: Row) => (
              <option key={row.id} value={row.id}>
                {row.name} · v{row.config.sequence_version || 1}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Pays">
          <select value={country} onChange={(event) => change({country: event.target.value})}>
            <option value="">Tous les pays</option>
            {Object.entries(markets).map(([key, label]) => (
              <option key={key} value={key}>
                {String(label)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Rechercher dans la file">
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Entreprise, nom…"
            maxLength={120}
          />
        </Field>
        <button type="submit">Rechercher</button>
      </form>
      <Feedback error={queue.error} notice={notice} />
      <div className="ws-queue-layout">
        <section className="ws-queue-list" aria-label="Fiches dans la file">
          <header>
            <strong>{queue.record?.total ?? '…'} à examiner</strong>
            <small>Les plus anciennes d’abord</small>
          </header>
          {rows.map((row) => (
            <RouteLink
              key={row.id}
              url={
                '/qualification?' +
                new URLSearchParams({
                  resource,
                  campaign,
                  country,
                  q: query,
                  record: row.id,
                  page: String(page),
                })
              }
              navigate={navigate}
              className={'ws-queue-item ' + (selected === row.id ? 'active' : '')}
            >
              <span>
                <strong>{resource === 'accounts' ? row.name : personName(row)}</strong>
                <small>{resource === 'accounts' ? row.industry || row.domain : row.account.name}</small>
                {skipped.includes(row.id) && <small>Reportée à plus tard</small>}
              </span>
              <ChevronRight size={14} />
            </RouteLink>
          ))}
          {!rows.length && <p>{queue.loading ? 'Chargement…' : 'Aucune fiche dans cette sélection.'}</p>}
          {(queue.record?.total || 0) > 10 && (
            <div className="ws-pagination">
              <button disabled={page <= 1} onClick={() => change({record: '', page: page - 1})}>
                Précédent
              </button>
              <span>
                {queue.record?.page || page}/{Math.ceil((queue.record?.total || 0) / 10)}
              </span>
              <button
                disabled={page * 10 >= queue.record!.total}
                onClick={() => change({record: '', page: page + 1})}
              >
                Suivant
              </button>
            </div>
          )}
        </section>
        <div className="ws-queue-detail">
          {selected ? (
            <Review
              key={selected + ':' + resource + ':' + campaign + ':' + props.discardVersion}
              {...props}
              id={selected}
              resource={resource}
              campaign={campaign}
              onSkip={() => {
                setSkipped((ids) => [...ids, selected]);
                change({record: ''});
                setNotice('Fiche reportée à plus tard pour cette session.');
              }}
              onDone={async () => {
                setReviewed((value) => value + 1);
                setSkipped((ids) => [...ids, selected]);
                change({record: ''});
                setNotice('Décision enregistrée. La fiche suivante est prête à examiner.');
                queue.refresh();
                await props.onRefresh();
              }}
            />
          ) : (
            <Panel title={rows.length ? 'Sélection examinée' : 'La file est à jour'}>
              <Blank
                title={rows.length ? 'Les fiches de cette page sont reportées' : 'Aucune fiche à qualifier'}
              >
                {rows.length
                  ? 'Passez à la page suivante ou reprenez les fiches reportées.'
                  : 'Changez les filtres ou ajoutez de nouvelles entreprises et de nouveaux contacts.'}
              </Blank>
              {skipped.length > 0 && (
                <button onClick={() => setSkipped([])}>Reprendre les fiches reportées</button>
              )}
            </Panel>
          )}
        </div>
      </div>
    </article>
  );
}
function Review({
  id,
  resource,
  campaign,
  api,
  data,
  navigate,
  admin,
  onDirtyChange,
  onSkip,
  onDone,
}: Props & {
  id: string;
  resource: string;
  campaign: string;
  onSkip: () => void;
  onDone: () => Promise<void>;
}) {
  const detail = useRecord(resource === 'accounts' ? '/workspaces/accounts/' + id : '/contacts/' + id, api),
    row = detail.record;
  const [reason, setReason] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const dirty = useRef(onDirtyChange);
  dirty.current = onDirtyChange;
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    dirty.current(!!reason.trim());
    return () => dirty.current(false);
  }, [reason]);
  if (!row)
    return (
      <Panel title={detail.loading ? 'Chargement de la fiche…' : 'Fiche indisponible'}>
        <Feedback error={detail.error} />
        {detail.error && <button onClick={detail.refresh}>Réessayer</button>}
      </Panel>
    );
  const company = resource === 'accounts' ? row : row.account;
  const target = data.campaigns.find((item: Row) => item.id === (campaign || row.campaign_id));
  const profile = data.icps.find((item: Row) => item.id === target?.icp_id);
  async function decide(event: FormEvent, decision: string) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('/qualification/' + resource + '/' + id, 'POST', {
        decision,
        reason,
        expected_updated_at: row!.updated_at,
        campaign_id: campaign || null,
        request_id: requestId.current,
      });
      setReason('');
      dirty.current(false);
      await onDone();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Panel
        title={resource === 'accounts' ? row.name : personName(row)}
        description={resource === 'accounts' ? row.domain : row.role + ' · ' + company.name}
        action={
          <RouteLink
            url={resource === 'accounts' ? '/companies/' + id : '/contacts/' + id + '?tab=qualification'}
            navigate={navigate}
            className="ws-button-link"
          >
            Ouvrir la fiche
            <ArrowUpRight size={14} />
          </RouteLink>
        }
      >
        {target && (
          <div className="ws-review-target">
            <strong>Cible · {target.niche || target.name}</strong>
            <p>
              {profile?.criteria?.niche_definition ||
                profile?.notes ||
                'Consultez le ciblage de cette sous-campagne.'}
            </p>
            {profile?.exclusions?.length > 0 && <small>Exclusions : {profile.exclusions.join(' · ')}</small>}
          </div>
        )}
        <dl className="ws-facts">
          <div>
            <dt>Activité</dt>
            <dd>{company.industry || 'À préciser'}</dd>
          </div>
          <div>
            <dt>Pays</dt>
            <dd>{markets[company.country] || company.country}</dd>
          </div>
          <div>
            <dt>Effectif</dt>
            <dd>{company.employee_count ?? 'À vérifier'}</dd>
          </div>
          <div>
            <dt>HubSpot</dt>
            <dd>
              {{unknown: 'À vérifier', signal: 'Signal public', confirmed: 'Confirmé', absent: 'Absent'}[
                company.hubspot_status as string
              ] || company.hubspot_status}
            </dd>
          </div>
        </dl>
        {resource === 'contacts' && (
          <>
            <div className="ws-prose">
              <h3>Pertinence professionnelle</h3>
              <p>{row.professional_relevance || 'À documenter avant validation.'}</p>
            </div>
            <dl className="ws-facts">
              <div>
                <dt>Email</dt>
                <dd>{row.email || 'Non renseigné'}</dd>
              </div>
              <div>
                <dt>Téléphone</dt>
                <dd>{row.phone || 'Non renseigné'}</dd>
              </div>
            </dl>
            {Object.entries(row.eligibility || {}).map(([channel, reasons]) => (
              <details className="ws-eligibility" key={channel}>
                <summary>
                  {{email: 'Email', phone: 'Appel', linkedin: 'LinkedIn'}[channel]} ·{' '}
                  {(reasons as string[]).length
                    ? (reasons as string[]).length + ' point(s) à vérifier'
                    : 'Prêt'}
                </summary>
                <ul>
                  {(reasons as string[]).map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </details>
            ))}
          </>
        )}
        <h3>Sources disponibles · {row.evidence.length}</h3>
        <Sources rows={row.evidence} />
      </Panel>
      <Panel
        title="Votre décision"
        description={
          resource === 'accounts'
            ? 'La décision concerne la sélection examinée. Chaque nouveau ciblage garde sa propre revue.'
            : 'La validation vérifie la cible, les coordonnées et les conditions de contact.'
        }
      >
        <Feedback error={error} />
        {admin ? (
          <form onSubmit={(event) => decide(event, 'qualified')}>
            <Field
              label="Pourquoi retenir ou écarter cette fiche ?"
              hint="10 caractères minimum. La raison reste dans l’historique."
            >
              <textarea
                value={reason}
                onChange={(event) => {
                  setReason(event.target.value);
                  requestId.current = crypto.randomUUID();
                }}
                rows={3}
                minLength={10}
                maxLength={3000}
                required
                disabled={busy}
                placeholder="Faits observés, adéquation à la cible, informations à confirmer…"
              />
            </Field>
            <div className="ws-decision-actions">
              <button className="primary" disabled={busy || reason.trim().length < 10}>
                <Check size={16} />
                {resource === 'accounts' ? 'Retenir et continuer' : 'Valider et continuer'}
              </button>
              <button
                type="button"
                disabled={busy || reason.trim().length < 10}
                onClick={(event) => decide(event, 'rejected')}
              >
                <X size={16} />
                Écarter
              </button>
              <button type="button" disabled={busy || !!reason.trim()} onClick={onSkip}>
                <SkipForward size={16} />
                Passer plus tard
              </button>
            </div>
          </form>
        ) : (
          <p>Un administrateur peut enregistrer la décision de qualification.</p>
        )}
      </Panel>
    </>
  );
}
