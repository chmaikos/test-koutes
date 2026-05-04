import { NavLink, Outlet, useNavigate } from "react-router-dom";
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
} from "lucide-react";
import clsx from "clsx";
import { useMe } from "@/api/hooks";
import { useLiveStream } from "@/hooks/useLiveStream";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Activity;
  adminOnly?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/boxes", label: "Boxes", icon: BoxesIcon },
  { to: "/alerts", label: "Alerts", icon: AlertTriangle },
  { to: "/exports", label: "Exports", icon: Download },
  { to: "/settings", label: "Settings", icon: SettingsIcon, adminOnly: true },
];

export function AppShell() {
  const { instance } = useMsal();
  const navigate = useNavigate();
  const me = useMe();
  useLiveStream();

  const items = NAV_ITEMS.filter(
    (item) => !item.adminOnly || me.data?.role === "admin",
  );

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
          {items.map((item) => (
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
              {item.label}
            </NavLink>
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
        <div className="mx-auto max-w-7xl p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
