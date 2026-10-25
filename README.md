# NAKPIX MCP Server

Official **NAKPIX** integration for AI agents (OpenClaw, Hermes, Claude,
OpenCode and other MCP-compatible clients). Pay **PIX** with crypto
(**USDT** on Polygon/Solana) — no conventional account, no mandatory KYC.

The MCP reuses NAKPIX's existing financial backend: order creation, quoting,
coupon validation and PIX execution. It does **not** reimplement payment
processing.

> ⚠️ The backend remains private. This repository contains no seeds, private
> keys, credentials or production data. See [SECURITY.md](SECURITY.md).

## What agents can do

- Create a Polygon or Solana wallet locally (encrypted).
- Get the public address and a secure backup (recovery words via CLI only).
- Check USDT/POL/SOL balances and deposits on-chain.
- Maintain a persistent PIX contact agenda.
- Set a permanent default discount coupon.
- Pay PIX with natural-language commands.
- Pay bills received as audio/image/QR/text/PDF.
- Schedule future and recurring payments (confirmed once, at creation).
- Receive real receipts and real failure reasons.
- Get alerts when an operation is unresolved for 15+ minutes.

## Architecture

```
agent (LLM)
   │  MCP (stdio)
   ▼
nakpix-mcp server ──► nakpix-wallet-agent  (LOCAL, keys stay here)
   │                       └─ ~/.nakpix/wallets/*.json  (encrypted)
   ├──► backend HTTP  (nakpix.xyz relays — existing processing)
   ├──► blockchain    (balance/deposit read-only)
   ├──► scheduler     (persistent, background process)
   └──► incidents     (15-min unresolved monitor + email)
```

The **wallet-agent** is the only component that touches secret material. The
MCP server never sees seeds, mnemonics or private keys — wallet tools return
public data only.

## Install

```bash
git clone https://github.com/nakpix/nakpix-mcp
cd nakpix-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Configure

```bash
cp .env.example .env   # fill NAKPIX_BACKEND_URL, SMTP, RPCs
```

Local data lives in `~/.nakpix/` (wallets encrypted with Argon2id + AES-256-GCM,
contacts, settings, SQLite DB, logs). Wallets are mode `0600`, dirs `0700`.

## Run

The MCP server runs over stdio and is launched by your agent client:

```bash
nakpix-mcp
```

The background services run independently (needed for scheduled/recurring
payments and the 15-minute incident monitor to work while no chat is open):

```bash
nakpix-scheduler    # executes due schedules (idempotent)
nakpix-incidents    # opens incidents + emails suporte@nakswap.xyz
```

Or with Docker:

```bash
docker compose up -d   # scheduler + incidents
```

## Wallet CLI (secure interface)

```bash
nakpix-wallet-agent status
nakpix-wallet-agent create polygon
nakpix-wallet-agent reveal polygon      # shows recovery words (terminal only)
nakpix-wallet-agent mark-backed-up polygon
```

Recovery words are **never** exposed through MCP/chat/logs — only through the
local CLI.

## Tools

`nakpix_wallet_status`, `nakpix_create_wallet`, `nakpix_import_wallet`,
`nakpix_get_wallet_address`, `nakpix_get_balances`, `nakpix_get_deposits`,
`nakpix_get_supported_networks`, `nakpix_get_wallet_backup_status`,
`nakpix_add_contact` (+ list/get/find/update/delete), `nakpix_set_default_coupon`
(+ get/remove/validate), `nakpix_validate_pix`, `nakpix_parse_pix`,
`nakpix_get_quote`, `nakpix_pay_pix`, `nakpix_get_payment_status`,
`nakpix_get_receipt`, `nakpix_get_payment_history`,
`nakpix_get_transaction_details`, `nakpix_schedule_payment`,
`nakpix_schedule_recurring_payment` (+ list/get/update/cancel/pause/resume/
history), `nakpix_get_payment_confirmation` (+ set), `nakpix_get_notifications`.

## Agent Skill

The companion agent skill lives at
[nakpix-agent-skill](https://github.com/nakpix/nakpix-agent-skill) and teaches
the agent the full flow (first-use wallet creation, confirmation, scheduling,
document reading, incident handling).

## License

MIT — see [LICENSE](LICENSE).
