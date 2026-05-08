import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { AlertTriangle, Boxes, KeyRound, Lock, LogIn } from "lucide-react";
import clsx from "clsx";
import {
  clearCachedAccounts,
  getSsoError,
  loginRequest,
  setSsoError,
  ssoAvailable,
  subscribeSsoError,
  type SsoErrorDetails,
} from "@/auth/msal";
import {
  getLocalToken,
  localLogin,
  subscribeLocalAuth,
} from "@/auth/local";

type Tab = "sso" | "local";

export function AuthGate({ children }: { children: ReactNode }) {
  const { instance, inProgress } = useMsal();
  const ssoAuthed = useIsAuthenticated();
  const [hasLocal, setHasLocal] = useState<boolean>(getLocalToken() !== null);
  const ssoOn = useMemo(() => ssoAvailable(), []);
  const [tab, setTab] = useState<Tab>(ssoOn ? "sso" : "local");
  const [ssoError, setSsoErrorState] = useState<SsoErrorDetails | null>(
    getSsoError(),
  );

  useEffect(() => {
    return subscribeLocalAuth(() => setHasLocal(getLocalToken() !== null));
  }, []);

  useEffect(() => {
    return subscribeSsoError((err) => setSsoErrorState(err));
  }, []);

  // Only show the "Authenticating..." spinner if MSAL is genuinely doing work.
  // When SSO is unavailable (non-secure context) MsalProvider can sit at
  // inProgress="startup" forever — we'd hang on the spinner instead of
  // showing the local-admin form.
  if (ssoOn && inProgress !== "none" && !ssoAuthed && !hasLocal) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        Authenticating...
      </div>
    );
  }

  if (ssoAuthed || hasLocal) {
    return <>{children}</>;
  }

  return (
    <div className="flex h-full items-center justify-center px-4">
      <div className="card card-pad w-full max-w-md">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-brand-100 text-brand-600">
          <Boxes className="h-6 w-6" />
        </div>
        <h1 className="text-center text-xl font-semibold">
          Warehouse Box Tracker
        </h1>
        <p className="mt-1 text-center text-sm text-slate-500">
          Sign in to continue.
        </p>

        {ssoOn ? (
          <div className="mt-5 grid grid-cols-2 rounded-md bg-slate-100 p-1 text-sm">
            <button
              type="button"
              className={clsx(
                "rounded px-3 py-1.5 transition",
                tab === "sso"
                  ? "bg-white shadow-sm font-medium"
                  : "text-slate-500",
              )}
              onClick={() => setTab("sso")}
            >
              Microsoft 365
            </button>
            <button
              type="button"
              className={clsx(
                "rounded px-3 py-1.5 transition",
                tab === "local"
                  ? "bg-white shadow-sm font-medium"
                  : "text-slate-500",
              )}
              onClick={() => setTab("local")}
            >
              Local admin
            </button>
          </div>
        ) : (
          <div className="mt-5 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            <Lock className="mt-0.5 h-3.5 w-3.5 flex-none" />
            <span>
              Microsoft 365 sign-in is unavailable on this URL. Browsers only
              expose the crypto APIs MSAL needs over HTTPS or on{" "}
              <code>localhost</code>. Use local admin credentials below, or open
              the app over HTTPS.
            </span>
          </div>
        )}

        {ssoOn && tab === "sso" ? (
          <>
            {ssoError && <SsoErrorBanner err={ssoError} />}
            <button
              type="button"
              className="btn-primary mt-5 w-full"
              onClick={async () => {
                setSsoError(null);
                // Wipe any account MSAL has cached from a previous attempt
                // (e.g. an Exclaimer-style service mailbox) before we kick
                // off the redirect, otherwise MSAL silently appends
                // login_hint/X-AnchorMailbox for that stale account and
                // Microsoft renders the generic "We couldn't sign you in"
                // page with no AADSTS code.
                await clearCachedAccounts();
                instance
                  .loginRedirect({
                    ...loginRequest,
                    account: undefined,
                    loginHint: undefined,
                  })
                  .catch((err) => {
                    console.warn("[auth] loginRedirect failed", err);
                  });
              }}
            >
              <LogIn className="h-4 w-4" /> Sign in with Microsoft
            </button>
          </>
        ) : (
          <LocalLoginForm onSuccess={() => setHasLocal(true)} />
        )}

        {(!ssoOn || tab === "local") && (
          <p className="mt-4 flex items-start gap-2 text-xs text-slate-500">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none text-amber-500" />
            Local credentials are an emergency-access fallback. Prefer Microsoft 365 SSO whenever it's available.
          </p>
        )}
      </div>
    </div>
  );
}

function LocalLoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <form
      className="mt-5 space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        setSubmitting(true);
        try {
          await localLogin(username.trim(), password);
          onSuccess();
        } catch (err: unknown) {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response
              ?.data?.detail ?? "Sign-in failed";
          setError(detail);
        } finally {
          setSubmitting(false);
        }
      }}
    >
      <label className="block">
        <span className="text-xs text-slate-500">Username</span>
        <input
          required
          autoFocus
          autoComplete="username"
          className="input"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Password</span>
        <input
          required
          type="password"
          autoComplete="current-password"
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <p className="text-sm text-rose-600">{error}</p>}
      <button type="submit" className="btn-primary w-full" disabled={submitting}>
        <KeyRound className="h-4 w-4" />
        {submitting ? "Signing in..." : "Sign in"}
      </button>
    </form>
  );
}

function SsoErrorBanner({ err }: { err: SsoErrorDetails }) {
  // AADSTS90014 ("required field 'request' is missing") is what Microsoft
  // returns when the user presses the browser Back button after a sign-in
  // attempt - the back navigation re-submits the authorize URL without a
  // fresh PKCE state. It's never a real config bug, so we soften the
  // message and tell the user to just click Sign in again.
  const isBackButtonArtifact = err.code === "AADSTS90014";

  return (
    <div
      role="alert"
      className="mt-5 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none text-rose-500" />
        <div className="space-y-1">
          <p className="font-semibold">
            {isBackButtonArtifact
              ? "Sign-in interrupted"
              : "Microsoft sign-in failed"}
            {err.code ? <span className="font-mono"> ({err.code})</span> : null}
          </p>
          {isBackButtonArtifact ? (
            <p>
              This usually means you pressed the browser Back button during
              sign-in. Click <span className="font-semibold">Sign in with
              Microsoft</span> below to start a fresh attempt.
            </p>
          ) : (
            <p className="break-words">{err.message}</p>
          )}
          {err.correlationId && (
            <p className="text-rose-700">
              Correlation ID: <span className="font-mono">{err.correlationId}</span>
            </p>
          )}
          {!isBackButtonArtifact && (
            <p className="text-rose-700">
              Check the README "Troubleshooting SSO" table, or use the Local
              admin tab as a fallback.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
