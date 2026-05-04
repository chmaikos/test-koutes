import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MsalProvider } from "@azure/msal-react";
import { EventType } from "@azure/msal-browser";
import "./index.css";
import { disableSso, msalInstance, ssoAvailable } from "@/auth/msal";
import { AuthGate } from "@/auth/AuthGate";
import { AppShell } from "@/components/AppShell";
import { CredentialsGate } from "@/components/CredentialsGate";
import { DashboardPage } from "@/pages/DashboardPage";
import { BoxesPage } from "@/pages/BoxesPage";
import { BoxDetailPage } from "@/pages/BoxDetailPage";
import { AlertsPage } from "@/pages/AlertsPage";
import { ExportsPage } from "@/pages/ExportsPage";
import { SettingsPage } from "@/pages/SettingsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

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
      msalInstance.addEventCallback((event) => {
        if (
          event.eventType === EventType.LOGIN_SUCCESS &&
          event.payload &&
          "account" in event.payload &&
          event.payload.account
        ) {
          msalInstance.setActiveAccount(event.payload.account);
        }
      });
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
                    <Route path="alerts" element={<AlertsPage />} />
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
