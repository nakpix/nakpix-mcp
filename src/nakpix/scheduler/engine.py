"""Scheduler entry point (``nakpix-scheduler``)."""

from __future__ import annotations

from . import run_loop


def main() -> None:
    print("[scheduler] ON — engine de agendamentos persistente", flush=True)
    run_loop()


if __name__ == "__main__":
    main()
