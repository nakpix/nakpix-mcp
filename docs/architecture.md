# Architecture

High-level integration overview. The financial backend is private; this
document describes only the client-side contract, not internal infrastructure.

## Components

```
agent (LLM)
   │  MCP (stdio)
   ▼
nakpix-mcp server
   ├── nakpix.wallet_agent   LOCAL component (keys stay on the machine)
   │      └─ ~/.nakpix/wallets/*.json   (Argon2id + AES-256-GCM)
   ├── nakpix.api            HTTP client to the NAKPIX backend
   ├── nakpix.blockchain     read-only balance/deposit queries
   ├── nakpix.storage        SQLite (schedules, history, incidents, notifications)
   ├── nakpix.scheduler      background process (scheduled/recurring PIX)
   └── nakpix.incidents      15-min unresolved-payment monitor + email
```

## Integration contract (reused, not reimplemented)

The MCP reuses the backend's processing for: order creation, quoting, coupon
validation, PIX execution, status and receipts. It adds only the agent-side
experience: local wallets, contacts, coupons, scheduling, document reading,
and incident handling.

## Cost rule (fixed, house rule)

Costs are computed by the backend — the MCP never knows or exposes internal
costs. A coupon's `pct` is the fee applied to the liquid PIX amount.

## Networks

- Polygon: USDT funds payments, POL pays gas.
- Solana: USDT funds payments, SOL pays gas.

## Secrets

See SECURITY.md. Seeds/mnemonics/private keys never leave the wallet-agent.
