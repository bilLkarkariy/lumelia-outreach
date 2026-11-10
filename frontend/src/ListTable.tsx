import {Fragment, useEffect, useRef, useState, type ReactNode} from 'react';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Columns3,
  ListFilter,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import './list-table.css';
import {SavedViews} from './ListPreferences';
import type {Api, Row} from './api';

export type {Api, Row};
export {ListPreferences, useListState} from './ListPreferences';

export type ListState = {
  resource: string;
  savedId?: string;
  q: string;
  filters: Record<string, string>;
  page: number;
  size: number;
  sort: string;
  direction: 'asc' | 'desc';
  hidden: string[];
  compact: boolean;
  selection: string[];
  layout: string;
};
export type Column = {
  key: string;
  label: string;
  sort?: string;
  width?: number;
  fixed?: boolean;
  optional?: boolean;
  mobile?: boolean;
  render: (row: Row) => ReactNode;
};
export type Filter = {key: string; label: string; options: Record<string, string>; extra?: boolean};
type Change = (patch: Partial<ListState>, resetPage?: boolean) => void;
export function useServerList(
  resource: string,
  state: ListState,
  change: Change,
  api: Api,
  revision: unknown,
  scope: Record<string, string> = {},
) {
  const [result, setResult] = useState<Row | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(''),
    [retry, setRetry] = useState(0);
  const sequence = useRef(0),
    lastQuery = useRef('');
  const changeRef = useRef(change);
  changeRef.current = change;
  const parameters = new URLSearchParams({
    q: state.q,
    page: String(state.page),
    page_size: String(state.size),
    sort: state.sort,
    direction: state.direction,
    ...state.filters,
    ...scope,
  }).toString();
  useEffect(() => {
    const request = ++sequence.current;
    const queryChanged = lastQuery.current !== parameters;
    setLoading(true);
    setError('');
    const timer = setTimeout(
      () => {
        api('/lists/' + resource + '?' + parameters)
          .then((value) => {
            if (request !== sequence.current) return;
            setResult(value);
            setLoading(false);
            lastQuery.current = parameters;
            if (value.page !== state.page) changeRef.current({page: value.page}, false);
          })
          .catch((error) => {
            if (request === sequence.current) {
              setError(error.message);
              setLoading(false);
            }
          });
      },
      queryChanged && state.q ? 220 : 0,
    );
    return () => {
      clearTimeout(timer);
      // eslint-disable-next-line react-hooks/exhaustive-deps -- sequence is a request counter, not a DOM node: the cleanup must invalidate the latest request
      sequence.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- page and search are already carried by parameters
  }, [resource, parameters, revision, retry]);
  return {result, loading, error, retry: () => setRetry((value) => value + 1)};
}
export function ListSummary({
  items,
  label = 'Chiffres de l’espace',
  collapseMobile = true,
}: {
  items: [ReactNode, string][];
  label?: string;
  collapseMobile?: boolean;
}) {
  const content = items.map(([value, label]) => (
    <div key={label}>
      <strong>{value ?? '—'}</strong>
      <span>{label}</span>
    </div>
  ));
  return (
    <>
      <div className="lt-summary" aria-label={label}>
        {content}
      </div>
      {collapseMobile && (
        <details className="lt-summary-mobile">
          <summary>
            {label}
            <ChevronDown size={15} />
          </summary>
          <div className="lt-summary">{content}</div>
        </details>
      )}
    </>
  );
}
export function ListToolbar({
  state,
  change,
  filters,
  columns,
  placeholder,
  searchLabel,
  children,
}: {
  state: ListState;
  change: Change;
  filters: Filter[];
  columns: Column[];
  placeholder: string;
  searchLabel: string;
  children?: ReactNode;
}) {
  const [extra, setExtra] = useState(false),
    [columnMenu, setColumnMenu] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!columnMenu) return;
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) setColumnMenu(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setColumnMenu(false);
        menu.current?.querySelector('button')?.focus();
      }
    };
    window.addEventListener('pointerdown', outside);
    window.addEventListener('keydown', escape);
    return () => {
      window.removeEventListener('pointerdown', outside);
      window.removeEventListener('keydown', escape);
    };
  }, [columnMenu]);
  const active = filters.filter((filter) => state.filters[filter.key]);
  return (
    <div className="lt-tools">
      <SavedViews state={state} change={change} columns={columns} />
      <div className="lt-toolbar">
        <div className="lt-search">
          <Search size={17} />
          <input
            type="search"
            aria-label={searchLabel}
            placeholder={placeholder}
            value={state.q}
            maxLength={120}
            onChange={(event) => change({q: event.target.value})}
          />
          {state.q && (
            <button className="lt-icon" aria-label="Effacer la recherche" onClick={() => change({q: ''})}>
              <X size={15} />
            </button>
          )}
        </div>
        <div className="lt-toolbar-actions">
          {children}
          <button
            className="lt-density"
            aria-pressed={state.compact}
            onClick={() => change({compact: !state.compact}, false)}
          >
            <SlidersHorizontal size={15} />
            {state.compact ? 'Compact' : 'Confort'}
          </button>
          <div className="lt-column-control" ref={menu}>
            <button aria-expanded={columnMenu} onClick={() => setColumnMenu(!columnMenu)}>
              <Columns3 size={15} />
              Colonnes
              <ChevronDown size={13} />
            </button>
            {columnMenu && (
              <div className="lt-column-menu">
                <strong>Colonnes du tableau</strong>
                {columns
                  .filter((column) => !column.fixed)
                  .map((column) => (
                    <label key={column.key}>
                      <input
                        type="checkbox"
                        checked={!state.hidden.includes(column.key)}
                        onChange={(event) =>
                          change(
                            {
                              hidden: event.target.checked
                                ? state.hidden.filter((key) => key !== column.key)
                                : [...state.hidden, column.key],
                            },
                            false,
                          )
                        }
                      />
                      <span>{column.label}</span>
                    </label>
                  ))}
                <button
                  onClick={() =>
                    change(
                      {hidden: columns.filter((column) => column.optional).map((column) => column.key)},
                      false,
                    )
                  }
                >
                  Rétablir les colonnes
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
      {filters.length > 0 && (
        <div className="lt-filters">
          {filters
            .filter((filter) => extra || !filter.extra || state.filters[filter.key])
            .map((filter) => (
              <label className="lt-filter" key={filter.key}>
                <span>{filter.label}</span>
                <select
                  aria-label={'Filtrer · ' + filter.label}
                  value={state.filters[filter.key] || ''}
                  onChange={(event) =>
                    change({filters: {...state.filters, [filter.key]: event.target.value}})
                  }
                >
                  <option value="">Tous</option>
                  {Object.entries(filter.options).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          {filters.some((filter) => filter.extra) && (
            <button className="lt-more-filters" aria-expanded={extra} onClick={() => setExtra(!extra)}>
              <ListFilter size={15} />
              {extra ? 'Moins de filtres' : 'Plus de filtres'}
            </button>
          )}
        </div>
      )}
      {(active.length > 0 || state.q) && (
        <div className="lt-active-filters">
          <span>{active.length + (state.q ? 1 : 0)} filtre(s)</span>
          {active.map((filter) => (
            <button
              key={filter.key}
              aria-label={'Retirer le filtre ' + filter.label}
              onClick={() => change({filters: {...state.filters, [filter.key]: ''}})}
            >
              {filter.label} : {filter.options[state.filters[filter.key]] || state.filters[filter.key]}
              <X size={12} />
            </button>
          ))}
          <button className="lt-reset" onClick={() => change({q: '', filters: {}})}>
            Réinitialiser
          </button>
        </div>
      )}
    </div>
  );
}
function PageCheckbox({
  checked,
  mixed,
  label,
  disabled,
  onChange,
}: {
  checked: boolean;
  mixed?: boolean;
  label: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!mixed && !checked;
  }, [mixed, checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label={label}
      checked={checked}
      disabled={disabled}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}
export function ListTable({
  title,
  rows,
  total,
  overall,
  state,
  change,
  columns,
  loading = false,
  error = '',
  onRetry,
  emptyTitle = 'Aucun résultat',
  emptyText = 'Modifiez vos filtres ou votre recherche.',
  selection,
  expandedId,
  renderExpanded,
}: {
  title: string;
  rows: Row[];
  total: number;
  overall?: number;
  state: ListState;
  change: Change;
  columns: Column[];
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyText?: string;
  selection?: {
    ids: string[];
    onChange: (ids: string[]) => void;
    label: (row: Row) => string;
    disabled?: boolean;
    max?: number;
  };
  expandedId?: string;
  renderExpanded?: (row: Row) => ReactNode;
}) {
  const contentRef = useRef<HTMLDivElement>(null);
  const go = (page: number) => {
    change({page}, false);
    requestAnimationFrame(() => contentRef.current?.scrollIntoView({block: 'start', behavior: 'instant'}));
  };
  const visible = columns.filter((column) => column.fixed || !state.hidden.includes(column.key));
  const pages = Math.max(1, Math.ceil(total / state.size)),
    currentPage = Math.min(state.page, pages);
  const first = total ? (currentPage - 1) * state.size + 1 : 0,
    last = Math.min(currentPage * state.size, total);
  const pageIds = rows.map((row) => row.id),
    selectedOnPage = selection?.ids.filter((id) => pageIds.includes(id)) || [];
  const selectPage = (checked: boolean) => {
    if (!selection) return;
    const others = selection.ids.filter((id) => !pageIds.includes(id));
    selection.onChange(checked ? [...others, ...pageIds].slice(0, selection.max || 100) : others);
  };
  const pagination = (
    <div className="lt-pagination">
      <div>
        <span role="status" aria-live="polite">
          {loading ? 'Actualisation…' : `${first}–${last} sur ${total.toLocaleString('fr-FR')}`}
        </span>
        <label>
          <span>Lignes par page</span>
          <select
            aria-label={'Lignes par page · ' + title}
            value={state.size}
            onChange={(event) => change({size: Number(event.target.value)})}
          >
            {[10, 25, 50, 100].map((size) => (
              <option value={size} key={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>
      <nav aria-label={'Pagination · ' + title}>
        <button aria-label="Première page" disabled={loading || currentPage === 1} onClick={() => go(1)}>
          <ChevronsLeft size={16} />
        </button>
        <button
          aria-label="Page précédente"
          disabled={loading || currentPage === 1}
          onClick={() => go(currentPage - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span>
          Page <strong>{currentPage}</strong> sur {pages}
        </span>
        <button
          aria-label="Page suivante"
          disabled={loading || currentPage >= pages}
          onClick={() => go(currentPage + 1)}
        >
          <ChevronRight size={16} />
        </button>
        <button
          aria-label="Dernière page"
          disabled={loading || currentPage >= pages}
          onClick={() => go(pages)}
        >
          <ChevronsRight size={16} />
        </button>
      </nav>
    </div>
  );
  return (
    <div ref={contentRef} className={'lt-content ' + (state.compact ? 'lt-compact' : '')} aria-busy={loading}>
      <div className="lt-result-line">
        <strong>
          {title}
          <span>{total.toLocaleString('fr-FR')}</span>
        </strong>
        <small>
          {overall != null && overall !== total
            ? `sur ${overall.toLocaleString('fr-FR')} dans l’espace`
            : 'Toutes les données disponibles'}
          {selection && selection.ids.length > 0 ? ` · ${selection.ids.length} sélectionné(s)` : ''}
        </small>
        {loading && (
          <span className="lt-progress" role="status">
            Actualisation…
          </span>
        )}
        <nav className="lt-top-pagination" aria-label={'Pagination rapide · ' + title}>
          <button
            aria-label="Page précédente en haut du tableau"
            disabled={loading || currentPage === 1}
            onClick={() => go(currentPage - 1)}
          >
            <ChevronLeft size={15} />
          </button>
          <span>
            {currentPage} / {pages}
          </span>
          <button
            aria-label="Page suivante en haut du tableau"
            disabled={loading || currentPage >= pages}
            onClick={() => go(currentPage + 1)}
          >
            <ChevronRight size={15} />
          </button>
        </nav>
      </div>
      {error ? (
        <div className="lt-empty" role="alert">
          <h2>La liste n’a pas pu être chargée</h2>
          <p>{error}</p>
          <button onClick={onRetry}>Réessayer</button>
        </div>
      ) : !rows.length ? (
        <div className="lt-empty" role="status">
          {loading ? (
            <>
              <div className="lt-skeleton" />
              <div className="lt-skeleton" />
              <p>Chargement des résultats…</p>
            </>
          ) : (
            <>
              <Search size={24} />
              <h2>{emptyTitle}</h2>
              <p>{emptyText}</p>
              {(state.q || Object.values(state.filters).some(Boolean)) && (
                <button onClick={() => change({q: '', filters: {}})}>Effacer les filtres</button>
              )}
            </>
          )}
        </div>
      ) : (
        <>
          <div className="lt-table-scroll" role="region" aria-label={'Tableau · ' + title} tabIndex={0}>
            <table className="lt-table">
              <caption className="lt-sr-only">
                {title} · {first} à {last} sur {total}
              </caption>
              <thead>
                <tr>
                  {selection && (
                    <th className="lt-select-cell" scope="col">
                      <PageCheckbox
                        label="Sélectionner cette page (100 contacts maximum)"
                        checked={selectedOnPage.length === rows.length}
                        mixed={selectedOnPage.length > 0}
                        disabled={
                          loading ||
                          selection.disabled ||
                          (selection.ids.length >= (selection.max || 100) && selectedOnPage.length === 0)
                        }
                        onChange={selectPage}
                      />
                    </th>
                  )}
                  {visible.map((column, index) => (
                    <th
                      key={column.key}
                      scope="col"
                      className={
                        (index === 0 ? 'lt-primary-cell ' : '') +
                        (column.key === 'actions' ? 'lt-actions-cell' : '')
                      }
                      style={{width: column.width, minWidth: column.width}}
                      aria-sort={
                        column.sort
                          ? state.sort === column.sort
                            ? state.direction === 'asc'
                              ? 'ascending'
                              : 'descending'
                            : 'none'
                          : undefined
                      }
                    >
                      {column.sort ? (
                        <button
                          onClick={() =>
                            change({
                              sort: column.sort!,
                              direction:
                                state.sort === column.sort && state.direction === 'asc' ? 'desc' : 'asc',
                            })
                          }
                        >
                          {column.label}
                          {state.sort === column.sort ? (
                            state.direction === 'asc' ? (
                              <ArrowUp size={13} />
                            ) : (
                              <ArrowDown size={13} />
                            )
                          ) : (
                            <ArrowUpDown size={12} />
                          )}
                        </button>
                      ) : column.label === 'Actions' ? (
                        <span className="lt-sr-only">Actions</span>
                      ) : (
                        column.label
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Fragment key={row.id}>
                    <tr className={selection?.ids.includes(row.id) ? 'lt-selected' : ''}>
                      {selection && (
                        <td className="lt-select-cell">
                          <PageCheckbox
                            label={'Sélectionner ' + selection.label(row)}
                            checked={selection.ids.includes(row.id)}
                            disabled={
                              loading ||
                              selection.disabled ||
                              (!selection.ids.includes(row.id) &&
                                selection.ids.length >= (selection.max || 100))
                            }
                            onChange={(checked) =>
                              selection.onChange(
                                checked
                                  ? [...selection.ids, row.id]
                                  : selection.ids.filter((id) => id !== row.id),
                              )
                            }
                          />
                        </td>
                      )}
                      {visible.map((column, index) => (
                        <td
                          key={column.key}
                          className={
                            (index === 0 ? 'lt-primary-cell ' : '') +
                            (column.key === 'actions' ? 'lt-actions-cell' : '')
                          }
                        >
                          {column.render(row)}
                        </td>
                      ))}
                    </tr>
                    {expandedId === row.id && renderExpanded && (
                      <tr className="lt-expanded-row">
                        <td colSpan={visible.length + (selection ? 1 : 0)}>{renderExpanded(row)}</td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <div className="lt-mobile-list">
            {selection && (
              <label className="lt-mobile-select">
                <PageCheckbox
                  label="Sélectionner cette page sur mobile"
                  checked={selectedOnPage.length === rows.length}
                  mixed={selectedOnPage.length > 0}
                  disabled={
                    loading ||
                    selection.disabled ||
                    (selection.ids.length >= (selection.max || 100) && selectedOnPage.length === 0)
                  }
                  onChange={selectPage}
                />
                Sélectionner cette page
              </label>
            )}
            {rows.map((row) => (
              <article
                className={'lt-mobile-card ' + (selection?.ids.includes(row.id) ? 'lt-selected' : '')}
                key={row.id}
              >
                <header>
                  {selection && (
                    <PageCheckbox
                      label={'Sélectionner ' + selection.label(row) + ' sur mobile'}
                      checked={selection.ids.includes(row.id)}
                      disabled={
                        loading ||
                        selection.disabled ||
                        (!selection.ids.includes(row.id) && selection.ids.length >= (selection.max || 100))
                      }
                      onChange={(checked) =>
                        selection.onChange(
                          checked ? [...selection.ids, row.id] : selection.ids.filter((id) => id !== row.id),
                        )
                      }
                    />
                  )}
                  <div>{visible[0]?.render(row)}</div>
                  {visible.find((column) => column.key === 'actions')?.render(row)}
                </header>
                <dl>
                  {visible
                    .slice(1)
                    .filter((column) => column.key !== 'actions' && column.mobile !== false)
                    .map((column) => (
                      <div key={column.key}>
                        <dt>{column.label}</dt>
                        <dd>{column.render(row)}</dd>
                      </div>
                    ))}
                </dl>
                {expandedId === row.id && renderExpanded?.(row)}
              </article>
            ))}
          </div>
        </>
      )}
      {pagination}
    </div>
  );
}
export function TableBadge({value, children}: {value: string; children?: ReactNode}) {
  return <span className={'lt-badge lt-badge-' + value}>{children || value}</span>;
}
export function SelectionBar({
  children,
  count,
  onClear,
}: {
  children: ReactNode;
  count: number;
  onClear: () => void;
}) {
  if (!count) return null;
  return (
    <div className="lt-selection">
      <span>
        <Check size={15} />
        <strong>{count} sélectionné(s)</strong>
        {count >= 100 && <small>Limite du lot atteinte</small>}
      </span>
      <div>
        {children}
        <button aria-label="Effacer la sélection" onClick={onClear}>
          <X size={15} />
          Effacer
        </button>
      </div>
    </div>
  );
}

export function localList(
  rows: Row[],
  state: ListState,
  search: (row: Row) => string,
  accepts: (row: Row) => boolean,
  values: Record<string, (row: Row) => unknown>,
) {
  const normalize = (value: string) =>
    value.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase('fr').trim();
  const query = normalize(state.q);
  const filtered = rows.filter((row) => (!query || normalize(search(row)).includes(query)) && accepts(row));
  const value = values[state.sort] || values.name;
  filtered.sort((a, b) => {
    const av = value(a),
      bv = value(b);
    if (av == null && bv != null) return 1;
    if (bv == null && av != null) return -1;
    const compared =
      typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av ?? '').localeCompare(String(bv ?? ''), 'fr', {numeric: true, sensitivity: 'base'});
    return (state.direction === 'desc' ? -compared : compared) || a.id.localeCompare(b.id);
  });
  const page = Math.min(state.page, Math.max(1, Math.ceil(filtered.length / state.size)));
  return {items: filtered.slice((page - 1) * state.size, page * state.size), total: filtered.length};
}
