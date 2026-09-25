import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useMsal } from "@azure/msal-react";
import { clearLocalSession, getLocalToken } from "@/auth/local";
import {
  Activity,
  AlertTriangle,
  Boxes as BoxesIcon,
  ClipboardList,
  Download,
  Files,
  LayoutDashboard,
  Layers3,
  LogOut,
  Menu,
  Settings as SettingsIcon,
  X,
} from "lucide-react";
import clsx from "clsx";
import { useDashboard, useMe } from "@/api/hooks";
import { useLiveStream } from "@/hooks/useLiveStream";
import {
  BarcodeScannerDialog,
  ScanBarcodeButton,
} from "@/components/BarcodeScannerDialog";
import { NotificationCenter } from "@/components/NotificationCenter";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Activity;
  adminOnly?: boolean;
  badgeKey?: "alerts";
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/boxes", label: "Boxes", icon: BoxesIcon },
  { to: "/files", label: "Files", icon: Files },
  { to: "/lots", label: "Lots", icon: Layers3 },
  { to: "/pallets", label: "Pallets", icon: Layers3 },
  { to: "/requests", label: "Requests", icon: ClipboardList },
  { to: "/productivity", label: "Productivity", icon: Activity },
  { to: "/alerts", label: "Alerts", icon: AlertTriangle, badgeKey: "alerts" },
  { to: "/exports", label: "Exports", icon: Download },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

/**
 * Map the current pathname to a short title for the mobile top bar.
 *
 * The desktop sidebar makes "where am I" obvious via the active link;
 * on phones we only have the bottom-nav highlight, so a textual title
 * up top earns its keep. Detail routes (boxes/:id, alerts/:id) reuse
 * the parent label rather than trying to fetch the entity name.
 */
function titleForPath(pathname: string): string {
  if (pathname === "/" || pathname === "") return "Dashboard";
  if (pathname.startsWith("/boxes")) return "Boxes";
  if (pathname.startsWith("/files")) return "Physical Files";
  if (pathname.startsWith("/lots")) return "Lots";
  if (pathname.startsWith("/pallets")) return "Pallets";
  if (pathname.startsWith("/requests")) return "Requests";
  if (pathname.startsWith("/productivity")) return "Productivity";
  if (pathname.startsWith("/alerts")) return "Alerts";
  if (pathname.startsWith("/exports")) return "Exports";
  if (pathname.startsWith("/settings")) return "Settings";
  return "Box Tracker";
}

export function AppShell() {
  const me = useMe();
  const dashboard = useDashboard();
  useLiveStream();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [scannerOpen, setScannerOpen] = useState(false);
  const location = useLocation();

  // Auto-close the drawer on every navigation so an in-drawer NavLink
  // click closes it without a separate handler on every link.
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  const items = NAV_ITEMS.filter(
    (item) => !item.adminOnly || me.data?.role === "admin",
  );
  const openAlerts = dashboard.data?.total_open_alerts ?? 0;
  const title = titleForPath(location.pathname);

  return (
    <div className="flex h-full flex-col md:flex-row">
      <DesktopSidebar items={items} openAlerts={openAlerts} />

      <main className="flex flex-1 flex-col overflow-hidden">
        <MobileTopBar
          title={title}
          openAlerts={openAlerts}
          onOpenDrawer={() => setDrawerOpen(true)}
        />
        <div className="flex-1 overflow-y-auto bg-slate-50">
          <AlertsBanner count={openAlerts} />
          <div className="mx-auto max-w-7xl px-4 py-5 pb-24 md:p-6 md:pb-6">
            <div className="mb-4 flex justify-end">
              <ScanBarcodeButton onClick={() => setScannerOpen(true)} />
            </div>
            <Outlet />
          </div>
        </div>
        <MobileBottomNav items={items} openAlerts={openAlerts} />
      </main>

      <MobileDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        items={items}
        openAlerts={openAlerts}
      />
      {scannerOpen && (
        <BarcodeScannerDialog onClose={() => setScannerOpen(false)} />
      )}
    </div>
  );
}

