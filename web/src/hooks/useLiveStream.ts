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
    case "pallet.created":
    case "pallet.updated":
    case "pallet.renamed":
    case "pallet.archived":
    case "pallet.restored": {
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: ["pallet-options"] });
      qc.invalidateQueries({ queryKey: ["pallet"] });
      qc.invalidateQueries({ queryKey: ["pallet-events"] });
      qc.invalidateQueries({ queryKey: ["pallet-integrity"] });
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["lot"] });
      qc.invalidateQueries({ queryKey: ["lot-boxes"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      if (typeof event.data.id === "number") {
        qc.invalidateQueries({ queryKey: ["pallet", event.data.id] });
        qc.invalidateQueries({ queryKey: ["pallet-events", event.data.id] });
      }
      break;
    }
    case "box.updated":
    case "box.deleted":
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: ["pallet-options"] });
      qc.invalidateQueries({ queryKey: ["pallet"] });
      qc.invalidateQueries({ queryKey: ["pallet-events"] });
      qc.invalidateQueries({ queryKey: ["pallet-integrity"] });
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: ["pallet-options"] });
      qc.invalidateQueries({ queryKey: ["lot-options"] });
      qc.invalidateQueries({ queryKey: ["lot"] });
      qc.invalidateQueries({ queryKey: ["lot-events"] });
      qc.invalidateQueries({ queryKey: ["lot-boxes"] });
      qc.invalidateQueries({ queryKey: ["lot-purge-preview"] });
      qc.invalidateQueries({ queryKey: ["lot-force-purge-preview"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request"] });
      qc.invalidateQueries({ queryKey: ["request-suggestion"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["exports"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      if (typeof event.data.id === "number") {
        if (event.type === "box.deleted" && event.data.archived !== true) {
          qc.removeQueries({
            queryKey: queryKeys.box(event.data.id),
            exact: true,
          });
          qc.removeQueries({
            queryKey: queryKeys.boxEvents(event.data.id),
            exact: true,
          });
        } else {
          qc.invalidateQueries({ queryKey: queryKeys.box(event.data.id) });
          qc.invalidateQueries({
            queryKey: queryKeys.boxEvents(event.data.id),
          });
        }
      }
      break;
    case "lot.created":
    case "lot.renamed":
    case "lot.reassigned":
    case "lot.merged": {
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: ["pallet-options"] });
      qc.invalidateQueries({ queryKey: ["pallet"] });
      qc.invalidateQueries({ queryKey: ["pallet-events"] });
      qc.invalidateQueries({ queryKey: ["pallet-integrity"] });
      qc.invalidateQueries({ queryKey: ["lot-options"] });
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request"] });
      qc.invalidateQueries({ queryKey: ["request-suggestion"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      const lotIds = [
        event.data.id,
        event.data.from_lot_id,
        event.data.to_lot_id,
      ].filter((value): value is number => typeof value === "number");
      for (const lotId of new Set(lotIds)) {
        qc.invalidateQueries({ queryKey: queryKeys.lot(lotId) });
        qc.invalidateQueries({ queryKey: queryKeys.lotEvents(lotId) });
        qc.invalidateQueries({ queryKey: ["lot-boxes", lotId] });
        qc.invalidateQueries({
          queryKey: queryKeys.lotPurgePreview(lotId),
        });
        qc.invalidateQueries({
          queryKey: queryKeys.lotForcePurgePreview(lotId),
        });
      }
      if (typeof event.data.box_id === "number") {
        qc.invalidateQueries({ queryKey: queryKeys.box(event.data.box_id) });
        qc.invalidateQueries({
          queryKey: queryKeys.boxEvents(event.data.box_id),
        });
      }
      break;
    }
    case "lot.purged": {
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["lot-options"] });
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: ["pallet-options"] });
      qc.invalidateQueries({ queryKey: ["pallet-integrity"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request-reconciliation"] });
      qc.invalidateQueries({ queryKey: ["request-analytics"] });
      qc.invalidateQueries({ queryKey: ["request-suggestion"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["exports"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      qc.removeQueries({ queryKey: ["box"] });
      qc.removeQueries({ queryKey: ["box-events"] });
      qc.removeQueries({ queryKey: ["pallet"] });
      qc.removeQueries({ queryKey: ["pallet-events"] });
      qc.removeQueries({ queryKey: ["request"] });
      qc.removeQueries({ queryKey: ["request-events"] });
      qc.removeQueries({ queryKey: ["request-documents"] });
      qc.removeQueries({ queryKey: ["request-comments"] });
      qc.removeQueries({ queryKey: ["request-attachments"] });
      qc.removeQueries({ queryKey: ["request-discrepancies"] });
      const lotId =
        typeof event.data.lot_id === "number"
          ? event.data.lot_id
          : typeof event.data.id === "number"
            ? event.data.id
            : undefined;
      if (lotId !== undefined) {
        qc.removeQueries({ queryKey: queryKeys.lot(lotId), exact: true });
        qc.removeQueries({
          queryKey: queryKeys.lotEvents(lotId),
          exact: true,
        });
        qc.removeQueries({ queryKey: ["lot-boxes", lotId] });
        qc.removeQueries({
          queryKey: queryKeys.lotPurgePreview(lotId),
          exact: true,
        });
        qc.removeQueries({
          queryKey: queryKeys.lotForcePurgePreview(lotId),
          exact: true,
        });
      }
      break;
    }
    case "alert.triggered":
    case "alert.resolved":
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      break;
    case "request.created":
    case "request.updated": {
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["lot-options"] });
      qc.invalidateQueries({ queryKey: ["lot-purge-preview"] });
      qc.invalidateQueries({ queryKey: ["lot-force-purge-preview"] });
      qc.invalidateQueries({ queryKey: ["request-suggestion"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      const requestId =
        typeof event.data.id === "number"
          ? event.data.id
          : typeof event.data.request_id === "number"
            ? event.data.request_id
            : undefined;
      if (requestId !== undefined) {
        qc.invalidateQueries({ queryKey: queryKeys.request(requestId) });
        qc.invalidateQueries({
          queryKey: queryKeys.requestEvents(requestId),
        });
        qc.invalidateQueries({
          queryKey: queryKeys.requestDocuments(requestId),
        });
      }
      break;
    }
    case "notification.created":
      qc.invalidateQueries({ queryKey: ["notifications"] });
      break;
    default:
      break;
  }
}
