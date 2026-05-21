"""grail-bot: автоматизация реги grails.fancraze.com + follow X.

Usage:
  python grail_bot.py
  python grail_bot.py --start 5
  python grail_bot.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import random
import csv
import logging
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from accounts_loader import Account, load_accounts
from grail_runner import GrailFlowError, GrailResult, run_grail_flow, claim_x_follow
from twitter_client import TwitterLoginError
from twitter_session import authenticate, follow_handle
from referrals_manager import ReferralManager


log = logging.getLogger("grail-bot")


@dataclass
class Config:
    follow_handle: str
    grail_url: str
    delay_between_accounts_sec: int
    post_oauth_settle_sec: float
    oauth_callback_timeout_sec: float
    headless: bool
    profiles_dir: Path
    accounts_file: Path
    proxies_file: Path
    results_csv: Path
    log_file: Path
    email_strategy: str
    email_file: Path
    email_auto_domain: str
    follow_enabled: bool
    follow_timeout_sec: float
    base_referral_code: str | None
    ref_min_uses: int
    ref_max_uses: int
    delay_min_sec: int
    delay_max_sec: int
    referrals_state_file: Path


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.parent.resolve()
    # Локальный override (gitignored) — содержит base_code и прочие персональные настройки
    local_path = base / "referrals.local.toml"
    if local_path.exists():
        local_raw = tomllib.loads(local_path.read_text(encoding="utf-8"))
        for section, vals in local_raw.items():
            raw.setdefault(section, {}).update(vals)
    return Config(
        follow_handle=raw["targets"]["follow_handle"],
        grail_url=raw["targets"]["grail_url"],
        delay_between_accounts_sec=int(raw["timing"]["delay_between_accounts_sec"]),
        post_oauth_settle_sec=float(raw["timing"]["post_oauth_settle_sec"]),
        oauth_callback_timeout_sec=float(raw["timing"]["oauth_callback_timeout_sec"]),
        headless=bool(raw["browser"]["headless"]),
        profiles_dir=(base / raw["browser"]["profiles_dir"]).resolve(),
        accounts_file=(base / raw["files"]["accounts_file"]).resolve(),
        proxies_file=(base / raw["files"]["proxies_file"]).resolve(),
        results_csv=(base / raw["files"]["results_csv"]).resolve(),
        log_file=(base / raw["files"]["log_file"]).resolve(),
        email_strategy=raw["email"]["strategy"],
        email_file=(base / raw["email"].get("file", "emails.txt")).resolve(),
        email_auto_domain=raw["email"].get("auto_domain", "gmail.com"),
        follow_enabled=bool(raw["follow"]["enabled"]),
        follow_timeout_sec=float(raw["follow"].get("profile_load_timeout_sec", 25)),
        base_referral_code=raw.get("referrals", {}).get("base_code") or None,
        ref_min_uses=int(raw.get("referrals", {}).get("min_uses", 5)),
        ref_max_uses=int(raw.get("referrals", {}).get("max_uses", 10)),
        delay_min_sec=int(raw.get("timing", {}).get("delay_min_sec", 2400)),
        delay_max_sec=int(raw.get("timing", {}).get("delay_max_sec", 4800)),
        referrals_state_file=(base / raw.get("referrals", {}).get("state_file", "referrals.json")).resolve(),
    )


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s :: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger("patchright").setLevel(logging.WARNING)


def derive_email(account: Account, idx: int, cfg: Config, emails_from_file: list[str]) -> str | None:
    s = cfg.email_strategy
    if s == "skip":
        return None
    if s == "from_file":
        if idx < len(emails_from_file):
            return emails_from_file[idx].strip() or None
        log.warning("emails.txt too short, idx=%d", idx)
        return None
    if s == "auto_username":
        login = account.login_hint or f"acc{idx}"
        return f"{login}+grail{idx}@{cfg.email_auto_domain}"
    raise ValueError(f"unknown email strategy: {s!r}")


def load_emails_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@dataclass
class RowResult:
    idx: int
    login_hint: str | None
    proxy: str | None
    twitter_ok: bool
    grail_x_connected: bool
    grail_email_submitted: bool
    grail_final_url: str
    handle: str | None
    follow_ok: bool
    xfollow_claimed: bool
    error: str | None
    started_at: str
    finished_at: str


def _pick_session(account: Account, mode: str):
    """auto = cred если есть, иначе cookie. cred/cookie = принудительный выбор."""
    if mode == "cred":
        return account.cred
    if mode == "cookie":
        return account.cookie
    return account.cred or account.cookie


async def process_account(
    idx: int, account: Account, cfg: Config, email: str | None, mode: str,
    ref_code: str | None = None,
) -> "RowResult":
    started_at = datetime.now(timezone.utc).isoformat()
    proxy = account.proxy_url
    sess = _pick_session(account, mode)
    mode_used = "cred" if (sess is not None and hasattr(sess, "login")) else ("cookie" if sess else "none")
    log.info("[%d] start hint=%s proxy=%s mode=%s", idx, account.login_hint, proxy, mode_used)

    profile_dir = cfg.profiles_dir / f"acc-{idx:03d}"
    ctx = None
    err: str | None = None
    twitter_ok = False
    grail_res = GrailResult(False, False, "", None)
    follow_ok = False
    xfollow_claimed = False

    if sess is None:
        err = f"no {mode}-mode data on account"
        log.error("[%d] %s", idx, err)
        return RowResult(
            idx=idx, login_hint=account.login_hint, proxy=proxy,
            twitter_ok=False, grail_x_connected=False, grail_email_submitted=False,
            grail_final_url="", handle=None, follow_ok=False, xfollow_claimed=False,
            ref_used=ref_code, new_ref_code=None, error=err,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        try:
            ctx = await authenticate(sess, profile_dir, cfg.headless)
            twitter_ok = True
        except TwitterLoginError as e:
            err = f"twitter auth: {e}"
            log.error("[%d] %s", idx, err)

        if twitter_ok and ctx is not None:
            try:
                grail_res = await run_grail_flow(
                    ctx,
                    grail_url=cfg.grail_url,
                    email=email,
                    referral_code=ref_code,
                    oauth_callback_timeout_sec=cfg.oauth_callback_timeout_sec,
                    post_oauth_settle_sec=cfg.post_oauth_settle_sec,
                )
                log.info(
                    "[%d] grail x_connected=%s email=%s url=%s handle=%s",
                    idx, grail_res.x_connected, grail_res.email_submitted,
                    grail_res.final_url, grail_res.handle,
                )
            except GrailFlowError as e:
                err = (err + "; " if err else "") + f"grail: {e}"
                log.error("[%d] %s", idx, err)

        if twitter_ok and cfg.follow_enabled and ctx is not None:
            try:
                follow_ok = await follow_handle(
                    ctx, cfg.follow_handle, timeout_sec=cfg.follow_timeout_sec
                )
                log.info("[%d] follow @%s -> %s", idx, cfg.follow_handle, follow_ok)
            except Exception as e:
                err = (err + "; " if err else "") + f"follow: {e}"
                log.error("[%d] follow failed: %s", idx, e)

        if grail_res.x_connected and ctx is not None:
            try:
                xfollow_claimed = await claim_x_follow(ctx)
                log.info("[%d] xfollow_claim -> %s", idx, xfollow_claimed)
            except Exception as e:
                log.warning("[%d] xfollow_claim error: %s", idx, e)
    except Exception as e:
        err = (err + "; " if err else "") + f"unhandled: {e}"
        log.exception("[%d] unhandled error", idx)
    finally:
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass

    finished_at = datetime.now(timezone.utc).isoformat()
    return RowResult(
        idx=idx,
        login_hint=account.login_hint,
        proxy=proxy,
        twitter_ok=twitter_ok,
        grail_x_connected=grail_res.x_connected,
        grail_email_submitted=grail_res.email_submitted,
        grail_final_url=grail_res.final_url,
        handle=grail_res.handle,
        follow_ok=follow_ok,
        xfollow_claimed=xfollow_claimed,
        ref_used=ref_code,
        new_ref_code=grail_res.new_referral_code,
        error=err,
        started_at=started_at,
        finished_at=finished_at,
    )


def append_csv(path: Path, row: RowResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow([
                "idx", "login_hint", "proxy", "twitter_ok",
                "grail_x_connected", "grail_email_submitted",
                "grail_final_url", "handle", "follow_ok", "xfollow_claimed",
                "error", "started_at", "finished_at",
            ])
        w.writerow([
            row.idx, row.login_hint or "", row.proxy or "",
            int(row.twitter_ok),
            int(row.grail_x_connected),
            int(row.grail_email_submitted),
            row.grail_final_url, row.handle or "",
            int(row.follow_ok), int(row.xfollow_claimed),
            row.ref_used or "", row.new_ref_code or "",
            row.error or "",
            row.started_at, row.finished_at,
        ])


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    setup_logging(cfg.log_file)
    log.info("grail-bot start, config=%s", args.config)

    proxies_path = Path(args.proxies_file).resolve() if args.proxies_file else cfg.proxies_file
    accounts = load_accounts(cfg.accounts_file, proxies_path)
    if args.no_proxy:
        for a in accounts:
            a.proxy_url = None
            if a.cred:
                a.cred.proxy_url = None
            if a.cookie:
                a.cookie.proxy_url = None
        log.info("--no-proxy: stripping proxies from all accounts")
    cred_n = sum(1 for a in accounts if a.cred)
    cookie_n = sum(1 for a in accounts if a.cookie)
    log.info("loaded %d accounts (cred=%d, cookie=%d) from %s (proxies=%s)",
             len(accounts), cred_n, cookie_n, cfg.accounts_file,
             "none" if args.no_proxy else proxies_path)
    if args.mode == "cred":
        accounts = [a for a in accounts if a.cred is not None]
        log.info("--mode cred: filtered to %d acc with cred data", len(accounts))
    elif args.mode == "cookie":
        accounts = [a for a in accounts if a.cookie is not None]
        log.info("--mode cookie: filtered to %d acc with cookie data", len(accounts))
    if not accounts:
        log.error("no accounts to process - check %s", cfg.accounts_file)
        return 1

    emails_from_file = load_emails_file(cfg.email_file) if cfg.email_strategy == "from_file" else []

    start_idx = max(0, args.start)
    if args.limit and args.limit > 0:
        end_idx = min(len(accounts), start_idx + args.limit)
    else:
        end_idx = len(accounts)

    log.info("will process accounts [%d..%d) of %d", start_idx, end_idx, len(accounts))

    ref_mgr = ReferralManager(
        state_file=cfg.referrals_state_file,
        base_code=cfg.base_referral_code,
        min_uses=cfg.ref_min_uses,
        max_uses=cfg.ref_max_uses,
    )
    log.info("referrals: %s", ref_mgr.stats())

    for i in range(start_idx, end_idx):
        acc = accounts[i]
        email = derive_email(acc, i, cfg, emails_from_file)
        ref_for_this = ref_mgr.next_code()
        log.info("=== account %d/%d  hint=%s email=%s ref=%s ===",
                 i + 1, end_idx, acc.login_hint, email, ref_for_this)

        if args.dry_run:
            log.info("[%d] dry-run: would process %s ref=%s",
                     i, acc.login_hint, ref_for_this)
            continue

        result = await process_account(i, acc, cfg, email, args.mode, ref_code=ref_for_this)
        append_csv(cfg.results_csv, result)

        if result.grail_x_connected and ref_for_this:
            ref_mgr.mark_used(ref_for_this)
        if result.new_ref_code:
            ref_mgr.add_new(result.new_ref_code)

        if i + 1 < end_idx:
            if args.skip_delay_on_fail and not result.twitter_ok:
                log.info("twitter_ok=False, skipping delay")
                continue
            delay = random.randint(cfg.delay_min_sec, cfg.delay_max_sec)
            log.info("sleeping %d sec (%.1f min) before next account",
                     delay, delay / 60)
            await asyncio.sleep(delay)

    log.info("done")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="grail-bot")
    p.add_argument("--config", default="config.toml", help="path to config.toml")
    p.add_argument("--start", type=int, default=0, help="start at account index")
    p.add_argument("--limit", type=int, default=0, help="how many accounts (0=all)")
    p.add_argument("--dry-run", action="store_true", help="parse only, no real flow")
    p.add_argument("--no-proxy", action="store_true", help="ignore all proxies (use direct IP)")
    p.add_argument("--proxies-file", default=None, help="override proxies_file from config")
    p.add_argument("--skip-delay-on-fail", action="store_true",
                   help="не ждать 180с если twitter_ok=False")
    p.add_argument("--mode", choices=["auto", "cred", "cookie"], default="auto",
                   help="auto=cred если есть данные, иначе cookie; cred/cookie=принудительно")
    args = p.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
