"""Local wallet agent — the ONLY component that touches secret material.

The MCP server never sees seeds, mnemonics or private keys. It calls this
package for generation, storage, recovery and signing; wallet tools expose
only public addresses to the LLM.
"""

__all__ = ["crypto", "polygon", "solana", "manager"]
