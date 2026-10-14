import { startLiveStream } from "./liveStream";
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

  useEffect(() => startLiveStream({
    baseURL,
    acquireToken: async () => {
      const localToken = getLocalToken();
      if (localToken) return localToken;
      if (!ssoAvailable()) return null;
      const account = instance.getActiveAccount() ?? instance.getAllAccounts()[0];
      return account ? acquireApiToken(account) : null;
    },
    onMessage: (event) => {
      try { handleEvent(JSON.parse(event.data) as StreamEvent, qc); }
      catch { console.warn("[stream] malformed event"); }
    },
    onReauthenticate: () => { void qc.invalidateQueries({ queryKey: ["me"] }); },
  }), [instance, qc]);
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
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["file-integrity"] });
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
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["file-integrity"] });
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
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["file-integrity"] });
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
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["file-integrity"] });
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
    case "file.created":
    case "file.updated":
    case "file.moved":
    case "file.archived":
    case "file.restored": {
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["file"] });
      qc.invalidateQueries({ queryKey: ["file-events"] });
      qc.invalidateQueries({ queryKey: ["file-integrity"] });
      qc.invalidateQueries({ queryKey: ["boxes"] });
      qc.invalidateQueries({ queryKey: ["lots"] });
      qc.invalidateQueries({ queryKey: ["pallets"] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request"] });
      qc.invalidateQueries({ queryKey: ["request-reconciliation"] });
      qc.invalidateQueries({ queryKey: ["request-suggestion"] });
      qc.invalidateQueries({ queryKey: ["return-sources"] });
      qc.invalidateQueries({ queryKey: ["return-candidates"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      if (typeof event.data.box_id === "number") {
        qc.invalidateQueries({ queryKey: queryKeys.box(event.data.box_id) });
      }
      if (typeof event.data.source_box_id === "number") {
        qc.invalidateQueries({
          queryKey: queryKeys.box(event.data.source_box_id),
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
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["file-integrity"] });
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
