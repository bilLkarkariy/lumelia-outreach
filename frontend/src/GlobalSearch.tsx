import {useEffect, useRef, useState} from 'react';
import {Search, X, ArrowUpRight} from 'lucide-react';
import {Feedback, RouteLink, type Api, type Navigate, type Row} from './WorkspaceUI';

export function GlobalSearch({
  api,
  navigate,
  request = 0,
  hideTrigger = false,
}: {
  api: Api;
  navigate: Navigate;
  request?: number;
  hideTrigger?: boolean;
}) {
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState(''),
    [data, setData] = useState<Row | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    trigger = useRef<HTMLButtonElement>(null),
    input = useRef<HTMLInputElement>(null),
    returnFocus = useRef<HTMLElement | null>(null);
  const shortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        !event.defaultPrevented &&
        !event.repeat &&
        (event.metaKey || event.ctrlKey) &&
        !event.altKey &&
        event.key.toLowerCase() === 'k'
      ) {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  useEffect(() => {
    if (request > 0) setOpen(true);
  }, [request]);
  useEffect(() => {
    if (open) {
      returnFocus.current = document.activeElement as HTMLElement | null;
      dialog.current?.showModal();
      input.current?.focus();
    } else if (dialog.current?.open) {
      dialog.current.close();
      const previous = returnFocus.current;
      if (previous?.isConnected && previous.getClientRects().length) previous.focus({preventScroll: true});
      else
        (trigger.current || document.querySelector<HTMLButtonElement>('.sb-toggle'))?.focus({
          preventScroll: true,
        });
    }
  }, [open]);
  useEffect(() => {
    let alive = true;
    setError('');
    setData(null);
    setLoading(query.trim().length >= 2);
    if (query.trim().length < 2) return;
    const timer = setTimeout(
      () =>
        api('/search?q=' + encodeURIComponent(query))
          .then((result) => {
            if (alive) {
              setData(result);
              setLoading(false);
            }
          })
          .catch((error) => {
            if (alive) {
              setError(error.message);
              setLoading(false);
            }
          }),
      200,
    );
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, query]);

  return (
    <>
      {!hideTrigger && (
        <button
          ref={trigger}
          aria-label="Rechercher partout"
          title="Rechercher partout · ⌘ K / Ctrl K"
          className="op-search-trigger"
          onClick={() => setOpen(true)}
        >
          <Search size={15} />
          <span>Rechercher partout</span>
          <kbd>{shortcut} K</kbd>
        </button>
      )}
      <dialog
        className="op-search-dialog"
        ref={dialog}
        onCancel={(event) => {
          event.preventDefault();
          setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          }
        }}
        aria-label="Recherche globale"
      >
        <header>
          <h2>Rechercher dans l’espace</h2>
          <button className="icon" aria-label="Fermer la recherche" onClick={() => setOpen(false)}>
            <X size={19} />
          </button>
        </header>
        <div className="op-search-input">
          <Search size={18} />
          <input
            ref={input}
            autoFocus
            type="search"
            aria-label="Recherche globale"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Entreprise, contact, campagne, opportunité…"
            maxLength={120}
          />
        </div>
        <Feedback error={error} />
        <div className="op-search-results" aria-live="polite">
          {loading ? (
            <p>Recherche…</p>
          ) : query.trim().length < 2 ? (
            <p>Saisissez au moins deux caractères. Raccourci : {shortcut} K.</p>
          ) : data?.total === 0 ? (
            <p>Aucun résultat pour « {query} ».</p>
          ) : (
            data?.groups
              .filter((group: Row) => group.total)
              .map((group: Row) => (
                <section key={group.type}>
                  <h3>
                    {group.label}
                    <span>{group.total}</span>
                  </h3>
                  {group.items.map((row: Row) => (
                    <RouteLink
                      key={row.id}
                      url={row.url}
                      navigate={(url) => {
                        setOpen(false);
                        navigate(url);
                      }}
                    >
                      <span>
                        <strong>{row.title}</strong>
                        <small>{row.subtitle}</small>
                      </span>
                      <ArrowUpRight size={14} />
                    </RouteLink>
                  ))}
                  {group.total > 8 && (
                    <small>8 résultats les plus récents. Précisez la recherche pour affiner.</small>
                  )}
                </section>
              ))
          )}
        </div>
      </dialog>
    </>
  );
}
