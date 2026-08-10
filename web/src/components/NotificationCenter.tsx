import { useState } from "react";
import { Bell, CheckCheck } from "lucide-react";
import { Link } from "react-router-dom";
import {
  useMarkNotificationsRead,
  useNotifications,
} from "@/api/hooks";

export function NotificationCenter({
  desktopAlign = "right",
}: {
  desktopAlign?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const notifications = useNotifications(1, 15);
  const markRead = useMarkNotificationsRead();
  const unread = notifications.data?.unread ?? 0;

  return (
    <div className="relative">
      <button
        type="button"
        className="relative inline-flex h-10 w-10 items-center justify-center rounded-full text-slate-600 hover:bg-slate-100"
        aria-label={`${unread} unread notification${unread === 1 ? "" : "s"}`}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Bell className="h-5 w-5" />
        {unread > 0 && (
          <span className="absolute right-0.5 top-0.5 inline-flex min-w-[1.1rem] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold leading-[1.1rem] text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <button
            type="button"
            className="fixed inset-0 z-30 cursor-default"
            aria-label="Close notifications"
            onClick={() => setOpen(false)}
          />
          <section
            className={`fixed inset-x-3 top-16 z-40 max-h-[70vh] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl sm:absolute sm:inset-auto sm:top-12 sm:w-96 ${
              desktopAlign === "left" ? "sm:left-0" : "sm:right-0"
            }`}
            aria-label="Notifications"
          >
            <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <div>
                <h2 className="font-semibold">Notifications</h2>
                <p className="text-xs text-slate-500">{unread} unread</p>
              </div>
              <button
                type="button"
                className="btn-ghost text-xs"
                disabled={unread === 0 || markRead.isPending}
                onClick={() => markRead.mutate(undefined)}
              >
                <CheckCheck className="h-4 w-4" /> Mark all read
              </button>
            </header>
            <div className="max-h-[58vh] divide-y divide-slate-100 overflow-y-auto">
              {notifications.isLoading && (
                <p className="p-5 text-sm text-slate-500">Loading…</p>
              )}
              {!notifications.isLoading &&
                notifications.data?.items.length === 0 && (
                  <p className="p-5 text-sm text-slate-500">
                    You have no notifications.
                  </p>
                )}
              {notifications.data?.items.map((notification) => (
                <Link
                  key={notification.id}
                  to={notification.deep_link}
                  className={`block px-4 py-3 hover:bg-slate-50 ${
                    notification.read_at ? "" : "bg-brand-50/60"
                  }`}
                  onClick={() => {
                    setOpen(false);
                    if (!notification.read_at) {
                      markRead.mutate([notification.id]);
                    }
                  }}
                >
                  <div className="flex items-start gap-2">
                    {!notification.read_at && (
                      <span className="mt-1.5 h-2 w-2 flex-none rounded-full bg-brand-600" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-slate-800">
                        {notification.title}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-xs text-slate-600">
                        {notification.body}
                      </p>
                      <time className="mt-1 block text-[11px] text-slate-400">
                        {new Date(notification.created_at).toLocaleString()}
                      </time>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
