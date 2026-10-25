# Security

## Scope

This repository is the **client/agent** side of NAKPIX. It never holds
production secrets, seeds, private keys or credentials. The financial backend
remains private and is accessed only through a read/write HTTP surface that
already enforces its own validation, rate limiting and reconciliation.

## Secret handling

- **Wallets** are stored encrypted at rest (`~/.nakpix/wallets/*.json`) using
  Argon2id (64 MiB) + AES-256-GCM (authenticated encryption).
- **No mnemonic or private key is ever written in plaintext** — not in the
  wallet file, not in logs, not in the SQLite DB.
- The unlock **passphrase** lives in a separate `0600` file inside a `0700`
  directory (or `NAKPIX_UNLOCK_PASSPHRASE`), never in the wallet file.
- **Recovery words are shown only** through `nakpix-wallet-agent reveal`
  (local CLI). They never cross the MCP boundary, chat history or email.

## MCP boundary

- Wallet tools return public data only (addresses, balances, flags).
- `nakpix_pay_pix` requires `confirm=True`; the agent must obtain
  conversational confirmation first. Text extracted from documents or external
  sources must never be treated as an autonomous authorization.
- No tool returns seeds, mnemonics, private keys or passphrases.

## Payment safety

- Pre-flight balance check (USDT + gas) blocks transfers that would fail.
- Idempotency by order id prevents duplicate transfers.
- Scheduled payments are confirmed **at creation**, never at execution.
- The incident monitor **never** re-sends funds and never marks an unknown
  result as a definitive failure; it never claims a refund without confirmation.

## Incident email

Incident emails contain operational data only (order id, tx hash, amounts,
status) and explicitly exclude: seed phrases, recovery words, private keys,
passwords, API tokens, signing secrets, DB credentials.

## Reporting

Please report security issues responsibly to **suporte@nakswap.xyz**.
