import { useState } from "react";
import { Plus } from "lucide-react";
import {
  useCreateWarehouse,
  useUpdateUser,
  useUpdateWarehouse,
  useUsers,
  useWarehouses,
} from "@/api/hooks";
import type { Role } from "@/api/types";

export function SettingsPage() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-slate-500">
          Admin-only: warehouse thresholds and user access.
        </p>
      </header>

      <WarehousesSection />
      <UsersSection />
    </div>
  );
}

function WarehousesSection() {
  const { data } = useWarehouses();
  const update = useUpdateWarehouse();
  const create = useCreateWarehouse();
  const [showAdd, setShowAdd] = useState(false);

  return (
    <section className="card overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-3">
        <div>
          <h2 className="font-semibold">Warehouses & thresholds</h2>
          <p className="text-xs text-slate-500">
            Inventory below the minimum or at/above the maximum will trigger an
            alert.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setShowAdd((v) => !v)}
        >
          <Plus className="h-4 w-4" />
          {showAdd ? "Cancel" : "Add warehouse"}
        </button>
      </header>
      {showAdd && (
        <NewWarehouseForm
          onCreate={async (input) => {
            await create.mutateAsync(input);
            setShowAdd(false);
          }}
          onCancel={() => setShowAdd(false)}
        />
      )}
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
          <tr>
            <th className="px-4 py-2.5 text-left">Name</th>
            <th className="px-4 py-2.5 text-left">Min inventory</th>
            <th className="px-4 py-2.5 text-left">Max capacity</th>
            <th className="px-4 py-2.5"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {data?.map((w) => (
            <WarehouseRow
              key={w.id}
              warehouse={w}
              onSave={(patch) => update.mutateAsync({ id: w.id, patch })}
            />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function NewWarehouseForm({
  onCreate,
  onCancel,
}: {
  onCreate: (input: {
    name: string;
    min_inventory: number;
    max_capacity: number;
  }) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [minInv, setMinInv] = useState(0);
  const [maxCap, setMaxCap] = useState(1000);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="grid gap-3 border-b border-slate-100 bg-slate-50/60 px-5 py-4 sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-end">
      <label className="block">
        <span className="text-xs text-slate-500">Name</span>
        <input
          className="input"
          autoFocus
          value={name}
          placeholder="Building 4"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Min inventory</span>
        <input
          type="number"
          className="input"
          min={0}
          value={minInv}
          onChange={(e) => setMinInv(Number(e.target.value))}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Max capacity</span>
        <input
          type="number"
          className="input"
          min={1}
          value={maxCap}
          onChange={(e) => setMaxCap(Number(e.target.value))}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={pending || !name.trim()}
          onClick={async () => {
            setPending(true);
            setError(null);
            try {
              await onCreate({
                name: name.trim(),
                min_inventory: minInv,
                max_capacity: maxCap,
              });
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to create warehouse";
              setError(typeof detail === "string" ? detail : "Failed to create warehouse");
            } finally {
              setPending(false);
            }
          }}
        >
          {pending ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      {error && (
        <p className="text-xs text-rose-600 sm:col-span-4">{error}</p>
      )}
    </div>
  );
}

function WarehouseRow({
  warehouse,
  onSave,
}: {
  warehouse: import("@/api/types").Warehouse;
  onSave: (patch: {
    name?: string;
    min_inventory?: number;
    max_capacity?: number;
  }) => Promise<unknown>;
}) {
  const [name, setName] = useState(warehouse.name);
  const [minInv, setMinInv] = useState(warehouse.min_inventory);
  const [maxCap, setMaxCap] = useState(warehouse.max_capacity);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    name !== warehouse.name ||
    minInv !== warehouse.min_inventory ||
    maxCap !== warehouse.max_capacity;

  return (
    <tr>
      <td className="px-4 py-2.5">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={0}
          value={minInv}
          onChange={(e) => setMinInv(Number(e.target.value))}
        />
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={1}
          value={maxCap}
          onChange={(e) => setMaxCap(Number(e.target.value))}
        />
      </td>
      <td className="px-4 py-2.5 text-right">
        {error && <span className="mr-2 text-xs text-rose-600">{error}</span>}
        <button
          type="button"
          className="btn-primary"
          disabled={!dirty || pending}
          onClick={async () => {
            setPending(true);
            setError(null);
            try {
              await onSave({ name, min_inventory: minInv, max_capacity: maxCap });
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to save";
              setError(detail);
            } finally {
              setPending(false);
            }
          }}
        >
          {pending ? "Saving..." : "Save"}
        </button>
      </td>
    </tr>
  );
}

function UsersSection() {
  const { data } = useUsers();
  const update = useUpdateUser();

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Users</h2>
        <p className="text-xs text-slate-500">
          Roles default to those granted via Entra App Roles. Pinning a role
          here will override Entra until you clear it.
        </p>
      </header>
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
          <tr>
            <th className="px-4 py-2.5 text-left">User</th>
            <th className="px-4 py-2.5 text-left">Role</th>
            <th className="px-4 py-2.5 text-left">Override</th>
            <th className="px-4 py-2.5 text-left">Active</th>
            <th className="px-4 py-2.5 text-left">Last login</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {data?.map((u) => (
            <tr key={u.id} className="hover:bg-slate-50">
              <td className="px-4 py-2.5">
                <div className="font-medium">{u.display_name || u.email}</div>
                <div className="text-xs text-slate-500">{u.email}</div>
              </td>
              <td className="px-4 py-2.5">
                <select
                  className="input inline-block w-auto"
                  value={u.role}
                  onChange={(e) =>
                    update.mutate({
                      id: u.id,
                      patch: { role: e.target.value as Role, role_override: true },
                    })
                  }
                >
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </select>
              </td>
              <td className="px-4 py-2.5">
                <input
                  type="checkbox"
                  checked={u.role_override}
                  onChange={(e) =>
                    update.mutate({
                      id: u.id,
                      patch: { role_override: e.target.checked },
                    })
                  }
                />
              </td>
              <td className="px-4 py-2.5">
                <input
                  type="checkbox"
                  checked={u.is_active}
                  onChange={(e) =>
                    update.mutate({
                      id: u.id,
                      patch: { is_active: e.target.checked },
                    })
                  }
                />
              </td>
              <td className="px-4 py-2.5 text-slate-500">
                {u.last_login_at
                  ? new Date(u.last_login_at).toLocaleString()
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
