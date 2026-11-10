import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react';
import {
  BarChart3,
  Bell,
  BookOpen,
  Bot,
  BriefcaseBusiness,
  Building2,
  CalendarDays,
  ChevronDown,
  ChevronsUpDown,
  ClipboardCheck,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LockKeyhole,
  LogOut,
  Mail,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  ShieldCheck,
  Star,
  Target,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react';
import {GlobalSearch} from './GlobalSearch';
import type {Api, Navigate, Row} from './WorkspaceUI';
import './workspace-sidebar.css';

type NavItem = {id: string; label: string; url: string; icon: LucideIcon};
const groups: {id: string; label: string; items: NavItem[]}[] = [
  {
    id: 'workspace',
    label: 'Espace de travail',
    items: [
      {id: 'contacts', label: 'Prospects', url: '/contacts', icon: Users},
      {id: 'companies', label: 'Entreprises', url: '/companies', icon: Building2},
      {id: 'tasks', label: 'À faire', url: '/tasks', icon: ListChecks},
      {id: 'campaigns', label: 'Campagnes', url: '/campaigns', icon: Target},
    ],
  },
  {
    id: 'activity',
    label: 'Pilotage',
    items: [
      {id: 'inbox', label: 'Réponses', url: '/inbox', icon: Inbox},
      {id: 'calendar', label: 'Calendrier', url: '/calendar', icon: CalendarDays},
      {id: 'pipeline', label: 'Opportunités', url: '/?view=pipeline', icon: BriefcaseBusiness},
      {id: 'analytics', label: 'Résultats', url: '/analytics', icon: BarChart3},
      {id: 'agent', label: 'Agent Lumelia', url: '/agent?view=human', icon: Bot},
    ],
  },
  {
    id: 'tools',
    label: 'Outils & ressources',
    items: [
      {id: 'qualification', label: 'Qualification', url: '/qualification', icon: ClipboardCheck},
      {id: 'quality', label: 'Qualité des données', url: '/quality', icon: ShieldCheck},
      {id: 'dashboard', label: 'Vue d’ensemble', url: '/?view=dashboard', icon: LayoutDashboard},
      {id: 'alerts', label: 'Alertes', url: '/alerts', icon: Bell},
      {id: 'senders', label: 'Boîtes email', url: '/?view=senders', icon: Mail},
      {id: 'offers', label: 'Offres', url: '/?view=offers', icon: BookOpen},
    ],
  },
];
const allItems = groups.flatMap((group) => group.items);
export function sidebarPageLabel(view: string, entityView: string) {
  const id = view === 'contacts' ? entityView : view;
  return (
    allItems.find((item) => item.id === id)?.label ||
    {
      settings: 'Réglages',
      contact: 'Fiche contact',
      company: 'Fiche entreprise',
      campaign: 'Campagne',
      sequence: 'Séquence',
    }[id] ||
    'Prospection'
  );
}
type Preferences = {collapsed: boolean; closedGroups: string[]; favorites: string[]};
const defaults: Preferences = {collapsed: false, closedGroups: ['tools'], favorites: []};

function readPreferences(key: string): Preferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    if (!value || typeof value !== 'object') return defaults;
    return {
      collapsed: value.collapsed === true,
      closedGroups: Array.isArray(value.closedGroups)
        ? groups.filter((group) => value.closedGroups.includes(group.id)).map((group) => group.id)
        : defaults.closedGroups,
      favorites: Array.isArray(value.favorites)
        ? [
            ...new Set<string>(
              value.favorites.filter((id: unknown) => allItems.some((item) => item.id === id)),
            ),
          ]
        : [],
    };
  } catch {
    return defaults;
  }
}

const SidebarContext = createContext<{
  mobile: boolean;
  open: boolean;
  collapsed: boolean;
  toggle: () => void;
} | null>(null);

/** Kept in the topbar so navigation is reachable even when the mobile drawer is closed. */
export function SidebarToggle() {
  const sidebar = useContext(SidebarContext);
  if (!sidebar) return null;
  const label = sidebar.mobile
    ? 'Ouvrir la navigation'
    : sidebar.collapsed
      ? 'Déplier la navigation'
      : 'Replier la navigation';
  return (
    <button
      type="button"
      className="sb-toggle"
      aria-label={label}
      title={label + ' · ⌘ B / Ctrl B'}
      aria-controls="workspace-navigation"
      aria-expanded={sidebar.mobile ? sidebar.open : !sidebar.collapsed}
      onClick={sidebar.toggle}
    >
      {sidebar.mobile ? (
        <Menu size={19} />
      ) : sidebar.collapsed ? (
        <PanelLeftOpen size={18} />
      ) : (
        <PanelLeftClose size={18} />
      )}
    </button>
  );
}

