import { useState } from "react";
import { Mail, Plus } from "lucide-react";
import {
  useAlertRecipients,
  useCreateWarehouse,
  useUpdateUser,
  useUpdateWarehouse,
  useUsers,
  useWarehouses,
} from "@/api/hooks";
import type { Role, User, Warehouse } from "@/api/types";

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
      <RecipientsPreview />
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
      <div className="hidden overflow-x-auto md:block">
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
      </div>
      <div className="divide-y divide-slate-100 md:hidden">
        {data?.map((w) => (
          <WarehouseCard
            key={w.id}
            warehouse={w}
            onSave={(patch) => update.mutateAsync({ id: w.id, patch })}
          />
        ))}
      </div>
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

type WarehousePatch = {
  name?: string;
  min_inventory?: number;
  max_capacity?: number;
};

/**
 * Shared edit state for the desktop row and the mobile card so a save
 * pending in one layout reflects in the other. Returning JSX-ready
 * inputs keeps the markup intentionally similar.
 */
function useWarehouseEditor(
  warehouse: Warehouse,
  onSave: (patch: WarehousePatch) => Promise<unknown>,
) {
  const [name, setName] = useState(warehouse.name);
  const [minInv, setMinInv] = useState(warehouse.min_inventory);
  const [maxCap, setMaxCap] = useState(warehouse.max_capacity);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    name !== warehouse.name ||
    minInv !== warehouse.min_inventory ||
    maxCap !== warehouse.max_capacity;

  async function save() {
    setPending(true);
    setError(null);
    try {
      await onSave({ name, min_inventory: minInv, max_capacity: maxCap });
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to save";
      setError(typeof detail === "string" ? detail : "Failed to save");
    } finally {
      setPending(false);
    }
  }

  return {
    name,
    setName,
    minInv,
    setMinInv,
    maxCap,
    setMaxCap,
    pending,
    error,
    dirty,
    save,
  };
}

function WarehouseRow({
  warehouse,
  onSave,
}: {
  warehouse: Warehouse;
  onSave: (patch: WarehousePatch) => Promise<unknown>;
}) {
  const ed = useWarehouseEditor(warehouse, onSave);
  return (
    <tr>
      <td className="px-4 py-2.5">
        <input
          className="input"
          value={ed.name}
          onChange={(e) => ed.setName(e.target.value)}
        />
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={0}
          value={ed.minInv}
          onChange={(e) => ed.setMinInv(Number(e.target.value))}
        />
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={1}
          value={ed.maxCap}
          onChange={(e) => ed.setMaxCap(Number(e.target.value))}
        />
      </td>
      <td className="px-4 py-2.5 text-right">
        {ed.error && (
          <span className="mr-2 text-xs text-rose-600">{ed.error}</span>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={!ed.dirty || ed.pending}
          onClick={() => void ed.save()}
        >
          {ed.pending ? "Saving..." : "Save"}
        </button>
      </td>
    </tr>
  );
}

function WarehouseCard({
  warehouse,
  onSave,
}: {
  warehouse: Warehouse;
  onSave: (patch: WarehousePatch) => Promise<unknown>;
}) {
  const ed = useWarehouseEditor(warehouse, onSave);
  return (
    <div className="space-y-3 px-4 py-3">
      <label className="block">
        <span className="text-xs text-slate-500">Name</span>
        <input
          className="input"
          value={ed.name}
          onChange={(e) => ed.setName(e.target.value)}
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="text-xs text-slate-500">Min inventory</span>
          <input
            type="number"
            className="input"
            min={0}
            value={ed.minInv}
            onChange={(e) => ed.setMinInv(Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Max capacity</span>
          <input
            type="number"
            className="input"
            min={1}
            value={ed.maxCap}
            onChange={(e) => ed.setMaxCap(Number(e.target.value))}
          />
        </label>
      </div>
      {ed.error && <p className="text-xs text-rose-600">{ed.error}</p>}
      <button
        type="button"
        className="btn-primary w-full"
        disabled={!ed.dirty || ed.pending}
        onClick={() => void ed.save()}
      >
        {ed.pending ? "Saving..." : "Save"}
      </button>
    </div>
  );
}

function UsersSection() {
  const { data } = useUsers();
  const { data: warehouses } = useWarehouses();
  const update = useUpdateUser();

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Users</h2>
        <p className="text-xs text-slate-500">
          Roles default to those granted via Entra App Roles. Pinning a role
          here will override Entra until you clear it. New users start with no
          warehouse access — toggle the checkboxes to grant per-warehouse
          permissions.
        </p>
      </header>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-4 py-2.5 text-left">User</th>
              <th className="px-4 py-2.5 text-left">Role</th>
              <th className="px-4 py-2.5 text-left">Override</th>
              <th className="px-4 py-2.5 text-left">Active</th>
              <th className="px-4 py-2.5 text-left">Email alerts</th>
              <th className="px-4 py-2.5 text-left">Warehouses</th>
              <th className="px-4 py-2.5 text-left">Last login</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.map((u) => (
              <tr key={u.id} className="hover:bg-slate-50">
                <td className="px-4 py-2.5">
                  <div className="font-medium">
                    {u.display_name || u.email}
                  </div>
                  <div className="text-xs text-slate-500">{u.email}</div>
                </td>
                <td className="px-4 py-2.5">
                  <select
                    className="input inline-block w-auto"
                    value={u.role}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: {
                          role: e.target.value as Role,
                          role_override: true,
                        },
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
                <td className="px-4 py-2.5">
                  <input
                    type="checkbox"
                    checked={u.email_alerts_enabled}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: { email_alerts_enabled: e.target.checked },
                      })
                    }
                    title={
                      u.email_alerts_enabled
                        ? "Receives alert emails for accessible warehouses"
                        : "Opted out of alert emails"
                    }
                  />
                </td>
                <td className="px-4 py-2.5">
                  <WarehouseAccessCell
                    user={u}
                    warehouses={warehouses ?? []}
                    onChange={(warehouse_ids) =>
                      update.mutate({ id: u.id, patch: { warehouse_ids } })
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
      </div>
      <div className="divide-y divide-slate-100 md:hidden">
        {data?.map((u) => (
          <UserCard
            key={u.id}
            user={u}
            warehouses={warehouses ?? []}
            onPatch={(patch) => update.mutate({ id: u.id, patch })}
          />
        ))}
      </div>
    </section>
  );
}

function UserCard({
  user,
  warehouses,
  onPatch,
}: {
  user: User;
  warehouses: Warehouse[];
  onPatch: (patch: {
    role?: Role;
    role_override?: boolean;
    is_active?: boolean;
    email_alerts_enabled?: boolean;
    warehouse_ids?: number[];
  }) => void;
}) {
  return (
    <div className="space-y-3 px-4 py-3">
      <div>
        <div className="text-sm font-medium">
          {user.display_name || user.email}
        </div>
        <div className="text-xs text-slate-500">{user.email}</div>
      </div>
      <label className="block">
        <span className="text-xs text-slate-500">Role</span>
        <select
          className="input"
          value={user.role}
          onChange={(e) =>
            onPatch({ role: e.target.value as Role, role_override: true })
          }
        >
          <option value="viewer">Viewer</option>
          <option value="operator">Operator</option>
          <option value="admin">Admin</option>
        </select>
      </label>
      <div className="grid grid-cols-1 gap-2 text-sm">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.role_override}
            onChange={(e) => onPatch({ role_override: e.target.checked })}
          />
          <span>Override Entra role</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.is_active}
            onChange={(e) => onPatch({ is_active: e.target.checked })}
          />
          <span>Active</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.email_alerts_enabled}
            onChange={(e) =>
              onPatch({ email_alerts_enabled: e.target.checked })
            }
          />
          <span>Email alerts</span>
        </label>
      </div>
      <div>
        <div className="mb-1 text-xs text-slate-500">Warehouses</div>
        <WarehouseAccessCell
          user={user}
          warehouses={warehouses}
          onChange={(warehouse_ids) => onPatch({ warehouse_ids })}
        />
      </div>
      <div className="text-xs text-slate-500">
        Last login{" "}
        {user.last_login_at
          ? new Date(user.last_login_at).toLocaleString()
          : "—"}
      </div>
    </div>
  );
}

