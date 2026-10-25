#!/usr/bin/env bash
# NAKPIX MCP — instalação turnkey (roda em 1 comando).
# Instala o pacote, cria o .env e imprime o bloco de config pra colar no agente.
set -e

echo "== NAKPIX MCP — instalando =="

INSTALL_DIR="${NAKPIX_INSTALL_DIR:-/opt/nakpix-mcp}"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone https://github.com/nakpix/nakpix-mcp "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

pip install -e . >/dev/null 2>&1 || pip install --break-system-packages -e . >/dev/null 2>&1

if [ ! -f .env ]; then
  cat > .env <<'EOF'
NAKPIX_BACKEND_URL=https://nakpix.xyz/relay
NAKPIX_BACKEND_POLYGON=https://nakpix.xyz/relay
NAKPIX_BACKEND_SOLANA=https://nakpix.xyz/relay-sol-prod
NAKPIX_HOME=~/.nakpix
EOF
  echo "criado .env em $INSTALL_DIR/.env"
fi

echo
echo "== COLE ISTO NO CONFIG DO SEU AGENTE (mcpServers) =="
cat <<'EOF'
{
  "mcpServers": {
    "nakpix": {
      "command": "nakpix-mcp",
      "env": {
        "NAKPIX_BACKEND_URL": "https://nakpix.xyz/relay",
        "NAKPIX_BACKEND_SOLANA": "https://nakpix.xyz/relay-sol-prod"
      }
    }
  }
}
EOF
echo
echo "== PRONTO =="
echo "1. reinicie o agente (pra ele reconectar o MCP)"
echo "2. instale a skill: https://github.com/nakpix/nakpix-agent-skill"
echo "3. no primeiro Pix, o agente pede pra criar a carteira (Polygon ou Solana)"