function DesktopSidebar({
  items,
  openAlerts,
}: {
  items: NavItem[];
  openAlerts: number;
}) {
  const me = useMe();
  const { instance } = useMsal();
  const navigate = useNavigate();

  return (
    <aside className="hidden w-64 flex-col border-r border-slate-200 bg-white md:flex">
      <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-600 text-white">
          <BoxesIcon className="h-4 w-4" />
        </div>
        <div className="flex-1 font-semibold tracking-tight">Box Tracker</div>
        <NotificationCenter desktopAlign="left" />
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {items.map((item) => (
          <SidebarLink
            key={item.to}
            item={item}
            badge={item.badgeKey === "alerts" ? openAlerts : 0}
          />
        ))}
      </nav>
      <div className="border-t border-slate-200 p-3">
        <div className="mb-2 px-3 text-xs uppercase tracking-wider text-slate-400">
          Signed in
        </div>
        <div className="px-3 text-sm font-medium">
          {me.data?.display_name ?? me.data?.email ?? "..."}
        </div>
        <div className="px-3 text-xs capitalize text-slate-500">
          {me.data?.role.replaceAll("_", " ") ?? ""}
        </div>
        <button
          type="button"
          className="btn-ghost mt-2 w-full justify-start"
          onClick={() => {
            if (getLocalToken()) {
              clearLocalSession();
              navigate("/", { replace: true });
              return;
            }
            instance.logoutRedirect();
            navigate("/");
          }}
        >
          <LogOut className="h-4 w-4" /> Sign out
        </button>
      </div>
    </aside>
  );
}

function SidebarLink({ item, badge }: { item: NavItem; badge: number }) {
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      className={({ isActive }) =>
        clsx(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
          isActive
            ? "bg-brand-50 text-brand-700"
            : "text-slate-600 hover:bg-slate-50",
        )
      }
    >
      <item.icon className="h-4 w-4" />
      <span className="flex-1">{item.label}</span>
      {badge > 0 && <CountPill count={badge} />}
    </NavLink>
  );
}

