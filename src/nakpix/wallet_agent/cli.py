"""``nakpix-wallet-agent`` CLI — the secure local wallet interface.

This is the ONLY place recovery words are ever shown. It is meant to be run
by the human owner in their own terminal, NOT through an MCP tool.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import manager


def _passphrase(args) -> str:
    if getattr(args, "passphrase", None):
        return args.passphrase
    return getpass.getpass("Passphrase de desbloqueio (não aparece na tela): ")


def cmd_status(args) -> int:
    wallets = manager.list_wallets()
    if not wallets:
        print("Nenhuma carteira configurada.")
        return 0
    for w in wallets:
        backed = "backup OK" if w["backed_up"] else "backup PENDENTE"
        print(f"{w['network']:8} {w['address']}  ({backed})")
    return 0


def cmd_create(args) -> int:
    info = manager.create(args.network, _passphrase(args))
    print(f"Carteira {info['network']} criada com sucesso!")
    print(f"Endereço público: {info['address']}")
    print("Guarde as palavras de recuperação com `nakpix-wallet-agent reveal "
          f"{args.network}` e marque o backup com `mark-backed-up`.")
    return 0


def cmd_import(args) -> int:
    info = manager.import_secret(args.network, args.secret, _passphrase(args))
    print(f"Carteira {info['network']} importada: {info['address']}")
    return 0


def cmd_address(args) -> int:
    print(manager.address(args.network))
    return 0


def cmd_reveal(args) -> int:
    print("⚠️  As palavras abaixo dão CONTROLE TOTAL sobre a carteira.", file=sys.stderr)
    print("   Nunca as envie por chat, e-mail ou log. Guarde offline.", file=sys.stderr)
    r = manager.reveal(args.network, _passphrase(args))
    print(f"Rede: {r['network']}")
    print(f"Endereço: {r['address']}")
    print(f"Caminho de derivação: {r['derivation_path']}")
    print(f"\nPalavras de recuperação:\n{r['mnemonic']}")
    print(f"\nChave privada (hex):\n{r['private_key']}")
    return 0


def cmd_mark_backed_up(args) -> int:
    manager.mark_backed_up(args.network)
    print(f"Backup de {args.network} marcado como concluído.")
    return 0


def cmd_unlock(args) -> int:
    manager.set_passphrase(_passphrase(args))
    print("Passphrase de desbloqueio salva (arquivo 0600).")
    return 0


def cmd_export(args) -> int:
    doc = manager.export_backup(args.network)
    print(json.dumps(doc, indent=2, ensure_ascii=False))
    return 0


def cmd_restore(args) -> int:
    doc = json.load(open(args.backup, encoding="utf-8"))
    info = manager.import_backup(args.network, doc, _passphrase(args))
    print(f"Carteira {info['network']} restaurada: {info['address']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nakpix-wallet-agent",
                                description="Gerenciador local de carteiras NAKPIX")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="lista carteiras (dados públicos)").set_defaults(fn=cmd_status)

    c = sub.add_parser("create", help="cria uma carteira")
    c.add_argument("network", choices=["polygon", "solana"])
    c.add_argument("--passphrase", help="passphrase (senão, pede interativamente)")
    c.set_defaults(fn=cmd_create)

    i = sub.add_parser("import", help="importa carteira (mnemonic ou private key)")
    i.add_argument("network", choices=["polygon", "solana"])
    i.add_argument("secret")
    i.add_argument("--passphrase")
    i.set_defaults(fn=cmd_import)

    a = sub.add_parser("address", help="mostra o endereço público")
    a.add_argument("network", choices=["polygon", "solana"])
    a.set_defaults(fn=cmd_address)

    r = sub.add_parser("reveal", help="[SEGURO] mostra palavras de recuperação")
    r.add_argument("network", choices=["polygon", "solana"])
    r.add_argument("--passphrase")
    r.set_defaults(fn=cmd_reveal)

    m = sub.add_parser("mark-backed-up", help="marca o backup como concluído")
    m.add_argument("network", choices=["polygon", "solana"])
    m.set_defaults(fn=cmd_mark_backed_up)

    u = sub.add_parser("unlock", help="grava a passphrase de desbloqueio")
    u.add_argument("--passphrase")
    u.set_defaults(fn=cmd_unlock)

    e = sub.add_parser("export", help="exporta o backup criptografado (JSON)")
    e.add_argument("network", choices=["polygon", "solana"])
    e.set_defaults(fn=cmd_export)

    rs = sub.add_parser("restore", help="restaura de um backup criptografado")
    rs.add_argument("network", choices=["polygon", "solana"])
    rs.add_argument("backup")
    rs.add_argument("--passphrase")
    rs.set_defaults(fn=cmd_restore)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except manager.WalletError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
