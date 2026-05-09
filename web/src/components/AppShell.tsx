import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useMsal } from "@azure/msal-react";
import { clearLocalSession, getLocalToken } from "@/auth/local";
import {
  Activity,
  AlertTriangle,
  Boxes as BoxesIcon,
  Download,
  LayoutDashboard,
  LogOut,
  Settings as SettingsIcon,
  X,
} from "lucide-react";
import clsx from "clsx";
import { useDashboard, useMe } from "@/api/hooks";
import { useLiveStream } from "@/hooks/useLiveStream";

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
  { to: "/alerts", label: "Alerts", icon: AlertTriangle, badgeKey: "alerts" },
  { to: "/exports", label: "Exports", icon: Download },
  { to: "/settings", label: "Settings", icon: SettingsIcon, adminOnly: true },
];

export function AppShell() {
  const { instance } = useMsal();
  const navigate = useNavigate();
  const me = useMe();
  const dashboard = useDashboard();
  useLiveStream();

  const items = NAV_ITEMS.filter(
    (item) => !item.adminOnly || me.data?.role === "admin",
  );
  const openAlerts = dashboard.data?.total_open_alerts ?? 0;

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-600 text-white">
            <BoxesIcon className="h-4 w-4" />
          </div>
          <div className="font-semibold tracking-tight">Box Tracker</div>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {items.map((item) => {
            const showBadge = item.badgeKey === "alerts" && openAlerts > 0;
            return (
              <NavLink
                key={item.to}
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
                {showBadge && (
                  <span
                    className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-rose-500 px-1.5 text-[11px] font-semibold leading-none text-white"
                    aria-label={`${openAlerts} open alert${openAlerts === 1 ? "" : "s"}`}
                  >
                    {openAlerts > 99 ? "99+" : openAlerts}
                  </span>
                )}
              </NavLink>
            );
          })}
        </nav>
        <div className="border-t border-slate-200 p-3">
          <div className="mb-2 px-3 text-xs uppercase tracking-wider text-slate-400">
            Signed in
          </div>
          <div className="px-3 text-sm font-medium">
            {me.data?.display_name ?? me.data?.email ?? "..."}
          </div>
          <div className="px-3 text-xs capitalize text-slate-500">
            {me.data?.role ?? ""}
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

      <main className="flex-1 overflow-y-auto bg-slate-50">
        <AlertsBanner count={openAlerts} />
        <div className="mx-auto max-w-7xl p-6">
          <Outlet />
        </div>
      </main>
    </div>
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
    // When the count drops to zero or shrinks below the dismissed
    // threshold the banner is implicitly dismissed; reset so the next
    // increase shows it again.
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
    <div className="border-b border-rose-200 bg-rose-50 px-6 py-2.5">
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