type Props = {
  children: ReactNode;
  user: Row;
  view: string;
  entityView: string;
  url: string;
  navigate: Navigate;
  api: Api;
  pendingTasks?: number;
  onLogout: () => void;
  busy: boolean;
};

export function WorkspaceShell({
  children,
  user,
  view,
  entityView,
  url,
  navigate,
  api,
  pendingTasks,
  onLogout,
  busy,
}: Props) {
  const storageKey = 'lumelia-sidebar-v1:' + user.id;
  const activeId =
    view === 'contacts'
      ? entityView
      : {contact: 'contacts', company: 'companies', campaign: 'campaigns', sequence: 'campaigns'}[view] ||
        view;
  const [preferences, setPreferences] = useState(() => {
    const saved = readPreferences(storageKey);
    const activeGroup = groups.find((group) => group.items.some((item) => item.id === activeId));
    return {...saved, closedGroups: saved.closedGroups.filter((id) => id !== activeGroup?.id)};
  });
  const [mobile, setMobile] = useState(() => matchMedia('(max-width: 760px)').matches);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchRequest, setSearchRequest] = useState(0);
  const [profileOpen, setProfileOpen] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const drawer = useRef<HTMLDialogElement>(null);
  const profile = useRef<HTMLDivElement>(null);
  const profileTrigger = useRef<HTMLButtonElement>(null);
  const profileFirstAction = useRef<HTMLAnchorElement>(null);
  const previousUrl = useRef(url);
  const collapsed = !mobile && preferences.collapsed;
  const shortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(preferences));
    } catch {
      /* Preferences remain usable without browser storage. */
    }
  }, [preferences, storageKey]);

  useEffect(() => {
    const media = matchMedia('(max-width: 760px)');
    const update = () => {
      setMobile(media.matches);
      setMobileOpen(false);
      setProfileOpen(false);
    };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    const dialog = drawer.current;
    if (!mobile || !mobileOpen || !dialog) return;
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
    };
  }, [mobile, mobileOpen]);

  useEffect(() => {
    if (previousUrl.current === url) return;
    previousUrl.current = url;
    setMobileOpen(false);
    setProfileOpen(false);
    const group = groups.find((group) => group.items.some((item) => item.id === activeId));
    if (group)
      setPreferences((previous) =>
        previous.closedGroups.includes(group.id)
          ? {...previous, closedGroups: previous.closedGroups.filter((id) => id !== group.id)}
          : previous,
      );
  }, [url, activeId]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.defaultPrevented ||
        event.repeat ||
        target?.closest('input, textarea, select, [contenteditable="true"]') ||
        document.querySelector('dialog[open]:not(.sb-drawer)')
      )
        return;
      if (
        (event.metaKey || event.ctrlKey) &&
        !event.altKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === 'b'
      ) {
        event.preventDefault();
        setProfileOpen(false);
        if (mobile) setMobileOpen((value) => !value);
        else setPreferences((previous) => ({...previous, collapsed: !previous.collapsed}));
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [mobile]);

  useEffect(() => {
    if (!profileOpen) return;
    profileFirstAction.current?.focus();
    const outside = (event: PointerEvent) => {
      if (!profile.current?.contains(event.target as Node)) setProfileOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || document.querySelector('dialog[open]:not(.sb-drawer)')) return;
      event.preventDefault();
      event.stopPropagation();
      setProfileOpen(false);
      profileTrigger.current?.focus();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape, true);
    };
  }, [profileOpen]);

  function toggle() {
    setProfileOpen(false);
    if (mobile) setMobileOpen((value) => !value);
    else setPreferences((previous) => ({...previous, collapsed: !previous.collapsed}));
  }
  function keepDrawerFocus(event: ReactKeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return;
    const targets = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('a[href], button:not([disabled])'),
    ).filter((element) => element.getClientRects().length > 0);
    const first = targets[0],
      last = targets[targets.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
  function follow(event: MouseEvent<HTMLAnchorElement>, destination: string) {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    setMobileOpen(false);
    setProfileOpen(false);
    navigate(destination);
  }
  function favorite(item: NavItem) {
    const exists = preferences.favorites.includes(item.id);
    setPreferences((previous) => ({
      ...previous,
      favorites: exists
        ? previous.favorites.filter((id) => id !== item.id)
        : [...previous.favorites, item.id],
    }));
    setAnnouncement(item.label + (exists ? ' retiré des favoris.' : ' ajouté aux favoris.'));
  }
  function navItem(item: NavItem, inFavorites = false) {
    const active = activeId === item.id;
    const pinned = preferences.favorites.includes(item.id);
    const count =
      item.id === 'tasks' && typeof pendingTasks === 'number' && pendingTasks > 0 ? pendingTasks : null;
    return (
      <li key={item.id} className={'sb-item' + (active ? ' is-active' : '')}>
        <a
          className="sb-link"
          href={item.url}
          aria-current={active ? 'page' : undefined}
          aria-label={item.label + (count ? ' · ' + count + ' actions à faire' : '')}
          title={collapsed ? item.label + (count ? ' · ' + count : '') : undefined}
          onClick={(event) => follow(event, item.url)}
        >
          <item.icon size={18} strokeWidth={1.7} aria-hidden="true" />
          <span className="sb-label">{item.label}</span>
          {count !== null && (
            <span className="sb-count" aria-hidden="true">
              {count > 99 ? '99+' : count}
            </span>
          )}
        </a>
        {!collapsed && (
          <button
            type="button"
            className={'sb-pin' + (pinned ? ' is-pinned' : '')}
            aria-pressed={pinned}
            aria-label={
              (pinned ? 'Retirer ' : 'Épingler ') + item.label + (pinned ? ' des favoris' : ' aux favoris')
            }
            title={pinned ? 'Retirer des favoris' : 'Épingler aux favoris'}
            onClick={() => {
              // A removed favorite must not leave keyboard focus on a detached button.
              if (inFavorites) {
                const original = document.getElementById(
                  'sb-group-' + groups.find((group) => group.items.some((entry) => entry.id === item.id))?.id,
                );
                const fallback = document.querySelector<HTMLButtonElement>('.sb-search');
                (original?.querySelector<HTMLButtonElement>('button') || fallback)?.focus();
              }
              favorite(item);
            }}
          >
            <Star size={13} aria-hidden="true" />
          </button>
        )}
      </li>
    );
  }

  const name = String(user.name || 'Lumelia');
  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase();
  const sidebar = (
    <>
      <div className="sb-heading">
        <a
          className="sb-brand"
          href="/contacts"
          aria-label="Lumelia · Prospects"
          onClick={(event) => follow(event, '/contacts')}
        >
          <span className="sb-monogram" aria-hidden="true">
            l<span>•</span>
          </span>
          <span className="sb-wordmark">
            lumelia<span>•</span>
          </span>
        </a>
        {mobile ? (
          <button
            type="button"
            className="sb-icon-button"
            aria-label="Fermer la navigation"
            onClick={() => setMobileOpen(false)}
          >
            <X size={19} />
          </button>
        ) : (
          <button
            type="button"
            className="sb-icon-button sb-heading-toggle"
            aria-label={collapsed ? 'Déplier la barre latérale' : 'Replier la barre latérale'}
            title={(collapsed ? 'Déplier' : 'Replier') + ' · ' + shortcut + ' B'}
            aria-expanded={!collapsed}
            aria-controls="workspace-navigation"
            onClick={toggle}
          >
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
        )}
      </div>
      <div className="sb-workspace">
        <span>Prospection</span>
        <span className="sb-private">
          <LockKeyhole size={10} /> Privé
        </span>
      </div>
      <button
        type="button"
        className="sb-search"
        aria-label="Rechercher dans l’espace"
        title={'Rechercher · ' + shortcut + ' K'}
        onClick={() => setSearchRequest((value) => value + 1)}
      >
        <Search size={17} />
        <span className="sb-label">Rechercher…</span>
        <kbd>{shortcut} K</kbd>
      </button>
      <nav className="sb-navigation" id="workspace-navigation" aria-label="Navigation principale">
        {!collapsed && (
          <section className="sb-favorites" aria-label="Favoris">
            <div className="sb-section-label">
              <Star size={12} />
              <span>Favoris</span>
              {preferences.favorites.length > 0 && (
                <span className="sb-favorite-total">{preferences.favorites.length}</span>
              )}
            </div>
            {preferences.favorites.length ? (
              <ul className="sb-list">
                {preferences.favorites.map((id) =>
                  navItem(
                    allItems.find((item) => item.id === id)!,
                    true,
                  ),
                )}
              </ul>
            ) : (
              <p className="sb-favorites-empty">Épinglez vos pages avec leur étoile.</p>
            )}
          </section>
        )}
        {groups.map((group) => {
          const expanded = collapsed || !preferences.closedGroups.includes(group.id);
          return (
            <section key={group.id} className="sb-group" id={'sb-group-' + group.id} aria-label={group.label}>
              <button
                type="button"
                className="sb-group-toggle"
                aria-expanded={expanded}
                aria-controls={'sb-links-' + group.id}
                onClick={() =>
                  setPreferences((previous) => ({
                    ...previous,
                    closedGroups: expanded
                      ? [...previous.closedGroups, group.id]
                      : previous.closedGroups.filter((id) => id !== group.id),
                  }))
                }
              >
                <span>{group.label}</span>
                <ChevronDown size={13} className={expanded ? '' : 'is-closed'} />
                {!expanded && group.items.some((item) => item.id === activeId) && (
                  <span className="sb-group-active" aria-label="Contient la page actuelle" />
                )}
              </button>
              <ul id={'sb-links-' + group.id} className="sb-list" hidden={!expanded}>
                {group.items.map((item) => navItem(item))}
              </ul>
            </section>
          );
        })}
      </nav>
      <div className="sb-footer">
        <a
          className={'sb-settings sb-link' + (activeId === 'settings' ? ' is-active' : '')}
          href="/settings"
          aria-current={activeId === 'settings' ? 'page' : undefined}
          aria-label="Réglages"
          title={collapsed ? 'Réglages' : undefined}
          onClick={(event) => follow(event, '/settings')}
        >
          <Settings size={18} strokeWidth={1.7} />
          <span className="sb-label">Réglages</span>
        </a>
        <div
          className="sb-profile"
          ref={profile}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node)) setProfileOpen(false);
          }}
        >
          <button
            type="button"
            ref={profileTrigger}
            className="sb-profile-trigger"
            aria-label={'Compte de ' + name}
            title={collapsed ? name : undefined}
            aria-expanded={profileOpen}
            aria-controls="sb-account-panel"
            onClick={() => setProfileOpen((value) => !value)}
          >
            <span className="sb-avatar" aria-hidden="true">
              {initials}
            </span>
            <span className="sb-profile-copy">
              <strong>{name}</strong>
              <small>Espace Lumelia</small>
            </span>
            <ChevronsUpDown size={14} />
          </button>
          {profileOpen && (
            <div className="sb-account-panel" id="sb-account-panel" role="group" aria-label="Mon compte">
              <div className="sb-account-heading">
                <strong>{name}</strong>
                <small>{user.role === 'admin' ? 'Administrateur' : 'Membre'} · Espace privé</small>
              </div>
              <a ref={profileFirstAction} href="/settings" onClick={(event) => follow(event, '/settings')}>
                <Settings size={16} />
                Réglages de l’espace
              </a>
              <button type="button" disabled={busy} onClick={onLogout}>
                <LogOut size={16} />
                Se déconnecter
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );

  return (
    <SidebarContext.Provider value={{mobile, open: mobileOpen, collapsed, toggle}}>
      <div className={'shell workspace-shell' + (collapsed ? ' sidebar-collapsed' : '')}>
        <a className="sb-skip" href="#workspace-main">
          Aller au contenu
        </a>
        {mobile ? (
          <dialog
            ref={drawer}
            className="sb-drawer"
            onKeyDown={keepDrawerFocus}
            aria-label="Navigation Lumelia"
            onCancel={() => setMobileOpen(false)}
            onClick={(event) => {
              if (event.target === event.currentTarget) {
                const bounds = event.currentTarget.getBoundingClientRect();
                if (
                  event.clientX < bounds.left ||
                  event.clientX > bounds.right ||
                  event.clientY < bounds.top ||
                  event.clientY > bounds.bottom
                )
                  setMobileOpen(false);
              }
            }}
          >
            <div className="workspace-sidebar">{sidebar}</div>
          </dialog>
        ) : (
          <aside className="workspace-sidebar" aria-label="Navigation Lumelia">
            {sidebar}
          </aside>
        )}
        {children}
        <GlobalSearch
          api={api}
          navigate={(destination) => {
            setMobileOpen(false);
            navigate(destination);
          }}
          request={searchRequest}
          hideTrigger
        />
        <span className="sb-sr-only" role="status">
          {announcement}
        </span>
      </div>
    </SidebarContext.Provider>
  );
}
