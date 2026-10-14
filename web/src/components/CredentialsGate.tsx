import type { ReactNode } from "react";
import { useMe } from "@/api/hooks";
import { ChangeCredentialsPage } from "@/pages/ChangeCredentialsPage";

export function CredentialsGate({ children }: { children: ReactNode }) {
  const { data: user, isLoading, isError, refetch } = useMe();

  if (isError) {
    return <div role="alert" className="flex h-full flex-col items-center justify-center gap-3 text-sm text-slate-600">
      <p>Your account could not be loaded. Check your connection and access permissions.</p>
      <button className="btn-secondary" onClick={() => void refetch()}>Try again</button>
    </div>;
  }

  if (isLoading || !user) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        Loading your account...
      </div>
    );
  }

  if (user.must_change_credentials) {
    return <ChangeCredentialsPage user={user} />;
  }

  return <>{children}</>;
}
