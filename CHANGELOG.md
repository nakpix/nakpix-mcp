# Changelog

All notable changes to the NAKPIX MCP server.

## [0.1.0] — 2026-10-25

### Added
- FastMCP server (~40 tools): wallets, balances, deposits, contacts, coupons,
  payments, scheduling, preferences, notifications.
- Local wallet agent (`nakpix-wallet-agent`): generate/import/recover/export
  Polygon (EVM) and Solana wallets, encrypted with Argon2id + AES-256-GCM.
- On-chain balance/deposit queries (Polygon + Solana, read-only).
- Persistent scheduler + worker for one-shot and recurring PIX (idempotent).
- 15-minute unresolved-payment incident monitor with dedup + SMTP email.
- Persistent notification queue for offline agent delivery.
- Docker + docker-compose deployment for background services.
- Test suite (pytest) covering wallet security, validation, storage, scheduler,
  and server tools.
