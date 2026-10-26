# Shark Pay and SmartWallet Integration

**Goal:** Add the user's two registered channels to the existing read-only collection, Beijing-time reports and PostgreSQL capture pipeline.

**Design:** Verify each new public SPA's API host, login, signing protocol and order-pool parameters before implementation. Add isolated `sharkpay` and `smartwallet` providers using the existing signed-pool implementation where compatible. Extend only the explicit trusted host pairs and registry; retain the exact read-only endpoint allowlist. Channel failures remain isolated and incomplete results stay out of trusted totals. Store supplied credentials only in ignored private local configuration.

- [x] Inspect the two user-provided pages and their observed public assets; record protocol evidence under ignored `.firecrawl/` files.
- [x] Add provider-specific configuration, client, spec and collection tests before their implementation; verify real read-only login and complete collection for both channels without printing authentication material.
- [x] Register both channels and test configured ordering, host isolation and the six-channel collection-to-SQL path.
- [x] Add empty configuration examples and usage documentation; prepare a private portable configuration for Mac Studio while preserving existing local settings and private backups.
- [x] Independently review the final changes, pass all 271 offline tests and both live checks, and prepare the explicit source changes for publication after a staged secret audit.
