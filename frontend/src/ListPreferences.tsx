import {createContext, useContext, useRef, useState, type ReactNode} from 'react';
import {Bookmark, Check, Pencil, Plus, Trash2, X} from 'lucide-react';
import type {Column, ListState, Row} from './ListTable';

type Saved = {id: string; name: string; state: Partial<ListState>};
type Store = {values: Record<string, Partial<ListState>>; saved: Record<string, Saved[]>};
export type ListChange = (patch: Partial<ListState>, resetPage?: boolean) => void;
const empty: Store = {values: {}, saved: {}};
const Context = createContext<{store: Store; error: string; update: (change: (old: Store) => Store) => void}>(
  {store: empty, error: '', update: () => {}},
);

function preferences(stored: unknown): Partial<ListState> {
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return {};
  const value: Row = stored;
  return {
    q: typeof value.q === 'string' ? value.q.slice(0, 120) : '',
    filters: Object.fromEntries(
      Object.entries(value.filters || {})
        .filter(([key, val]) => /^[a-z_]+$/.test(key) && typeof val === 'string')
        .map(([key, val]) => [key, String(val).slice(0, 120)]),
    ),
    page: Number.isInteger(value.page) && value.page > 0 ? value.page : 1,
    size: [10, 25, 50, 100].includes(value.size) ? value.size : 25,
    ...(typeof value.sort === 'string' ? {sort: value.sort.slice(0, 50)} : {}),
    direction: value.direction === 'desc' ? 'desc' : 'asc',
    ...(Array.isArray(value.hidden)
      ? {hidden: value.hidden.filter((item: unknown) => typeof item === 'string').slice(0, 40)}
      : {}),
    compact: value.compact === true,
    layout: 'table',
    selection: [],
    savedId: typeof value.savedId === 'string' ? value.savedId.slice(0, 40) : '',
  };
}
function read(key: string): Store {
  try {
    const stored = JSON.parse(localStorage.getItem(key) || '{}');
    if (stored.version !== 1) return empty;
    return {
      values: Object.fromEntries(
        Object.entries(stored.values || {})
          .slice(0, 100)
          .map(([resource, value]) => [resource, preferences(value)]),
      ),
      saved: Object.fromEntries(
        Object.entries(stored.saved || {})
          .slice(0, 100)
          .map(([resource, rows]) => [
            resource,
            Array.isArray(rows)
              ? rows
                  .filter((row) => typeof row?.name === 'string' && typeof row?.id === 'string')
                  .slice(0, 20)
                  .map((row) => ({
                    id: row.id.slice(0, 40),
                    name: row.name.slice(0, 80),
                    state: preferences(row.state),
                  }))
              : [],
          ]),
      ),
    };
  } catch {
    return empty;
  }
}
export function ListPreferences({
  children,
  storageKey = 'lumelia-list-preferences-v1',
}: {
  children: ReactNode;
  storageKey?: string;
}) {
  const [store, setStore] = useState<Store>(() => read(storageKey)),
    [error, setError] = useState('');
  const current = useRef(store);
  function update(change: (old: Store) => Store) {
    const next = change(current.current);
    current.current = next;
    setStore(next);
    try {
      const values = Object.fromEntries(
        Object.entries(next.values).map(([key, value]) => [key, {...value, selection: []}]),
      );
      localStorage.setItem(storageKey, JSON.stringify({version: 1, values, saved: next.saved}));
      setError('');
    } catch {
      setError(
        'Le navigateur ne peut pas conserver les réglages. Ils restent disponibles jusqu’à la fermeture de cette page.',
      );
    }
  }
  return <Context.Provider value={{store, update, error}}>{children}</Context.Provider>;
}
export function useListState(key: string, initial: Partial<ListState> = {}): [ListState, ListChange] {
  const context = useContext(Context);
  const state: ListState = {
    q: '',
    filters: {},
    page: 1,
    size: 25,
    sort: 'name',
    direction: 'asc',
    hidden: [],
    compact: false,
    selection: [],
    layout: 'table',
    ...initial,
    ...context.store.values[key],
    resource: key,
  };
  const change: ListChange = (patch, resetPage = true) =>
    context.update((old) => {
      const previous = {...state, ...old.values[key]};
      const custom = ['q', 'filters', 'size', 'sort', 'direction', 'hidden', 'compact'].some(
        (key) => key in patch,
      );
      return {
        ...old,
        values: {
          ...old.values,
          [key]: {
            ...previous,
            ...(custom && !('savedId' in patch) ? {savedId: ''} : {}),
            ...patch,
            ...(resetPage ? {page: 1} : {}),
            ...('q' in patch || 'filters' in patch ? {selection: []} : {}),
          },
        },
      };
    });
  return [state, change];
}
export function SavedViews({state, change}: {state: ListState; change: ListChange; columns: Column[]}) {
  const {store, update, error} = useContext(Context);
  const views = store.saved[state.resource] || [],
    active = views.find((row) => row.id === state.savedId);
  const [mode, setMode] = useState<'new' | 'rename' | ''>(''),
    [name, setName] = useState(''),
    [message, setMessage] = useState('');
  function save() {
    const title = name.trim();
    if (!title) {
      setMessage('Donnez un nom à cette vue.');
      return;
    }
    if (
      views.some(
        (row) =>
          row.name.toLocaleLowerCase('fr') === title.toLocaleLowerCase('fr') &&
          (mode !== 'rename' || row.id !== active?.id),
      )
    ) {
      setMessage('Ce nom est déjà utilisé.');
      return;
    }
    if (mode === 'new' && views.length >= 20) {
      setMessage('Vous pouvez conserver 20 vues par liste. Supprimez une ancienne vue pour en ajouter une.');
      return;
    }
    const id = mode === 'rename' && active ? active.id : crypto.randomUUID();
    update((old) => ({
      ...old,
      values: {...old.values, [state.resource]: {...state, savedId: id}},
      saved: {
        ...old.saved,
        [state.resource]:
          mode === 'rename'
            ? views.map((row) => (row.id === id ? {...row, name: title} : row))
            : [...views, {id, name: title, state: {...preferences(state), page: 1, savedId: id}}],
      },
    }));
    setMode('');
    setMessage('Vue enregistrée dans ce navigateur.');
  }
  return (
    <div className="lt-saved-views">
      <div className="lt-saved-main">
        <Bookmark size={15} />
        <select
          aria-label="Vues enregistrées"
          value={active?.id || ''}
          onChange={(event) => {
            const saved = views.find((row) => row.id === event.target.value);
            if (saved) {
              change({...saved.state, savedId: saved.id, page: 1, selection: []});
              setMessage('');
            }
          }}
        >
          <option value="" disabled>
            {views.length ? 'Vue personnalisée' : 'Mes vues enregistrées'}
          </option>
          {views.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="lt-save-view"
          onClick={() => {
            setMode('new');
            setName('');
            setMessage('');
          }}
        >
          <Plus size={14} />
          Enregistrer une vue
        </button>
        {active && (
          <>
            <button
              type="button"
              className="lt-icon"
              aria-label="Renommer cette vue"
              onClick={() => {
                setMode('rename');
                setName(active.name);
                setMessage('');
              }}
            >
              <Pencil size={14} />
            </button>
            <button
              type="button"
              className="lt-icon"
              aria-label="Supprimer cette vue enregistrée"
              onClick={() => {
                update((old) => ({
                  ...old,
                  saved: {...old.saved, [state.resource]: views.filter((row) => row.id !== active.id)},
                  values: {...old.values, [state.resource]: {...state, savedId: ''}},
                }));
                setMode('');
                setMessage('Vue supprimée. Les réglages courants sont conservés.');
              }}
            >
              <Trash2 size={14} />
            </button>
          </>
        )}
      </div>
      {mode && (
        <form
          className="lt-save-form"
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <label>
            Nom de la vue
            <input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              required
              placeholder="Ex. Décideurs avec ligne directe"
            />
          </label>
          <button type="submit">
            <Check size={15} />
            {mode === 'rename' ? 'Renommer' : 'Enregistrer'}
          </button>
          <button
            type="button"
            className="lt-icon"
            aria-label="Annuler l’enregistrement de la vue"
            onClick={() => setMode('')}
          >
            <X size={15} />
          </button>
        </form>
      )}
      {(error || message) && (
        <small role={error ? 'alert' : 'status'} className={error ? 'lt-preference-error' : ''}>
          {error || message}
        </small>
      )}
    </div>
  );
}
