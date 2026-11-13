"""Orchestrator: fetch -> rewrite with Gemini -> publish to Blogger, on a schedule."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

import schedule

from blogger_publisher import BloggerError, get_credentials, publish_post
from config import settings
from fetcher import RawItem, fetch_all, telegram_interactive_login
from gemini_processor import GeminiError, rewrite_with_gemini
from state_store import StateStore

logger = logging.getLogger("ai_news")
_running = True


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(settings.log_file, maxBytes=2_000_000,
                                       backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file_handler)
    for noisy in ("httpx", "httpcore", "googleapiclient", "telethon", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _is_fresh(item: RawItem) -> bool:
    if settings.item_max_age_hours <= 0 or item.published is None:
        return True
    return item.published >= datetime.now(timezone.utc) - timedelta(hours=settings.item_max_age_hours)


def run_pipeline(store: StateStore, dry_run: bool = False) -> None:
    logger.info("=== Pipeline run started (dry_run=%s) ===", dry_run)
    try:
        items = fetch_all(settings)
    except Exception:
        logger.exception("Fetching crashed; aborting this run")
        return

    candidates = [i for i in items if not store.seen(i.uid) and _is_fresh(i)]
    logger.info("%d new items, processing up to %d", len(candidates), settings.max_posts_per_run)

    published = 0
    for item in candidates:
        if published >= settings.max_posts_per_run or not _running:
            break
        logger.info("Processing [%s] %s", item.source_name, (item.title or item.text[:60]).replace("\n", " "))
        try:
            post = rewrite_with_gemini(item.full_text(), source_url=item.url,
                                       source_name=item.source_name)
            if dry_run:
                logger.info("DRY RUN | title=%s | tags=%s", post.title, post.tags)
                print(f"\n--- {post.title} ---\n{post.content_html}\nTags: {post.tags}\n")
            else:
                result = publish_post(post)
                store.mark_done(item.uid, title=post.title, blogger_id=result["id"],
                                url=result["url"], status=result["status"])
            published += 1
        except (GeminiError, BloggerError) as exc:
            logger.error("Item failed: %s", exc)
            _handle_failure(store, item, dry_run)
        except Exception:
            logger.exception("Unexpected error on item %s", item.uid)
            _handle_failure(store, item, dry_run)

        if published < settings.max_posts_per_run and settings.delay_between_posts:
            time.sleep(settings.delay_between_posts)  # be gentle with API quotas

    logger.info("=== Pipeline run finished: %d post(s) %s ===", published,
                "generated" if dry_run else "created")


def _handle_failure(store: StateStore, item: RawItem, dry_run: bool) -> None:
    if dry_run:
        return
    count = store.record_failure(item.uid)
    if count >= settings.max_retries_per_item:
        logger.error("Giving up on %s after %d failures", item.uid, count)
        store.mark_done(item.uid, status="FAILED")


def _stop(*_):
    global _running
    _running = False
    logger.info("Shutdown requested...")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI News Automation Tool")
    parser.add_argument("--once", action="store_true", help="run the pipeline once and exit")
    parser.add_argument("--dry-run", action="store_true", help="generate posts but do not publish")
    parser.add_argument("--auth", action="store_true", help="authorise Google Blogger (browser)")
    parser.add_argument("--telegram-login", action="store_true", help="one-time Telegram login")
    args = parser.parse_args()

    setup_logging()

    if args.auth:
        get_credentials(interactive=True)
        print("Blogger authorisation complete.")
        return 0
    if args.telegram_login:
        telegram_interactive_login(settings)
        return 0

    problems = settings.validate(need_blogger=not args.dry_run)
    if problems:
        for p in problems:
            logger.error("Config problem: %s", p)
        return 1

    store = StateStore(settings.state_file)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    run_pipeline(store, dry_run=args.dry_run)
    if args.once or args.dry_run:
        return 0

    schedule.every(settings.run_interval_minutes).minutes.do(run_pipeline, store=store)
    logger.info("Scheduler active: every %d minute(s). Press Ctrl+C to stop.",
                settings.run_interval_minutes)
    while _running:
        try:
            schedule.run_pending()
        except Exception:
            logger.exception("Scheduled run crashed; will retry next interval")
        time.sleep(1)
    logger.info("Stopped cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
