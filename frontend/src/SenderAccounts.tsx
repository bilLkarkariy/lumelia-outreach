import {useEffect, useState} from 'react';
import {ArrowLeft, Plus, X} from 'lucide-react';
import {Warmup} from './Warmup';
import {NativeWarmup} from './NativeWarmup';
import {SenderDirectory} from './ListDirectories';
import {errorText, type Api, type Row} from './api';

export function SenderAccounts({api, admin}: {api: Api; admin: boolean}) {
  const [rows, setRows] = useState<Row[]>([]),
    [limits, setLimits] = useState<Row[]>([]);
  const [inspected, setInspected] = useState(''),
    [loading, setLoading] = useState(true);
  const [editor, setEditor] = useState<Row | null>(null),
    [policy, setPolicy] = useState<Row | null>(null);
  const [existing, setExisting] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  async function load() {
    const [mailboxes, groups] = await Promise.all([api('/senders'), api('/email-limits')]);
    setRows(mailboxes);
    setLimits(groups);
    setLoading(false);
  }
  useEffect(() => {
    load().catch((e) => {
      setError(errorText(e));
      setLoading(false);
    });
    const timer = setInterval(() => {
      if (!document.hidden) load().catch((e) => setError(errorText(e)));
    }, 15000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- polling starts once on mount; load is recreated on every render
  }, []);
  async function run(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
      await load();
      setNotice(message);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  function field(label: string, name: string, value: string | number = '', type = 'text', required = true) {
    return (
      <label className="field">
        <span>{label}</span>
        <input
          name={name}
          type={type}
          defaultValue={value ?? ''}
          required={required}
          autoComplete={type === 'password' ? 'new-password' : 'off'}
        />
      </label>
    );
  }
  async function saveConnection(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.currentTarget));
    await run(async () => {
      const config = {
        host: f.host,
        port: Number(f.port),
        security: f.security,
        username: f.username,
        password: f.password,
        from_email: f.from_email,
        from_name: f.from_name,
        reply_to: f.reply_to || null,
        legal_footer: f.legal_footer,
        privacy_url: f.privacy_url,
        dns_verified: f.dns_verified === 'on',
        mailboxes: [
          {
            host: f.imap_host,
            port: Number(f.imap_port),
            username: f.imap_username,
            password: f.imap_password,
            address: f.from_email,
            folder: 'INBOX',
          },
          ...(f.reply_to && f.reply_to !== f.from_email
            ? [
                {
                  host: f.reply_imap_host,
                  port: 993,
                  username: f.reply_imap_username,
                  password: f.reply_imap_password,
                  address: f.reply_to,
                  folder: 'INBOX',
                },
              ]
            : []),
        ],
      };
      await api(
        editor?.id ? '/senders/' + editor.id + '/connection' : '/senders',
        editor?.id ? 'PATCH' : 'POST',
        {config, provider_group: f.provider_group},
      );
      setEditor(null);
    }, 'Connexion enregistrée en pause. Testez les accès, puis renseignez la préparation et les plafonds.');
  }
  const cfg = editor?.config || {},
    imap = cfg.mailboxes?.[0] || {},
    reply = cfg.mailboxes?.[1] || {};
  return (
    <>
      <NativeWarmup api={api} admin={admin} refresh={load} />
      <section className="panel sender-panel sender-workspace">
        <header>
          <div>
            <h2>Boîtes d’envoi</h2>
            <p>Expéditeur conservé pour toutes les relances d’un prospect</p>
          </div>
          {admin && !editor && (
            <button
              onClick={() => {
                setEditor({});
                setPolicy(null);
                setError('');
              }}
            >
              <Plus size={16} /> Ajouter une boîte
            </button>
          )}
        </header>
        <div className="padded">
          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              {notice}
            </div>
          )}
          {editor && !editor.id && (
            <div className="actions">
              <button type="button" onClick={() => setExisting(false)} aria-pressed={!existing}>
                Saisir les accès
              </button>
              <button
                type="button"
                onClick={() => setExisting(true)}
                aria-pressed={existing}
                disabled={!rows.some((row) => row.status === 'authenticated')}
              >
                Même serveur et mêmes mots de passe
              </button>
            </div>
          )}
          {editor && !editor.id && existing ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const f = Object.fromEntries(new FormData(event.currentTarget));
                run(async () => {
                  await api('/senders/' + f.source + '/existing-mailbox', 'POST', {
                    address: f.address,
                    from_name: f.from_name,
                    confirm_owned_shared_access: f.confirm === 'on',
                    dns_verified: f.dns_verified === 'on',
                  });
                  setEditor(null);
                  setExisting(false);
                }, 'Boîte existante connectée : SMTP et IMAP vérifiés. Campagnes en pause. Activez sa préparation après la première collecte IMAP.');
              }}
            >
              <div className="inline-header">
                <h3>Connecter une boîte existante</h3>
                <button
                  type="button"
                  className="icon"
                  aria-label="Fermer la connexion"
                  onClick={() => setEditor(null)}
                >
                  <X size={18} />
                </button>
              </div>
              <p>
                Aucun achat ni création chez le fournisseur. Lumelia teste les accès de cette adresse sur les
                mêmes serveurs, avec les mots de passe déjà chiffrés. Aucun secret n’est réaffiché.
              </p>
              <label className="field">
                <span>Boîte de référence — mêmes accès</span>
                <select name="source" required>
                  {rows
                    .filter((row) => row.status === 'authenticated')
                    .map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.from_email} · {row.provider_group}
                      </option>
                    ))}
                </select>
              </label>
              {field('Adresse de la boîte existante', 'address', '', 'email')}
              {field('Nom de l’expéditeur', 'from_name', 'Lumelia')}
              <label className="checkbox">
                <input name="confirm" type="checkbox" required />
                Je possède cette boîte existante et elle utilise les mêmes mots de passe SMTP et IMAP que la
                référence.
              </label>
              <label className="checkbox">
                <input name="dns_verified" type="checkbox" />
                SPF, DKIM et DMARC de ce domaine vérifiés
              </label>
              <p>
                Les campagnes restent en pause. Les plafonds individuels sont repris, sans augmenter les
                plafonds partagés. La progression et les contrôles Gmail/Outlook commencent à zéro.
              </p>
              <button className="primary" disabled={busy}>
                {busy ? 'Vérification SMTP et IMAP…' : 'Vérifier et connecter cette boîte'}
              </button>
            </form>
          ) : editor ? (
            <form key={editor.id || 'new'} onSubmit={saveConnection}>
              <div className="inline-header">
                <h3>{editor.id ? 'Modifier la connexion' : 'Nouvelle boîte'}</h3>
                <button
                  type="button"
                  className="icon"
                  aria-label="Fermer la connexion"
                  onClick={() => setEditor(null)}
                >
                  <X size={18} />
                </button>
              </div>
              <div className="form-grid">
                {field('Email de l’expéditeur', 'from_email', cfg.from_email, 'email')}
                {field('Nom de l’expéditeur', 'from_name', cfg.from_name || 'Lumelia')}
                {field('Serveur SMTP', 'host', cfg.host)}
                {field('Port SMTP', 'port', cfg.port || 465, 'number')}
                <label className="field">
                  <span>Chiffrement SMTP</span>
                  <select name="security" defaultValue={cfg.security || 'ssl'}>
                    <option value="ssl">SSL/TLS</option>
                    <option value="starttls">STARTTLS</option>
                  </select>
                </label>
                {field(
                  'Groupe fournisseur partagé',
                  'provider_group',
                  editor.provider_group || '',
                  'text',
                  false,
                )}
                {field('Identifiant SMTP', 'username', cfg.username)}
                {field('Mot de passe SMTP', 'password', '', 'password')}
              </div>
              <p>
                Utilisez le même groupe pour toutes les boîtes dépendant du même quota fournisseur. À défaut,
                le serveur SMTP sert de groupe. Un alias utilisant le même compte SMTP est refusé.
              </p>
              <h3>Réception des réponses</h3>
              <div className="form-grid">
                {field('Serveur IMAP', 'imap_host', imap.host)}
                {field('Port IMAP SSL', 'imap_port', imap.port || 993, 'number')}
                {field('Identifiant IMAP', 'imap_username', imap.username)}
                {field('Mot de passe IMAP', 'imap_password', '', 'password')}
              </div>
              {field('Adresse de réponse distincte (facultatif)', 'reply_to', cfg.reply_to, 'email', false)}
              <details>
                <summary>Réception de l’adresse de réponse distincte</summary>
                {field('Serveur IMAP de l’adresse de réponse', 'reply_imap_host', reply.host, 'text', false)}
                {field(
                  'Identifiant IMAP de l’adresse de réponse',
                  'reply_imap_username',
                  reply.username,
                  'text',
                  false,
                )}
                {field(
                  'Mot de passe IMAP de l’adresse de réponse',
                  'reply_imap_password',
                  '',
                  'password',
                  false,
                )}
              </details>
              <label className="field">
                <span>Pied d’email : identité, motif du contact et opposition</span>
                <textarea name="legal_footer" defaultValue={cfg.legal_footer || ''} rows={4} />
              </label>
              {field(
                'Page d’information sur les données (HTTPS)',
                'privacy_url',
                cfg.privacy_url,
                'url',
                false,
              )}
              <label className="checkbox">
                <input name="dns_verified" type="checkbox" defaultChecked={cfg.dns_verified || false} />
                SPF, DKIM et DMARC vérifiés
              </label>
              <p>
                Les mots de passe sont chiffrés et ne sont jamais réaffichés. Modifier la connexion remet la
                boîte en pause et les messages à relire.
              </p>
              <button className="primary" disabled={busy}>
                Enregistrer la boîte
              </button>
            </form>
          ) : (
            <>
              {!inspected && (
                <SenderDirectory
                  rows={rows}
                  loading={loading}
                  onSelect={(id) => {
                    setInspected(id);
                    window.scrollTo({top: 0, behavior: 'instant'});
                  }}
                />
              )}
              {inspected && (
                <button
                  className="lt-detail-back"
                  onClick={() => {
                    setInspected('');
                    setPolicy(null);
                  }}
                >
                  <ArrowLeft size={16} />
                  Toutes les boîtes
                </button>
              )}
              {rows
                .filter((row) => row.id === inspected)
                .map((row) => (
                  <article className="sender-card" key={row.id}>
                    <div className="sender-heading">
                      <div>
                        <h3>{row.from_email}</h3>
                        <small>
                          {row.domain} · {row.provider_group}
                        </small>
                      </div>
                      <span
                        className={'badge ' + (row.global_sending_paused || row.paused ? 'paused' : 'draft')}
                      >
                        {row.global_sending_paused
                          ? 'Envois globalement suspendus'
                          : row.paused
                            ? 'Boîte en pause'
                            : row.operational_status === 'waiting_first_send'
                              ? 'En attente de démarrage'
                              : row.operational_status === 'blocked'
                                ? 'Condition à résoudre'
                                : row.operational_status === 'sending'
                                  ? 'Activité en cours'
                                  : 'Cadence à revoir'}
                      </span>
                    </div>
                    <div className="sender-counters">
                      <span>
                        <strong>{row.ramp?.effective_limit ?? row.daily_limit}</strong> plafond actuel / 24 h
                      </span>
                      <span>
                        <strong>{row.external_daily_reserve}</strong> réservés hors application
                      </span>
                      <span>
                        <strong>{row.available_24h}</strong> envois possibles maintenant
                      </span>
                      <span>
                        <strong>{Math.round(row.min_interval_seconds / 60)} min</strong> entre deux emails
                      </span>
                    </div>
                    <p>
                      <strong>
                        {row.status === 'authenticated'
                          ? 'Connectée · SMTP et IMAP vérifiés'
                          : 'Connexion à vérifier'}
                      </strong>{' '}
                      · {row.used_24h} tentative(s) de campagne sur 24 h · cible {row.daily_limit} / 24 h
                    </p>
                    <Warmup api={api} admin={admin} row={row} refresh={load} />
                    {row.blocking_reasons.length > 0 && (
                      <details>
                        <summary>{row.blocking_reasons.length} condition(s) avant envoi</summary>
                        <ul>
                          {row.blocking_reasons.map((reason: string) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {admin && (
                      <div className="actions">
                        <button
                          disabled={busy}
                          onClick={() =>
                            run(async () => {
                              const r = await api('/senders/' + row.id + '/check', 'POST');
                              if (r.status !== 'authenticated')
                                throw new Error(r.state.connection_error || 'Connexion indisponible.');
                            }, 'Accès SMTP et IMAP vérifiés. Aucun email envoyé.')
                          }
                        >
                          Tester les accès
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => {
                            setPolicy(row);
                            setError('');
                          }}
                        >
                          Plafonds de la boîte
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            run(async () => {
                              setEditor(await api('/senders/' + row.id));
                              setPolicy(null);
                            }, '')
                          }
                        >
                          Connexion
                        </button>
                      </div>
                    )}
                    {policy && policy.id === row.id && (
                      <form
                        className="sender-policy"
                        key={policy.id}
                        onSubmit={(event) => {
                          event.preventDefault();
                          const f = Object.fromEntries(new FormData(event.currentTarget));
                          run(async () => {
                            await api('/senders/' + row.id + '/policy', 'PATCH', {
                              paused: f.paused === 'on',
                              warmup_status: row.warmup_status,
                              readiness_note: row.readiness_note,
                              daily_limit: Number(f.daily_limit),
                              total_daily_limit: Number(f.total_daily_limit),
                              external_daily_reserve: Number(f.external_daily_reserve),
                              min_interval_seconds: Number(f.interval_minutes) * 60,
                            });
                            setPolicy(null);
                          }, 'Plafonds de la boîte enregistrés.');
                        }}
                      >
                        <div className="form-grid">
                          {field(
                            'Plafond cible de prospection / 24 h',
                            'daily_limit',
                            policy.daily_limit,
                            'number',
                          )}
                          {field(
                            'Total de la boîte / 24 h',
                            'total_daily_limit',
                            policy.total_daily_limit,
                            'number',
                          )}
                          {field(
                            'Réserve hors application / 24 h',
                            'external_daily_reserve',
                            policy.external_daily_reserve,
                            'number',
                          )}
                          {field(
                            'Intervalle minimal (minutes)',
                            'interval_minutes',
                            policy.min_interval_seconds / 60,
                            'number',
                          )}
                        </div>
                        <label className="checkbox">
                          <input name="paused" type="checkbox" defaultChecked={policy.paused} />
                          Boîte en pause
                        </label>
                        <div className="actions">
                          <button className="primary" disabled={busy}>
                            Enregistrer les plafonds de la boîte
                          </button>
                          <button type="button" onClick={() => setPolicy(null)}>
                            Annuler
                          </button>
                        </div>
                      </form>
                    )}
                  </article>
                ))}
              {limits.length > 0 && (
                <details className="shared-limits">
                  <summary>Plafonds partagés</summary>
                  <h3>Plafonds partagés par domaine et fournisseur</h3>
                  <p>
                    Chaque plafond couvre les tentatives de toutes ses boîtes et leurs réserves hors
                    application. Un plafond à zéro bloque l’envoi.
                  </p>
                  {limits.map((limit) => (
                    <form
                      key={limit.id + '-' + limit.updated_at}
                      className="group-limit"
                      onSubmit={(event) => {
                        event.preventDefault();
                        const f = Object.fromEntries(new FormData(event.currentTarget));
                        run(
                          () =>
                            api('/email-limits', 'POST', {
                              scope: limit.scope,
                              key: limit.key,
                              daily_limit: Number(f.limit),
                              note: f.note,
                            }),
                          'Plafond partagé enregistré.',
                        );
                      }}
                    >
                      <div>
                        <strong>{limit.key}</strong>
                        <small>
                          {limit.scope === 'domain' ? 'Domaine' : 'Fournisseur'} · {limit.used_24h} tentatives
                          + {limit.external_daily_reserve} réservés
                        </small>
                      </div>
                      <label className="field">
                        <span>Maximum / 24 h</span>
                        <input
                          name="limit"
                          type="number"
                          min="0"
                          max="50000"
                          defaultValue={limit.daily_limit}
                          disabled={!admin}
                        />
                      </label>
                      <label className="field">
                        <span>Base du plafond</span>
                        <input
                          name="note"
                          defaultValue={limit.note}
                          placeholder="Forfait et quota vérifiés"
                          disabled={!admin}
                        />
                      </label>
                      {admin && <button disabled={busy}>Enregistrer</button>}
                    </form>
                  ))}
                </details>
              )}
            </>
          )}
        </div>
      </section>
    </>
  );
}

export function SenderChoice({
  api,
  contact,
  rows,
  disabled,
  onChange,
}: {
  api: Api;
  contact: Row;
  rows: Row[];
  disabled: boolean;
  onChange: (fn: () => Promise<unknown>) => void;
}) {
  const [value, setValue] = useState(contact.email_sender_id || '');
  useEffect(() => setValue(contact.email_sender_id || ''), [contact.id, contact.email_sender_id]);
  return (
    <div className="sender-choice">
      <label className="field">
        <span>Boîte expéditrice pour ce prospect</span>
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">Attribution automatique parmi les boîtes prêtes</option>
          {rows.map((row) => (
            <option key={row.id} value={row.id}>
              {row.from_email}
              {row.paused ? ' · en pause' : ''}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={disabled || !rows.length}
        onClick={() =>
          onChange(() => api('/contacts/' + contact.id + '/sender', 'POST', {sender_id: value || null}))
        }
      >
        Attribuer la boîte
      </button>
      <p>
        L’expéditeur reste fixe après la première tentative. Un changement avant envoi impose une nouvelle
        validation des messages.
      </p>
    </div>
  );
}
