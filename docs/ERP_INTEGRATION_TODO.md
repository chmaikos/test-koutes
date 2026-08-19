# ERP integration — deferred TODO

ERP connectivity is intentionally outside the current requests-workflow roadmap. The application continues to use manually entered ERP references and versioned PDF/JPEG/PNG document uploads.

Implementation must not begin until the following are agreed with the ERP owner:

- API or webhook contract, environments, availability guarantees, and rate limits.
- Authentication, credential rotation, network allow-listing, and service-account ownership.
- Canonical identifiers for delivery notes, return notes, requests, warehouses,
  lots, pallets, and boxes. Define whether ERP Pallet identity is the app's
  numeric `pallet_id`, the case-insensitive number scoped by canonical Lot, or
  an explicit cross-system mapping. A Pallet has no warehouse identifier or
  location; warehouse distribution must be derived from its Boxes. Never use
  display text alone as a global key.
- Snapshot semantics for request lines: immutable Lot/Pallet display values
  preserve what was received even after a later rename, Box relocation, merge
  absorption, archival, or purge. Agree which canonical IDs remain resolvable and how an
  absorbed Pallet points to its surviving target.
- Reference uniqueness, document versioning, cancellation, correction, and late-arrival rules.
- Direction of authority for quantities, line discrepancies, completion, and reconciliation.
- Idempotency keys, retry policy, replay handling, ordering guarantees, and dead-letter recovery.
- Mapping between ERP statuses and application request/fulfillment states.
- Personal-data classification, audit retention, logging redaction, and access controls.
- Monitoring, support ownership, backup consistency, and disaster-recovery expectations.

Recommended delivery order after the contract exists:

1. Read-only ERP reference validation.
2. Read-only document metadata and file retrieval.
3. ERP-line-to-fulfillment reconciliation.
4. Completion/discrepancy status publishing.
5. Webhook-driven request creation or updates.

Each stage requires contract tests, a sandbox ERP environment, idempotent integration-attempt auditing, and a feature flag with manual-upload fallback.