function CountPill({ count }: { count: number }) {
  return (
    <span
      className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-rose-500 px-1.5 text-[11px] font-semibold leading-none text-white"
      aria-label={`${count} open alert${count === 1 ? "" : "s"}`}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

/**
 * Top bar visible only on mobile. Pairs the hamburger (which reveals
 * the identity + sign-out drawer) with a route-derived title.
 */
function MobileTopBar({
  title,
  openAlerts,
  onOpenDrawer,
}: {
  title: string;
  openAlerts: number;
  onOpenDrawer: () => void;
}) {
  return (
    <div className="flex h-14 items-center gap-2 border-b border-slate-200 bg-white px-3 md:hidden">
      <button
        type="button"
        className="-ml-1 inline-flex h-10 w-10 items-center justify-center rounded-md text-slate-700 hover:bg-slate-100"
        aria-label="Open menu"
        onClick={onOpenDrawer}
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-600 text-white">
        <BoxesIcon className="h-4 w-4" />
      </div>
      <div className="flex-1 truncate text-base font-semibold tracking-tight">
        {title}
      </div>
      <NotificationCenter />
      {openAlerts > 0 && (
        <Link
          to="/alerts"
          className="inline-flex h-10 items-center gap-1.5 rounded-full bg-rose-500 px-3 text-xs font-semibold text-white"
          aria-label={`${openAlerts} open alerts`}
        >
          <AlertTriangle className="h-3.5 w-3.5" />
          {openAlerts > 99 ? "99+" : openAlerts}
        </Link>
      )}
    </div>
  );
}

/**
 * Slide-in drawer. Hosts the same NavLinks as the desktop sidebar plus
 * the identity card and sign-out button -- mobile users still need a
 * way out, but bottom nav has no room for a "Sign out" tab.
 */
function MobileDrawer({
  open,
  onClose,
  items,
  openAlerts,
}: {
  open: boolean;
  onClose: () => void;
  items: NavItem[];
  openAlerts: number;
}) {
  const me = useMe();
  const { instance } = useMsal();
  const navigate = useNavigate();

  return (
    <div
      className={clsx(
        "fixed inset-0 z-40 md:hidden",
        open ? "pointer-events-auto" : "pointer-events-none",
      )}
      aria-hidden={!open}
    >
      <div
        className={clsx(
          "absolute inset-0 bg-slate-900/40 transition-opacity",
          open ? "opacity-100" : "opacity-0",
        )}
        onClick={onClose}
      />
      <aside
        className={clsx(
          "absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col bg-white shadow-xl transition-transform",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Main menu"
      >
        <div className="flex h-14 items-center justify-between gap-2 border-b border-slate-200 px-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-600 text-white">
              <BoxesIcon className="h-4 w-4" />
            </div>
            <div className="font-semibold tracking-tight">Box Tracker</div>
          </div>
          <button
            type="button"
            className="-mr-1 inline-flex h-10 w-10 items-center justify-center rounded-md text-slate-700 hover:bg-slate-100"
            aria-label="Close menu"
            onClick={onClose}
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {items.map((item) => (
            <SidebarLink
              key={item.to}
              item={item}
              badge={item.badgeKey === "alerts" ? openAlerts : 0}
            />
          ))}
        </nav>
        <div className="border-t border-slate-200 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <div className="mb-2 px-3 text-xs uppercase tracking-wider text-slate-400">
            Signed in
          </div>
          <div className="px-3 text-sm font-medium">
            {me.data?.display_name ?? me.data?.email ?? "..."}
          </div>
          <div className="px-3 text-xs capitalize text-slate-500">
            {me.data?.role.replaceAll("_", " ") ?? ""}
          </div>
          <button
            type="button"
            className="btn-ghost mt-2 min-h-[44px] w-full justify-start"
            onClick={() => {
              if (getLocalToken()) {
                clearLocalSession();
                navigate("/", { replace: true });
                onClose();
                return;
              }
              instance.logoutRedirect();
              navigate("/");
            }}
          >
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      </aside>
    </div>
  );
}

/**
 * Bottom navigation tab bar: thumb-reachable on phones, mirrors the
 * desktop sidebar's top-level entries. The Settings entry is filtered
 * out for non-admins by the parent (it's adminOnly), so the bar is
 * either four or five tabs wide.
 */
function MobileBottomNav({
  items,
  openAlerts,
}: {
  items: NavItem[];
  openAlerts: number;
}) {
  return (
    <nav
      className="border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] md:hidden"
      aria-label="Primary"
    >
      <div className="flex overflow-x-auto">
        {items.map((item) => {
          const showBadge = item.badgeKey === "alerts" && openAlerts > 0;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                clsx(
                  "relative flex min-w-[4.5rem] flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] font-medium",
                  isActive ? "text-brand-700" : "text-slate-500",
                )
              }
            >
              <item.icon className="h-5 w-5" />
              <span>{item.label}</span>
              {showBadge && (
                <span
                  className="absolute right-3 top-1 inline-flex min-w-[1.1rem] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold leading-none text-white"
                  aria-hidden="true"
                >
                  {openAlerts > 99 ? "99+" : openAlerts}
                </span>
              )}
            </NavLink>
          );
        })}
      </div>
    </nav>
  );
}

/**
 * Top-of-page banner that surfaces open alerts even when the user is
 * deep inside an unrelated screen. Dismissed banners are remembered for
 * the current count -- a new alert that bumps the count back up will
 * resurface it without the user having to refresh.
 */
function AlertsBanner({ count }: { count: number }) {
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);

  useEffect(() => {
    if (dismissedAt !== null && count <= dismissedAt) {
      return;
    }
    if (dismissedAt !== null && count > dismissedAt) {
      setDismissedAt(null);
    }
  }, [count, dismissedAt]);

  if (count <= 0) return null;
  if (dismissedAt !== null && count <= dismissedAt) return null;

  return (
    <div className="border-b border-rose-200 bg-rose-50 px-4 py-2.5 md:px-6">
      <div className="mx-auto flex max-w-7xl items-center gap-3 text-sm text-rose-800">
        <AlertTriangle className="h-4 w-4 flex-none" />
        <span className="flex-1">
          {count} open alert{count === 1 ? "" : "s"}.{" "}
          <Link to="/alerts" className="font-medium underline hover:text-rose-900">
            Review now
          </Link>
        </span>
        <button
          type="button"
          aria-label="Dismiss alerts banner"
          className="rounded-full p-1 text-rose-700 hover:bg-rose-100"
          onClick={() => setDismissedAt(count)}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
