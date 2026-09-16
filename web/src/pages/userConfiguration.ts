export const USER_CONFIGURATION_COLUMNS = [
  { key: "identity", label: "User", field: null },
  { key: "role", label: "Role", field: "role" },
  { key: "override", label: "Override", field: "role_override" },
  { key: "active", label: "Active", field: "is_active" },
  {
    key: "alertEmail",
    label: "Email alerts",
    field: "email_alerts_enabled",
  },
  {
    key: "requestEmail",
    label: "Request email",
    field: "email_requests_enabled",
  },
  { key: "warehouses", label: "Warehouses", field: "warehouse_ids" },
  { key: "lastLogin", label: "Last login", field: null },
] as const;

export type UserConfigurationColumnKey =
  (typeof USER_CONFIGURATION_COLUMNS)[number]["key"];
