import type { ReactNode } from "react";
import { useMe } from "@/api/hooks";
import { ChangeCredentialsPage } from "@/pages/ChangeCredentialsPage";

export function CredentialsGate({ children }: { children: ReactNode }) {
  const { data: user, isLoading } = useMe();

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
