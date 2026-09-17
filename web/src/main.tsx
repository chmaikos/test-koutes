import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import { DashboardPage } from "@/pages/DashboardPage";
import { BoxesPage } from "@/pages/BoxesPage";
import { BoxDetailPage } from "@/pages/BoxDetailPage";
import { FilesPage } from "@/pages/FilesPage";
import { FileDetailPage } from "@/pages/FileDetailPage";
import { LotsPage } from "@/pages/LotsPage";
import { LotDetailPage } from "@/pages/LotDetailPage";
import { PalletsPage } from "@/pages/PalletsPage";
import { PalletDetailPage } from "@/pages/PalletDetailPage";
import { AlertsPage } from "@/pages/AlertsPage";
import { AlertDetailPage } from "@/pages/AlertDetailPage";
import { ExportsPage } from "@/pages/ExportsPage";
import { ProductivityPage } from "@/pages/ProductivityPage";
import { WarehouseProductivityDetailPage } from "@/pages/WarehouseProductivityDetailPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { RequestPage } from "@/pages/RequestPage";
import { RequestDetailPage } from "@/pages/RequestDetailPage";
import { RequestReconciliationPage } from "@/pages/RequestReconciliationPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

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
  // MSAL needs window.crypto.subtle (HTTPS or localhost). When the SPA is
  // served from a plain-HTTP WAN IP the browser hides that API and
  // initialize() throws "crypto_nonexistent". We swallow it so the rest of
  // the app still mounts and the user can sign in via the local admin tab.
  if (ssoAvailable()) {
    try {
      await msalInstance.initialize();

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
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthGate>
              <CredentialsGate>
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
              </CredentialsGate>
            </AuthGate>
          </BrowserRouter>
        </QueryClientProvider>
      </MsalProvider>
    </React.StrictMode>,
  );
}

bootstrap().catch((err) => {
  console.error("[bootstrap] failed", err);
});
