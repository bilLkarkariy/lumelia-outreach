import {useState} from 'react';
import {errorText, type Row} from './api';
export const languages: Row = {
  fr: 'Français',
  en: 'Anglais',
  de: 'Allemand',
  nl: 'Néerlandais',
  it: 'Italien',
};
export const markets: Row = {
  FR: 'France',
  GB: 'Royaume-Uni',
  DE: 'Allemagne',
  BE: 'Belgique',
  CH: 'Suisse',
  US: 'États-Unis',
};
export const timezones: Row = {
  FR: 'Europe/Paris',
  GB: 'Europe/London',
  DE: 'Europe/Berlin',
  BE: 'Europe/Brussels',
  CH: 'Europe/Zurich',
  US: 'America/New_York',
};
export function MarketField({value = 'FR', onChange}: {value?: string; onChange?: (value: string) => void}) {
  return (
    <label className="field">
      <span>Pays du marché</span>
      <select name="country" defaultValue={value} required onChange={(e) => onChange?.(e.target.value)}>
        {!markets[value] && <option value={value}>{value}</option>}
        {Object.entries(markets).map(([code, name]) => (
          <option key={code} value={code}>
            {String(name)}
          </option>
        ))}
      </select>
    </label>
  );
}
export function LanguageReview({
  contact,
  api,
  onSaved,
}: {
  contact: Row;
  api: (path: string, method?: string, data?: unknown) => Promise<unknown>;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const current = contact.communication_language || {};
  return (
    <section className="language-review">
      <div className="inline-header">
        <h3>Langue du contact</h3>
        <button onClick={() => setEditing(!editing)}>{editing ? 'Fermer' : 'Vérifier la langue'}</button>
      </div>
      <p>
        <strong>{languages[current.language] || 'À déterminer'}</strong>
        {current.reviewed_at && ' · revue le ' + new Date(current.reviewed_at).toLocaleDateString('fr-FR')}
      </p>
      {current.note && <p>{current.note}</p>}
      {current.source_url && (
        <a href={current.source_url} target="_blank" rel="noreferrer">
          Source de la langue
        </a>
      )}
      {error && (
        <p role="alert" className="alert error">
          {error}
        </p>
      )}
      {editing && (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            const f = Object.fromEntries(new FormData(event.currentTarget));
            setBusy(true);
            setError('');
            try {
              await api('/contacts/' + contact.id + '/language', 'POST', f);
              setEditing(false);
              onSaved();
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="field">
            <span>Langue professionnelle vérifiée</span>
            <select name="language" defaultValue={current.language || ''}>
              <option value="">À déterminer</option>
              {Object.entries(languages).map(([code, label]) => (
                <option key={code} value={code}>
                  {String(label)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Observation : échange, profil ou page professionnelle</span>
            <textarea
              name="note"
              required
              minLength={10}
              maxLength={2000}
              defaultValue={current.note || ''}
            />
          </label>
          <label className="field">
            <span>Source (URL facultative)</span>
            <input name="source_url" type="url" defaultValue={current.source_url || ''} />
          </label>
          <p>
            Le pays ne suffit pas à déterminer la langue. La revue impose une nouvelle validation des
            messages.
          </p>
          <button className="primary" disabled={busy}>
            Enregistrer la langue vérifiée
          </button>
        </form>
      )}
    </section>
  );
}
