import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useMsal } from "@azure/msal-react";
import { acquireApiToken, ssoAvailable } from "@/auth/msal";
import { getLocalToken } from "@/auth/local";
import { queryKeys } from "@/api/hooks";

interface StreamEvent {
  type: string;
  data: Record<string, unknown>;
}

const baseURL = (import.meta.env.VITE_API_BASE_URL as string) || "/api";

export function useLiveStream() {
  const qc = useQueryClient();
  const { instance } = useMsal();

  useEffect(() => {
    let es: EventSource | null = null;
    let cancelled = false;

    (async () => {
      let token: string | null = getLocalToken();
      if (!token) {
        if (!ssoAvailable()) return;
        const account =
          instance.getActiveAccount() ?? instance.getAllAccounts()[0];
        if (!account) return;
        try {
          token = await acquireApiToken(account);
        } catch (err) {
          console.warn("[stream] could not acquire token", err);
          return;
        }
      }
      if (cancelled || !token) return;
      try {
        const url = `${baseURL}/stream?access_token=${encodeURIComponent(token)}`;
        es = new EventSource(url);
        es.addEventListener("message", (ev) => {
          try {
            const payload: StreamEvent = JSON.parse(
              (ev as MessageEvent<string>).data,
            );
            handleEvent(payload, qc);
          } catch (err) {
            console.warn("[stream] malformed event", err);
          }
        });
        es.addEventListener("error", () => {
          // EventSource auto-reconnects; just log.
          console.debug("[stream] connection blip");
        });
      } catch (err) {
        console.warn("[stream] failed to open", err);
      }
    })();

    return () => {
      cancelled = true;
      if (es) es.close();
    };
  }, [instance, qc]);
}

function handleEvent(
  event: StreamEvent,
  qc: ReturnType<typeof useQueryClient>,
) {
  switch (event.type) {
    case "box.updated":
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      if (typeof event.data.id === "number") {
        qc.invalidateQueries({ queryKey: queryKeys.box(event.data.id) });
        qc.invalidateQueries({
          queryKey: queryKeys.boxEvents(event.data.id),
        });
      }
      break;
    case "alert.triggered":
    case "alert.resolved":
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      break;
    default:
      break;
  }
}