function RecipientsPreview() {
  const { data, isLoading, error } = useAlertRecipients();

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <div className="flex items-center gap-2">
          <Mail className="h-4 w-4 text-slate-500" />
          <h2 className="font-semibold">Alert email routing</h2>
        </div>
        <p className="text-xs text-slate-500">
          Who would receive an alert email if one fired right now. Active
          users with the warehouse ACL granted (plus all admins) get the
          primary mail; admins always cover escalation. Toggle "Email
          alerts" off above to opt a user out without removing their access.
        </p>
      </header>
      {isLoading && (
        <div className="px-5 py-6 text-sm text-slate-400">Loading…</div>
      )}
      {error && (
        <div className="px-5 py-6 text-sm text-rose-600">
          Failed to load recipient preview.
        </div>
      )}
      {data && (
        <div className="divide-y divide-slate-100">
          {data.warehouses.map((w) => (
            <div
              key={w.warehouse_id}
              className="grid gap-3 px-5 py-3 sm:grid-cols-[14rem_1fr]"
            >
              <div className="text-sm font-medium text-slate-700">
                {w.warehouse_name}
              </div>
              <RecipientChips emails={w.primary} emptyHint="No recipients (alerts will fall back to ALERT_EMAIL_TO)" />
            </div>
          ))}
          <div className="grid gap-3 bg-slate-50/60 px-5 py-3 sm:grid-cols-[14rem_1fr]">
            <div className="text-sm font-medium text-slate-700">
              Escalation (admins)
            </div>
            <RecipientChips
              emails={data.escalation}
              emptyHint="No active admins with email alerts enabled"
            />
          </div>
        </div>
      )}
    </section>
  );
}

function RecipientChips({
  emails,
  emptyHint,
}: {
  emails: string[];
  emptyHint: string;
}) {
  if (emails.length === 0) {
    return <span className="text-xs italic text-slate-400">{emptyHint}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {emails.map((email) => (
        <span
          key={email}
          className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700"
        >
          {email}
        </span>
      ))}
    </div>
  );
}

function WarehouseAccessCell({
  user,
  warehouses,
  onChange,
}: {
  user: User;
  warehouses: Warehouse[];
  onChange: (warehouse_ids: number[]) => void;
}) {
  // Admins always have unrestricted access -- the ACL doesn't apply to them
  // regardless of what's stored in user_warehouse_access. Surface that as a
  // read-only badge so it's clear to the operator.
  if (user.role === "admin") {
    return (
      <span className="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
        All warehouses
      </span>
    );
  }
  const granted = new Set(user.warehouse_ids);
  return (
    <div className="flex flex-wrap gap-2">
      {warehouses.length === 0 ? (
        <span className="text-xs text-slate-400">No warehouses</span>
      ) : (
        warehouses.map((w) => {
          const checked = granted.has(w.id);
          return (
            <label
              key={w.id}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs hover:bg-slate-50"
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => {
                  const next = new Set(granted);
                  if (e.target.checked) {
                    next.add(w.id);
                  } else {
                    next.delete(w.id);
                  }
                  onChange(Array.from(next).sort((a, b) => a - b));
                }}
              />
              <span>{w.name}</span>
            </label>
          );
        })
      )}
    </div>
  );
}
