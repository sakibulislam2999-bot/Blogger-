"""Central configuration. All values come from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def _bool(name: str, default: bool) -> bool:
    return (os.getenv(name) or str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else BASE_DIR / p


@dataclass(frozen=True)
class Settings:
    # Gemini
    gemini_api_key: str
    gemini_model: str
    # Sources
    rss_feeds: list[str]
    telegram_channels: list[str]
    twitter_usernames: list[str]
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: Path
    twitter_bearer_token: str
    # Blogger
    blogger_blog_id: str
    blogger_client_secrets: Path
    blogger_token_file: Path
    publish_as_draft: bool
    # Pipeline
    run_interval_minutes: int
    fetch_limit_per_source: int
    max_posts_per_run: int
    item_max_age_hours: int
    min_text_length: int
    max_retries_per_item: int
    delay_between_posts: int
    # Paths / logging
    data_dir: Path
    state_file: Path
    log_file: Path
    log_level: str

    def validate(self, *, need_gemini: bool = True, need_blogger: bool = True) -> list[str]:
        """Return a list of human-readable configuration problems."""
        problems: list[str] = []
        if need_gemini and not self.gemini_api_key:
            problems.append("GEMINI_API_KEY is missing.")
        if need_blogger and not self.blogger_blog_id:
            problems.append("BLOGGER_BLOG_ID is missing.")
        if need_blogger and not self.blogger_client_secrets.exists() and not self.blogger_token_file.exists():
            problems.append(f"Blogger OAuth client file not found: {self.blogger_client_secrets}")
        if self.telegram_channels and not (self.telegram_api_id and self.telegram_api_hash):
            problems.append("TELEGRAM_CHANNELS set but TELEGRAM_API_ID / TELEGRAM_API_HASH missing.")
        if self.twitter_usernames and not self.twitter_bearer_token:
            problems.append("TWITTER_USERNAMES set but TWITTER_BEARER_TOKEN missing.")
        if not (self.rss_feeds or self.telegram_channels or self.twitter_usernames):
            problems.append("No sources configured (RSS_FEEDS / TELEGRAM_CHANNELS / TWITTER_USERNAMES).")
        return problems


def load_settings() -> Settings:
    data_dir = _path("DATA_DIR", "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=(os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip(),
        rss_feeds=_list("RSS_FEEDS"),
        telegram_channels=_list("TELEGRAM_CHANNELS"),
        twitter_usernames=[u.lstrip("@") for u in _list("TWITTER_USERNAMES")],
        telegram_api_id=_int("TELEGRAM_API_ID", 0),
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
        telegram_session=data_dir / "telegram",  # Telethon appends ".session"
        twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", "").strip(),
        blogger_blog_id=os.getenv("BLOGGER_BLOG_ID", "").strip(),
        blogger_client_secrets=_path("BLOGGER_CLIENT_SECRETS", "client_secret.json"),
        blogger_token_file=_path("BLOGGER_TOKEN_FILE", "data/blogger_token.json"),
        publish_as_draft=_bool("PUBLISH_AS_DRAFT", True),
        run_interval_minutes=max(1, _int("RUN_INTERVAL_MINUTES", 60)),
        fetch_limit_per_source=max(1, _int("FETCH_LIMIT_PER_SOURCE", 10)),
        max_posts_per_run=max(1, _int("MAX_POSTS_PER_RUN", 3)),
        item_max_age_hours=_int("ITEM_MAX_AGE_HOURS", 24),
        min_text_length=_int("MIN_TEXT_LENGTH", 80),
        max_retries_per_item=max(1, _int("MAX_RETRIES_PER_ITEM", 3)),
        delay_between_posts=max(0, _int("DELAY_BETWEEN_POSTS_SECONDS", 10)),
        data_dir=data_dir,
        state_file=data_dir / "processed.json",
        log_file=data_dir / "app.log",
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    )


settings = load_settings()
