import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, ShieldAlert } from "lucide-react";
import { changeLocalCredentials } from "@/auth/local";
import { queryKeys } from "@/api/hooks";
import type { User } from "@/api/types";

const MIN_PASSWORD_LENGTH = 12;

interface Props {
  user: User;
}

export function ChangeCredentialsPage({ user }: Props) {
  const qc = useQueryClient();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newUsername, setNewUsername] = useState(user.username ?? "");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const usernameUnchanged =
    newUsername.trim() === (user.username ?? "").trim();

  return (
    <div className="flex h-full items-center justify-center px-4 py-8">
      <div className="card card-pad w-full max-w-md">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-amber-700">
          <ShieldAlert className="h-6 w-6" />
        </div>
        <h1 className="text-center text-xl font-semibold">
          Set your credentials
        </h1>
        <p className="mt-1 text-center text-sm text-slate-500">
          You're signed in with the bootstrap admin account. Pick a new
          username and password before you can use the rest of the app.
        </p>

        <form
          className="mt-5 space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            if (newPassword !== confirmPassword) {
              setError("New password and confirmation do not match.");
              return;
            }
            if (newPassword.length < MIN_PASSWORD_LENGTH) {
              setError(
                `New password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
              );
              return;
            }
            if (newPassword === currentPassword) {
              setError("New password must differ from the current one.");
              return;
            }
            setSubmitting(true);
            try {
              await changeLocalCredentials({
                current_password: currentPassword,
                new_password: newPassword,
                new_username: usernameUnchanged ? undefined : newUsername.trim(),
              });
              qc.invalidateQueries({ queryKey: queryKeys.me });
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to update credentials";
              setError(detail);
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <label className="block">
            <span className="text-xs text-slate-500">Current password</span>
            <input
              required
              type="password"
              autoComplete="current-password"
              className="input"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">New username</span>
            <input
              required
              autoComplete="username"
              className="input"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">
              New password (min {MIN_PASSWORD_LENGTH} characters)
            </span>
            <input
              required
              type="password"
              autoComplete="new-password"
              className="input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Confirm new password</span>
            <input
              required
              type="password"
              autoComplete="new-password"
              className="input"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </label>
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <button
            type="submit"
            className="btn-primary w-full"
            disabled={submitting}
          >
            <KeyRound className="h-4 w-4" />
            {submitting ? "Saving..." : "Save and continue"}
          </button>
        </form>
      </div>
    </div>
  );
}
