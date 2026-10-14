import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { MsalProvider } from "@azure/msal-react";
import {
  AuthError,
  EventType,
  type EventMessage,
} from "@azure/msal-browser";
import "./index.css";
import {
  disableSso,
  msalInstance,
  setSsoError,
  ssoAvailable,
} from "@/auth/msal";
import { AuthGate } from "@/auth/AuthGate";
import { AppShell } from "@/components/AppShell";
import { CredentialsGate } from "@/components/CredentialsGate";
import { purgeLegacyApiCaches } from "@/auth/cache";

const DashboardPage = lazy(() =>
  import("@/pages/DashboardPage").then((module) => ({ default: module.DashboardPage })),
);
const BoxesPage = lazy(() =>
  import("@/pages/BoxesPage").then((module) => ({ default: module.BoxesPage })),
);
const BoxDetailPage = lazy(() =>
  import("@/pages/BoxDetailPage").then((module) => ({ default: module.BoxDetailPage })),
);
const FilesPage = lazy(() =>
  import("@/pages/FilesPage").then((module) => ({ default: module.FilesPage })),
);
const FileDetailPage = lazy(() =>
  import("@/pages/FileDetailPage").then((module) => ({ default: module.FileDetailPage })),
);
const LotsPage = lazy(() =>
  import("@/pages/LotsPage").then((module) => ({ default: module.LotsPage })),
);
const LotDetailPage = lazy(() =>
  import("@/pages/LotDetailPage").then((module) => ({ default: module.LotDetailPage })),
);
const PalletsPage = lazy(() =>
  import("@/pages/PalletsPage").then((module) => ({ default: module.PalletsPage })),
);
const PalletDetailPage = lazy(() =>
  import("@/pages/PalletDetailPage").then((module) => ({ default: module.PalletDetailPage })),
);
const AlertsPage = lazy(() =>
  import("@/pages/AlertsPage").then((module) => ({ default: module.AlertsPage })),
);
const AlertDetailPage = lazy(() =>
  import("@/pages/AlertDetailPage").then((module) => ({ default: module.AlertDetailPage })),
);
const ExportsPage = lazy(() =>
  import("@/pages/ExportsPage").then((module) => ({ default: module.ExportsPage })),
);
const ProductivityPage = lazy(() =>
  import("@/pages/ProductivityPage").then((module) => ({ default: module.ProductivityPage })),
);
const WarehouseProductivityDetailPage = lazy(() =>
  import("@/pages/WarehouseProductivityDetailPage").then((module) => ({ default: module.WarehouseProductivityDetailPage })),
);
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((module) => ({ default: module.SettingsPage })),
);
const RequestPage = lazy(() =>
  import("@/pages/RequestPage").then((module) => ({ default: module.RequestPage })),
);
const RequestDetailPage = lazy(() =>
  import("@/pages/RequestDetailPage").then((module) => ({ default: module.RequestDetailPage })),
);
const RequestReconciliationPage = lazy(() =>
  import("@/pages/RequestReconciliationPage").then((module) => ({ default: module.RequestReconciliationPage })),
);

// Microsoft errors come back as either an `AuthError` (typed, with errorCode
// and correlationId), a generic Error, or a bare string. Normalize them into
// the SsoError shape the AuthGate renders, and try to extract the underlying
// `AADSTS#####` code from the message so the UI can show it verbatim - that
// code is what makes Entra issues actually diagnosable.
function captureMsalError(raw: unknown) {
  if (!raw) return;
  let message = "";
  let code: string | null = null;
  let correlationId: string | null = null;

  if (raw instanceof AuthError) {
    message = raw.errorMessage || raw.message || raw.errorCode;
    code = raw.errorCode || null;
    const maybeCid = (raw as { correlationId?: string }).correlationId;
    correlationId = maybeCid ?? null;
  } else if (raw instanceof Error) {
    message = raw.message;
  } else {
    message = String(raw);
  }

  const aadstsMatch = /AADSTS\d+/.exec(message);
  if (aadstsMatch) {
    code = aadstsMatch[0];
  }

  setSsoError({
    code,
    message: message || "Microsoft sign-in failed",
    correlationId,
    timestamp: Date.now(),
  });
  console.warn("[auth] MSAL error", { code, message, correlationId });
}

async function bootstrap() {
  await purgeLegacyApiCaches();
  // MSAL needs window.crypto.subtle (HTTPS or localhost). When the SPA is
  // served from a plain-HTTP WAN IP the browser hides that API and
  // initialize() throws "crypto_nonexistent". We swallow it so the rest of
  // the app still mounts and the user can sign in via the local admin tab.
  if (ssoAvailable()) {
    try {
      await msalInstance.initialize();
      msalInstance.enableAccountStorageEvents();

      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
      }
      msalInstance.addEventCallback((event: EventMessage) => {
        if (
          event.eventType === EventType.LOGIN_SUCCESS &&
          event.payload &&
          "account" in event.payload &&
          event.payload.account
        ) {
          msalInstance.setActiveAccount(event.payload.account);
          setSsoError(null);
          return;
        }
        if (
          event.eventType === EventType.LOGIN_FAILURE ||
          event.eventType === EventType.ACQUIRE_TOKEN_FAILURE
        ) {
          captureMsalError(event.error);
        }
      });

      // The redirect leg of `loginRedirect()` resolves here when the user
      // comes back from login.microsoftonline.com. If Microsoft passed back
      // an error in the URL hash, `handleRedirectPromise` rejects with it.
      // Without surfacing this, the user just lands on a blank login screen
      // again with no clue what went wrong.
      try {
        await msalInstance.handleRedirectPromise();
      } catch (err) {
        captureMsalError(err);
      }
    } catch (err) {
      disableSso((err as Error)?.message ?? "msal init failed");
    }
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <MsalProvider instance={msalInstance}>
          <BrowserRouter>
            <AuthGate>
              <CredentialsGate>
                <Suspense fallback={<div className="p-6 text-sm text-slate-500">Loading page...</div>}>
                <Routes>
                  <Route element={<AppShell />}>
                    <Route index element={<DashboardPage />} />
                    <Route path="boxes" element={<BoxesPage />} />
                    <Route path="boxes/:id" element={<BoxDetailPage />} />
                    <Route path="files" element={<FilesPage />} />
                    <Route path="files/:id" element={<FileDetailPage />} />
                    <Route path="lots" element={<LotsPage />} />
                    <Route path="lots/:id" element={<LotDetailPage />} />
                    <Route path="pallets" element={<PalletsPage />} />
                    <Route path="pallets/:id" element={<PalletDetailPage />} />
                    <Route path="requests" element={<RequestPage />} />
                    <Route
                      path="requests/reconciliation"
                      element={<RequestReconciliationPage />}
                    />
                    <Route
                      path="requests/:id"
                      element={<RequestDetailPage />}
                    />
                    <Route path="alerts" element={<AlertsPage />} />
                    <Route path="alerts/:id" element={<AlertDetailPage />} />
                    <Route path="productivity" element={<ProductivityPage />} />
                    <Route
                      path="productivity/:warehouseId"
                      element={<WarehouseProductivityDetailPage />}
                    />
                    <Route path="exports" element={<ExportsPage />} />
                    <Route path="settings" element={<SettingsPage />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Route>
                </Routes>
                </Suspense>
              </CredentialsGate>
            </AuthGate>
          </BrowserRouter>
      </MsalProvider>
    </React.StrictMode>,
  );
}

bootstrap().catch((err) => {
  console.error("[bootstrap] failed", err);
});
