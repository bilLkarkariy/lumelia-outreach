import {useEffect, useRef, useState} from 'react';

const views = new Set([
  'dashboard',
  'contacts',
  'offers',
  'campaigns',
  'tasks',
  'pipeline',
  'senders',
  'settings',
  'qualification',
  'inbox',
  'calendar',
  'quality',
  'analytics',
  'alerts',
  'agent',
]);
export const contactTabs = ['overview', 'qualification', 'emails', 'history'] as const;
export type ContactTab = (typeof contactTabs)[number];
type Route = {
  view: string;
  contactId: string;
  accountId: string;
  groupId: string;
  sequenceId: string;
  inboxId: string;
  entityView: string;
  section: string;
  tab: ContactTab;
  url: string;
};
type Pending = {url: string; delta?: number};

export function contactUrl(id: string, tab: ContactTab = 'overview') {
  return '/contacts/' + encodeURIComponent(id) + (tab === 'overview' ? '' : '?tab=' + tab);
}

function readRoute(): Route {
  const url = new URL(window.location.href);
  const contact = url.pathname.match(/^\/contacts\/([a-zA-Z0-9-]+)\/?$/);
  const tab = url.searchParams.get('tab');
  const account = url.pathname.match(/^\/companies\/([a-zA-Z0-9-]+)\/?$/);
  const group = url.pathname.match(/^\/campaigns\/([a-zA-Z0-9-]+)\/?$/);
  const sequence = url.pathname.match(/^\/sequences\/([a-zA-Z0-9-]+)\/?$/);
  const inbox = url.pathname.match(/^\/inbox\/([a-zA-Z0-9-]+)\/?$/);
  const path = url.pathname.replace(/\/$/, '');
  return {
    view: contact
      ? 'contact'
      : account
        ? 'company'
        : group
          ? 'campaign'
          : sequence
            ? 'sequence'
            : inbox || path === '/inbox'
              ? 'inbox'
              : [
                    '/calendar',
                    '/quality',
                    '/analytics',
                    '/alerts',
                    '/agent',
                    '/tasks',
                    '/campaigns',
                    '/settings',
                  ].includes(path)
                ? path.slice(1)
                : path === '/qualification'
                  ? 'qualification'
                  : ['/contacts', '/companies'].includes(path)
                    ? 'contacts'
                    : views.has(url.searchParams.get('view') || '')
                      ? url.searchParams.get('view')!
                      : 'contacts',
    contactId: contact?.[1] || '',
    accountId: account?.[1] || '',
    groupId: group?.[1] || '',
    sequenceId: sequence?.[1] || '',
    inboxId: inbox?.[1] || '',
    entityView: path === '/companies' ? 'companies' : 'contacts',
    section: tab || 'overview',
    tab: contactTabs.includes(tab as ContactTab) ? (tab as ContactTab) : 'overview',
    url: url.pathname + url.search,
  };
}

/** A small history adapter keeps contact links refreshable and protects an open edit. */
export function useWorkspaceNavigation() {
  const [route, setRoute] = useState(readRoute);
  const [pending, setPending] = useState<Pending | null>(null);
  const [discardVersion, setDiscardVersion] = useState(0);
  const dirty = useRef(false);
  const currentIndex = useRef(Number(window.history.state?.lumeliaIndex || 0));
  const restoring = useRef(false);

  useEffect(() => {
    window.history.replaceState(
      {...window.history.state, lumeliaIndex: currentIndex.current},
      '',
      window.location.href,
    );
    const pop = (event: PopStateEvent) => {
      if (restoring.current) {
        restoring.current = false;
        return;
      }
      const destinationIndex = Number(event.state?.lumeliaIndex || 0);
      const delta = destinationIndex - currentIndex.current;
      if (dirty.current && delta) {
        setPending({url: readRoute().url, delta});
        restoring.current = true;
        window.history.go(-delta);
        return;
      }
      currentIndex.current = destinationIndex;
      setRoute(readRoute());
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('popstate', pop);
    window.addEventListener('beforeunload', unload);
    return () => {
      window.removeEventListener('popstate', pop);
      window.removeEventListener('beforeunload', unload);
    };
  }, []);

  function commit(url: string) {
    currentIndex.current += 1;
    window.history.pushState({lumeliaIndex: currentIndex.current}, '', url);
    setRoute(readRoute());
  }
  function navigate(url: string) {
    if (url === readRoute().url) return;
    if (dirty.current) setPending({url});
    else commit(url);
  }
  function discardAndNavigate() {
    if (!pending) return;
    dirty.current = false;
    setDiscardVersion((value) => value + 1);
    if (pending.delta) window.history.go(pending.delta);
    else commit(pending.url);
    setPending(null);
  }
  return {
    ...route,
    navigate,
    pending,
    discardVersion,
    discardAndNavigate,
    keepEditing: () => setPending(null),
    setDirty: (value: boolean) => {
      dirty.current = value;
    },
    setView: (view: string) =>
      navigate(
        view === 'contacts'
          ? '/contacts'
          : view === 'dashboard'
            ? '/?view=dashboard'
            : view === 'qualification'
              ? '/qualification'
              : [
                    'inbox',
                    'calendar',
                    'quality',
                    'analytics',
                    'alerts',
                    'agent',
                    'tasks',
                    'campaigns',
                    'settings',
                  ].includes(view)
                ? '/' + view
                : '/?view=' + view,
      ),
  };
}
