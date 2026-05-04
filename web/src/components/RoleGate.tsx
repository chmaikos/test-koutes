import type { ReactNode } from "react";
import { useMe } from "@/api/hooks";
import type { Role } from "@/api/types";

interface RoleGateProps {
  roles: Role[];
  children: ReactNode;
  fallback?: ReactNode;
}

export function RoleGate({ roles, children, fallback = null }: RoleGateProps) {
  const me = useMe();
  if (me.isLoading || !me.data) return null;
  if (!roles.includes(me.data.role)) return <>{fallback}</>;
  return <>{children}</>;
}

export function useHasRole(roles: Role[]): boolean {
  const me = useMe();
  if (!me.data) return false;
  return roles.includes(me.data.role);
}
