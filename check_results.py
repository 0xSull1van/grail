"""Проверка результатов прогона grail-бота.

Читает results.csv и выводит сводку:
- сколько акков прошло полный флоу
- сколько упало и на каком шаге
- у каких xfollow_claimed = True (получили 25 очков)

Usage:
    python check_results.py
    python check_results.py --failed     # показать только неуспешные
    python check_results.py --ok         # показать только успешные
    python check_results.py --summary    # только итоговые цифры
"""

from __future__ import annotations
import argparse
import csv
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--failed", action="store_true")
    p.add_argument("--ok", action="store_true")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--csv", default="results.csv")
    args = p.parse_args()

    path = Path(args.csv)
    if not path.exists():
        print(f"[!] {path} not found. Run grail_bot.py first.")
        sys.exit(1)

    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        print("[!] results.csv is empty")
        sys.exit(1)

    def b(val: str) -> bool:
        return val.strip() in ("1", "True", "true")

    total = len(rows)
    ok_twitter = [r for r in rows if b(r.get("twitter_ok", "0"))]
    ok_grail   = [r for r in rows if b(r.get("grail_x_connected", "0"))]
    ok_email   = [r for r in rows if b(r.get("grail_email_submitted", "0"))]
    ok_follow  = [r for r in rows if b(r.get("follow_ok", "0"))]
    ok_claim   = [r for r in rows if b(r.get("xfollow_claimed", "0"))]
    full_ok    = [r for r in rows
                  if b(r.get("twitter_ok","0")) and b(r.get("grail_x_connected","0"))
                  and b(r.get("follow_ok","0"))]

    print(f"=== Results: {path} ===")
    print(f"  Всего акков в CSV:        {total}")
    print(f"  Twitter login OK:         {len(ok_twitter)}")
    print(f"  Grail X connected:        {len(ok_grail)}")
    print(f"  Email submitted:          {len(ok_email)}")
    print(f"  Follow @fcgrails:         {len(ok_follow)}")
    print(f"  xFollow claimed (25pts):  {len(ok_claim)}")
    print(f"  Полный флоу:              {len(full_ok)}")
    print(f"  Провалились:              {total - len(full_ok)}")
    print()

    if args.summary:
        return

    # Таблица
    header = f"{'idx':>4}  {'login':20}  {'tw':3}  {'grail':5}  {'email':5}  {'fol':3}  {'clm':3}  {'url':30}  error"
    print(header)
    print("-" * len(header))

    for r in rows:
        idx   = r.get("idx", "?")
        login = (r.get("login_hint") or "")[:20]
        tw    = "OK" if b(r.get("twitter_ok","0")) else "NO"
        grail = "OK" if b(r.get("grail_x_connected","0")) else "NO"
        email = "OK" if b(r.get("grail_email_submitted","0")) else "--"
        fol   = "OK" if b(r.get("follow_ok","0")) else "NO"
        clm   = "OK" if b(r.get("xfollow_claimed","0")) else "--"
        url   = (r.get("grail_final_url") or "")[-30:]
        err   = (r.get("error") or "")[:60]

        is_ok = tw == "OK" and grail == "OK" and fol == "OK"

        if args.failed and is_ok:
            continue
        if args.ok and not is_ok:
            continue

        status = "V" if is_ok else "X"
        print(f"[{status}] {idx:>4}  {login:20}  {tw:3}  {grail:5}  {email:5}  {fol:3}  {clm:3}  {url:30}  {err}")


if __name__ == "__main__":
    main()
