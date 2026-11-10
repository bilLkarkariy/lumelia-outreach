import {ArrowUpRight, Mail, Target} from 'lucide-react';
import {
  ListSummary,
  ListTable,
  ListToolbar,
  TableBadge,
  localList,
  useListState,
  type Column,
  type Filter,
  type Row,
} from './ListTable';

const money = (cents: number) =>
  new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR', maximumFractionDigits: 0}).format(
    (cents || 0) / 100,
  );
const shortDate = (value?: string) =>
  value ? new Date(value).toLocaleDateString('fr-FR', {day: 'numeric', month: 'short'}) : 'Jamais';
export function CampaignDirectory({
  groups,
  campaigns,
  offers,
  onSelect,
}: {
  groups: Row[];
  campaigns: Row[];
  offers: Row[];
  onSelect: (id: string) => void;
}) {
  const [state, change] = useListState('campaigns', {hidden: ['meetings', 'updated']});
  const rows = groups.map((group) => {
    const members = campaigns.filter((row) => row.group_id === group.id);
    return {
      ...group,
      offer: offers.find((offer) => offer.id === group.offer_id),
      members,
      niches: new Set(members.map((row) => row.niche?.trim().toLocaleLowerCase('fr'))).size,
      families: new Set(members.map((row) => row.family_id || row.id)).size,
      status: members.some((row) => row.status === 'active')
        ? 'active'
        : members.length && members.every((row) => row.status === 'paused')
          ? 'paused'
          : 'draft',
    };
  });
  const columns: Column[] = [
    {
      key: 'name',
      label: 'Campagne principale',
      fixed: true,
      sort: 'name',
      width: 285,
      render: (row) => (
        <div className="lt-record">
          <span className="lt-avatar" aria-hidden="true">
            <Target size={17} />
          </span>
          <div>
            <button className="lt-company-name" onClick={() => onSelect(row.id)}>
              {row.name}
            </button>
            <small>
              {row.families} sous-campagne(s) · {row.members.length} version(s)
            </small>
          </div>
        </div>
      ),
    },
    {
      key: 'offer',
      label: 'Offre',
      sort: 'offer',
      width: 185,
      render: (row) => <strong>{row.offer?.name || 'Offre à préciser'}</strong>,
    },
    {key: 'niches', label: 'Niches', sort: 'niches', width: 95, render: (row) => <span>{row.niches}</span>},
    {
      key: 'contacts',
      label: 'Contacts',
      sort: 'contacts',
      width: 120,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.metrics.contacts}</strong>
          <small>{row.metrics.companies} entreprises</small>
        </div>
      ),
    },
    {
      key: 'sent',
      label: 'Emails envoyés',
      sort: 'sent',
      width: 125,
      render: (row) => <span>{row.metrics.sent}</span>,
    },
    {
      key: 'replies',
      label: 'Réponses',
      sort: 'replies',
      width: 100,
      render: (row) => <span>{row.metrics.replies}</span>,
    },
    {
      key: 'status',
      label: 'État',
      sort: 'status',
      width: 145,
      render: (row) => (
        <TableBadge value={row.status}>
          {row.status === 'active'
            ? 'Version active'
            : row.status === 'paused'
              ? 'En pause'
              : 'En préparation'}
        </TableBadge>
      ),
    },
    {
      key: 'meetings',
      label: 'RDV tenus',
      sort: 'meetings',
      width: 120,
      optional: true,
      render: (row) => <span>{row.metrics.meetings_held}</span>,
    },
    {
      key: 'updated',
      label: 'Modifiée le',
      sort: 'updated',
      width: 130,
      optional: true,
      render: (row) => <span className="lt-date">{shortDate(row.updated_at)}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => (
        <button aria-label={'Ouvrir la campagne ' + row.name} onClick={() => onSelect(row.id)}>
          <ArrowUpRight size={16} />
        </button>
      ),
    },
  ];
  const filters: Filter[] = [
    {key: 'offer_id', label: 'Offre', options: Object.fromEntries(offers.map((row) => [row.id, row.name]))},
    {
      key: 'status',
      label: 'État',
      options: {
        active: 'Avec une version active',
        draft: 'En préparation',
        paused: 'Toutes les versions en pause',
      },
    },
  ];
  const filtered = localList(
    rows,
    state,
    (row) =>
      [row.name, row.offer?.name, ...row.members.map((member: Row) => member.name + ' ' + member.niche)].join(
        ' ',
      ),
    (row) =>
      (!state.filters.offer_id || row.offer_id === state.filters.offer_id) &&
      (!state.filters.status || row.status === state.filters.status),
    {
      name: (row) => row.name,
      offer: (row) => row.offer?.name,
      niches: (row) => row.niches,
      contacts: (row) => row.metrics.contacts,
      sent: (row) => row.metrics.sent,
      replies: (row) => row.metrics.replies,
      meetings: (row) => row.metrics.meetings_held,
      status: (row) => row.status,
      updated: (row) => row.updated_at,
    },
  );
  return (
    <div className="lt-page">
      <ListSummary
        items={[
          [rows.length, 'campagnes principales'],
          [rows.reduce((total, row) => total + row.families, 0), 'sous-campagnes'],
          [campaigns.length, 'versions au total'],
          [campaigns.filter((row) => row.status === 'active').length, 'versions actives'],
        ]}
      />
      <section className="lt-panel">
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          searchLabel="Rechercher une campagne"
          placeholder="Campagne, offre ou niche…"
        />
        <ListTable
          title="Campagnes principales"
          rows={filtered.items}
          total={filtered.total}
          overall={rows.length}
          state={state}
          change={change}
          columns={columns}
          emptyTitle="Aucune campagne dans cette vue"
          emptyText="Créez une campagne principale, puis ajoutez ses niches et ses sous-campagnes."
        />
      </section>
    </div>
  );
}
export function OfferDirectory({
  offers,
  campaigns,
  onSelect,
}: {
  offers: Row[];
  campaigns: Row[];
  onSelect: (id: string) => void;
}) {
  const [state, change] = useListState('offers', {hidden: ['buyer']});
  const statusNames: Row = {
    primary: 'Prospection principale',
    experimental: 'À tester',
    network: 'Réseau',
    historical: 'Archivée',
  };
  const rows = offers.map((offer) => {
    const initialPrice = offer.pricing.find((tier: Row) => tier.period !== 'month') || offer.pricing[0];
    return {
      ...offer,
      campaigns: campaigns.filter((row) => row.offer_id === offer.id).length,
      initialPrice,
      minimum: initialPrice?.min_cents ?? null,
    };
  });
  const columns: Column[] = [
    {
      key: 'name',
      label: 'Offre',
      fixed: true,
      sort: 'name',
      width: 245,
      render: (row) => (
        <div className="lt-cell-stack">
          <button className="lt-company-name" onClick={() => onSelect(row.id)}>
            {row.name}
          </button>
          <small>{row.brief.duration || 'Délai à préciser'}</small>
        </div>
      ),
    },
    {
      key: 'audience',
      label: 'Pour qui',
      width: 285,
      render: (row) => <span>{row.brief.audience || row.summary}</span>,
    },
    {
      key: 'status',
      label: 'Usage',
      sort: 'status',
      width: 175,
      render: (row) => (
        <TableBadge
          value={row.status === 'primary' ? 'active' : row.status === 'historical' ? 'cancelled' : 'draft'}
        >
          {statusNames[row.status] || row.status}
        </TableBadge>
      ),
    },
    {
      key: 'price',
      label: 'Prestation initiale · HT',
      sort: 'price',
      width: 160,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>
            {row.minimum == null ? (
              'Sur devis'
            ) : (
              <>
                {money(row.minimum)}
                {row.initialPrice.max_cents !== row.minimum ? ' à ' + money(row.initialPrice.max_cents) : ''}
                {row.initialPrice.period === 'month' ? ' / mois' : ''}
              </>
            )}
          </strong>
          <small>{row.initialPrice?.name || 'Périmètre à définir'}</small>
        </div>
      ),
    },
    {
      key: 'campaigns',
      label: 'Versions liées',
      sort: 'campaigns',
      width: 130,
      render: (row) => <span>{row.campaigns}</span>,
    },
    {
      key: 'buyer',
      label: 'Interlocuteur cible',
      width: 260,
      optional: true,
      render: (row) => <span>{row.brief.buyer || 'À définir'}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => (
        <button aria-label={'Voir l’offre ' + row.name} onClick={() => onSelect(row.id)}>
          <ArrowUpRight size={16} />
        </button>
      ),
    },
  ];
  const filtered = localList(
    rows,
    state,
    (row) => [row.name, row.summary, row.brief.audience, row.brief.buyer].join(' '),
    (row) => !state.filters.status || row.status === state.filters.status,
    {
      name: (row) => row.name,
      status: (row) => row.status,
      price: (row) => row.minimum,
      campaigns: (row) => row.campaigns,
    },
  );
  return (
    <div className="lt-page">
      <section className="lt-panel">
        <ListToolbar
          state={state}
          change={change}
          filters={[{key: 'status', label: 'Usage', options: statusNames}]}
          columns={columns}
          searchLabel="Rechercher une offre"
          placeholder="Offre, cible ou besoin…"
        />
        <ListTable
          title="Offres"
          rows={filtered.items}
          total={filtered.total}
          overall={rows.length}
          state={state}
          change={change}
          columns={columns}
          emptyTitle="Aucune offre ne correspond"
        />
      </section>
    </div>
  );
}
export function SenderDirectory({
  rows,
  loading,
  onSelect,
}: {
  rows: Row[];
  loading: boolean;
  onSelect: (id: string) => void;
}) {
  const [state, change] = useListState('mailboxes', {hidden: ['provider', 'checked']});
  const operational = (row: Row) =>
    row.global_sending_paused ? 'paused' : row.paused ? 'paused' : row.operational_status || 'blocked';
  const ops: Row = {
    paused: 'En pause',
    waiting_first_send: 'En attente de démarrage',
    blocked: 'Condition à résoudre',
    sending: 'Activité en cours',
  };
  const columns: Column[] = [
    {
      key: 'name',
      label: 'Boîte expéditrice',
      sort: 'name',
      fixed: true,
      width: 255,
      render: (row) => (
        <div className="lt-record">
          <span className="lt-avatar" aria-hidden="true">
            <Mail size={16} />
          </span>
          <div>
            <button className="lt-company-name" onClick={() => onSelect(row.id)}>
              {row.from_email}
            </button>
            <small>{row.from_name || row.domain}</small>
          </div>
        </div>
      ),
    },
    {
      key: 'connection',
      label: 'Connexion',
      sort: 'connection',
      width: 135,
      render: (row) => (
        <TableBadge value={row.status === 'authenticated' ? 'valid' : 'unknown'}>
          {row.status === 'authenticated' ? 'Connectée' : 'À vérifier'}
        </TableBadge>
      ),
    },
    {
      key: 'status',
      label: 'État des envois',
      sort: 'status',
      width: 180,
      render: (row) => (
        <TableBadge value={operational(row) === 'sending' ? 'active' : 'paused'}>
          {ops[operational(row)] || 'À examiner'}
        </TableBadge>
      ),
    },
    {
      key: 'limit',
      label: 'Plafond / 24 h',
      sort: 'limit',
      width: 125,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.ramp?.effective_limit ?? row.daily_limit}</strong>
          <small>Cible : {row.daily_limit}</small>
        </div>
      ),
    },
    {
      key: 'volume',
      label: 'Volume / 24 h',
      sort: 'volume',
      width: 130,
      render: (row) => (
        <div className="lt-cell-stack">
          <strong>{row.used_24h} tentative(s)</strong>
          <small>{row.available_24h} disponible(s)</small>
        </div>
      ),
    },
    {
      key: 'checks',
      label: 'Réception externe',
      width: 170,
      render: (row) => (
        <div className="lt-cell-stack">
          {['gmail', 'outlook'].map((provider) => {
            const check = row.ramp?.delivery_checks?.[provider];
            const fresh =
              check?.observed_at && Date.now() - new Date(check.observed_at).getTime() <= 7 * 86400000;
            return (
              <div
                className="lt-cell-line"
                key={provider}
                title={check ? 'Observation du ' + shortDate(check.observed_at) : 'Aucun résultat enregistré'}
              >
                <span>{provider === 'gmail' ? 'Gmail' : 'Outlook'}</span>
                <TableBadge
                  value={
                    check?.outcome === 'spam' || check?.outcome === 'missing'
                      ? 'invalid'
                      : fresh && check?.outcome === 'inbox'
                        ? 'valid'
                        : 'unknown'
                  }
                >
                  {check && !fresh
                    ? 'À renouveler'
                    : check?.outcome === 'inbox'
                      ? 'Reçu'
                      : check?.outcome === 'spam'
                        ? 'Indésirables'
                        : check?.outcome === 'missing'
                          ? 'Non reçu'
                          : 'À tester'}
                </TableBadge>
              </div>
            );
          })}
        </div>
      ),
    },
    {
      key: 'provider',
      label: 'Fournisseur',
      width: 185,
      optional: true,
      render: (row) => <span>{row.provider_group}</span>,
    },
    {
      key: 'checked',
      label: 'Dernier contrôle',
      sort: 'checked',
      width: 145,
      optional: true,
      render: (row) => <span className="lt-date">{shortDate(row.last_checked_at)}</span>,
    },
    {
      key: 'actions',
      label: 'Actions',
      fixed: true,
      render: (row) => (
        <button aria-label={'Gérer la boîte ' + row.from_email} onClick={() => onSelect(row.id)}>
          <ArrowUpRight size={16} />
        </button>
      ),
    },
  ];
  const filtered = localList(
    rows,
    state,
    (row) => [row.from_email, row.from_name, row.domain, row.provider_group].join(' '),
    (row) =>
      (!state.filters.connection ||
        (row.status === 'authenticated' ? 'connected' : 'check') === state.filters.connection) &&
      (!state.filters.status || operational(row) === state.filters.status) &&
      (!state.filters.provider || row.provider_group === state.filters.provider),
    {
      name: (row) => row.from_email,
      connection: (row) => row.status,
      status: operational,
      limit: (row) => row.ramp?.effective_limit ?? row.daily_limit,
      volume: (row) => row.used_24h,
      checked: (row) => row.last_checked_at,
    },
  );
  const filters: Filter[] = [
    {key: 'connection', label: 'Connexion', options: {connected: 'Connectée', check: 'À vérifier'}},
    {key: 'status', label: 'Envois', options: ops},
    {
      key: 'provider',
      label: 'Fournisseur',
      options: Object.fromEntries(rows.map((row) => [row.provider_group, row.provider_group])),
      extra: true,
    },
  ];
  return (
    <div className="lt-page">
      <ListSummary
        items={[
          [loading && !rows.length ? '—' : rows.length, 'boîtes dans l’espace'],
          [rows.filter((row) => row.status === 'authenticated').length, 'boîtes connectées'],
          [rows.reduce((total, row) => total + row.used_24h, 0), 'tentatives sur 24 h'],
          [rows.reduce((total, row) => total + row.available_24h, 0), 'envois possibles maintenant'],
        ]}
      />
      <section className="lt-panel">
        <ListToolbar
          state={state}
          change={change}
          filters={filters}
          columns={columns}
          searchLabel="Rechercher une boîte"
          placeholder="Adresse, domaine ou fournisseur…"
        />
        <ListTable
          title="Boîtes expéditrices"
          rows={filtered.items}
          total={filtered.total}
          overall={rows.length}
          state={state}
          change={change}
          columns={columns}
          loading={loading}
          emptyTitle="Aucune boîte dans cette vue"
          emptyText="Ajoutez une boîte ou ajustez les filtres pour retrouver une connexion."
        />
      </section>
    </div>
  );
}
