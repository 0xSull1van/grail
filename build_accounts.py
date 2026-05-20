"""Собирает accounts.txt из источников в N:/Base/Projects/evm/ardinals-orchestrator.

Использование:
  python build_accounts.py                       # по умолчанию: Order + work1 + work2 = 125
  python build_accounts.py --sources work1 work2 # custom набор
  python build_accounts.py --all                 # все 5 файлов (195 уникальных)
  python build_accounts.py --list                # показать счётчики по файлам и комбинациям

Дедуп по auth_token, приоритет первого вхождения (порядок --sources важен).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

EVM = Path("N:/Base/Projects/evm/ardinals-orchestrator")
ALL_SOURCES = ["cookies.txt", "Order26297831.txt", "newtwitters.txt", "work1.txt", "work2.txt"]
DEFAULT_SOURCES = ["Order26297831.txt", "work1.txt", "work2.txt"]

AUTH_RE = re.compile(r"auth_token=([0-9a-fA-F]{30,})")


def _read(name: str) -> list[tuple[str, str]]:
    """Возвращает [(auth_token, raw_line), ...] из файла."""
    p = EVM / name
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = AUTH_RE.search(line)
        if m:
            out.append((m.group(1).lower(), line.rstrip()))
    return out


def _counts() -> None:
    print("Счётчики по файлам:")
    per = {}
    for f in ALL_SOURCES:
        toks = {t for t, _ in _read(f)}
        per[f] = toks
        print(f"  {f}: {len(toks)}")
    print(f"\nВсе 5 файлов вместе (дедуп): {len(set().union(*per.values()))}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", nargs="+", default=None,
                   help=f"Файлы из {EVM}. По умолчанию: {DEFAULT_SOURCES}")
    p.add_argument("--all", action="store_true", help="Все 5 файлов")
    p.add_argument("--list", action="store_true", help="Показать счётчики и выйти")
    p.add_argument("--out", default="N:/Base/Projects/grail/accounts.txt")
    args = p.parse_args()

    if args.list:
        _counts()
        return 0

    if args.all:
        sources = ALL_SOURCES
    elif args.sources:
        sources = args.sources
    else:
        sources = DEFAULT_SOURCES

    seen: dict[str, str] = {}
    counts: dict[str, tuple[int, int]] = {}
    for src in sources:
        added = total = 0
        for token, line in _read(src):
            total += 1
            if token in seen:
                continue
            seen[token] = line
            added += 1
        counts[src] = (added, total)

    print(f"Источники из {EVM}:")
    for name, (a, t) in counts.items():
        print(f"  {name}: +{a} / {t}")
    print(f"\nИтого уникальных: {len(seen)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(seen.values()) + "\n", encoding="utf-8")
    print(f"Записано в {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
