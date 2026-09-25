import { describe, expect, it } from "vitest";
import appShellSource from "@/components/AppShell.tsx?raw";
import boxDetailSource from "@/pages/BoxDetailPage.tsx?raw";
import exportsSource from "@/pages/ExportsPage.tsx?raw";
import fileDetailSource from "@/pages/FileDetailPage.tsx?raw";
import filesPageSource from "@/pages/FilesPage.tsx?raw";
import hooksSource from "@/api/hooks.ts?raw";
import mainSource from "@/main.tsx?raw";
import requestDetailSource from "@/pages/RequestDetailPage.tsx?raw";
import { cleanFileItems, validateFileItems } from "@/components/fileItems";
import { parseFileSearchParams } from "@/pages/files";

describe("physical File filters and validation", () => {
  it("parses supported list filters, sort, and pagination", () => {
    expect(
      parseFileSearchParams(
        new URLSearchParams(
          "q=ABC&warehouse_id=1&lot_id=2&pallet_id=3&box_id=4&status=processing&activity=archived&include_inactive=true&sort_by=updated_at&sort_dir=desc&page=2&page_size=50",
        ),
      ),
    ).toEqual({
      filters: {
        search: "ABC",
        warehouse_id: 1,
        lot_id: 2,
        pallet_id: 3,
        box_id: 4,
        status: "processing",
        activity: "archived",
        include_inactive: true,
        sort_by: "updated_at",
        sort_dir: "desc",
      },
      page: 2,
      pageSize: 50,
    });
  });

  it("drops invalid URL values", () => {
    expect(
      parseFileSearchParams(
        new URLSearchParams(
          "warehouse_id=-1&activity=deleted&sort_by=owner&page=0&page_size=999",
        ),
      ),
    ).toEqual({ filters: {}, page: 1, pageSize: 25 });
  });

  it("requires references and rejects normalized duplicates", () => {
    expect(validateFileItems([], { requireAtLeastOne: true })).toMatch(
      /at least one/i,
    );
    expect(
      validateFileItems([
        { reference: " File   1 " },
        { reference: "file 1" },
      ]),
    ).toMatch(/duplicated/i);
    expect(
      cleanFileItems([
        { reference: " FILE  1 ", description: " Notes " },
      ]),
    ).toEqual([
      { reference: "FILE 1", description: "Notes" },
    ]);
  });
});

describe("physical File frontend wiring", () => {
  it("registers navigation and list/detail routes", () => {
    expect(appShellSource).toContain('{ to: "/files", label: "Files"');
    expect(mainSource).toContain('path="files"');
    expect(mainSource).toContain('path="files/:id"');
  });

  it("wires every File mutation and broad invalidation", () => {
    for (const name of [
      "useCreateFile",
      "useUpdateFile",
      "useMoveFile",
      "useArchiveFile",
      "useRestoreFile",
    ]) {
      expect(hooksSource).toContain(`function ${name}`);
    }
    for (const key of [
      '["files"]',
      '["boxes"]',
      '["lots"]',
      '["pallets"]',
      '["requests"]',
    ]) {
      expect(hooksSource).toContain(`queryKey: ${key}`);
    }
    expect(hooksSource).toContain("queryKeys.dashboard");
  });

  it("gates inactive inventory and force controls by role", () => {
    expect(filesPageSource).toContain('useHasRole(["admin"])');
    expect(filesPageSource).toContain("include_inactive: undefined");
    expect(fileDetailSource).toContain("useFile(fileId, isAdmin, true)");
    expect(fileDetailSource).toContain("item.archived_at !== null");
    expect(fileDetailSource).toContain("Force this action");
    expect(exportsSource).toContain("isAdmin && fileIncludeInactive");
  });

  it("uses authoritative Box File rows and exposes actions", () => {
    expect(boxDetailSource).toContain('activity: "all"');
    expect(boxDetailSource).toContain("Add File");
    expect(boxDetailSource).toContain("Confirm archive");
    expect(boxDetailSource).toContain('to={`/files/${file.id}`}');
  });

  it("renders request snapshots and explicit move acceptance", () => {
    expect(requestDetailSource).toContain(
      "Immutable physical File snapshots",
    );
    expect(requestDetailSource).toContain("file.snapshot_kind");
    expect(requestDetailSource).toContain("acceptFileMoves");
    expect(requestDetailSource).toContain(
      "Physical File reconciliation details",
    );
  });

  it("offers filtered CSV and XLSX File exports", () => {
    expect(exportsSource).toContain("/exports/files.${format}");
    expect(exportsSource).toContain("Download Files CSV");
    expect(exportsSource).toContain("Download Files XLSX");
  });
});
