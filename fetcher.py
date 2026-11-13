"""Data ingestion: RSS, Telegram public channels (Telethon) and Twitter/X (API v2)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser
from bs4 import BeautifulSoup

from config import Settings

logger = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; AINewsBot/1.0)"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class RawItem:
    """Normalised unit of raw content, regardless of source."""
    source_type: str          # "rss" | "telegram" | "twitter"
    source_name: str          # feed title / channel / @user
    uid: str                  # globally unique id used for de-duplication
    text: str
    url: str = ""
    title: str = ""
    published: Optional[datetime] = None

    def full_text(self) -> str:
        return f"{self.title}\n\n{self.text}".strip() if self.title else self.text


def clean_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ------------------------------------------------------------------ RSS
def fetch_rss(feed_urls: list[str], limit: int) -> list[RawItem]:
    items: list[RawItem] = []
    for url in feed_urls:
        try:
            feed = feedparser.parse(url, agent=USER_AGENT)
            if feed.bozo and not feed.entries:
                logger.warning("RSS feed unreadable: %s (%s)", url, feed.get("bozo_exception"))
                continue
            source = feed.feed.get("title", url)
            for entry in feed.entries[:limit]:
                link = entry.get("link", "")
                body = ""
                if entry.get("content"):
                    body = entry["content"][0].get("value", "")
                body = body or entry.get("summary", "") or entry.get("description", "")
                published = None
                if entry.get("published_parsed"):
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                items.append(RawItem(
                    source_type="rss",
                    source_name=source,
                    uid=f"rss:{entry.get('id') or link}",
                    title=clean_text(entry.get("title", "")),
                    text=clean_text(body),
                    url=link,
                    published=published,
                ))
            logger.info("RSS %s -> %d entries", url, min(len(feed.entries), limit))
        except Exception:
            logger.exception("RSS fetch failed for %s", url)
    return items


# ------------------------------------------------------------- Telegram
def _normalise_channel(name: str) -> str:
    return re.sub(r"^(https?://)?(t\.me/)?@?", "", name.strip()).strip("/")


async def _fetch_telegram_async(s: Settings) -> list[RawItem]:
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError

    items: list[RawItem] = []
    client = TelegramClient(str(s.telegram_session), s.telegram_api_id, s.telegram_api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            logger.error("Telegram session not authorised. Run: python main.py --telegram-login")
            return items
        for raw_name in s.telegram_channels:
            channel = _normalise_channel(raw_name)
            try:
                async for msg in client.iter_messages(channel, limit=s.fetch_limit_per_source):
                    text = (msg.message or "").strip()
                    if len(text) < s.min_text_length:
                        continue
                    published = msg.date.astimezone(timezone.utc) if msg.date else None
                    items.append(RawItem(
                        source_type="telegram",
                        source_name=f"@{channel}",
                        uid=f"tg:{channel}:{msg.id}",
                        text=text,
                        url=f"https://t.me/{channel}/{msg.id}",
                        published=published,
                    ))
                logger.info("Telegram @%s fetched", channel)
            except FloodWaitError as exc:
                logger.warning("Telegram flood wait (%ss) on @%s; skipping", exc.seconds, channel)
            except Exception:
                logger.exception("Telegram fetch failed for @%s", channel)
    finally:
        await client.disconnect()
    return items


def fetch_telegram(s: Settings) -> list[RawItem]:
    """Sync wrapper around the Telethon async client."""
    return asyncio.run(_fetch_telegram_async(s))


def telegram_interactive_login(s: Settings) -> None:
    """One-time interactive login (phone number + code). Creates the session file."""
    from telethon import TelegramClient

    async def _login() -> None:
        client = TelegramClient(str(s.telegram_session), s.telegram_api_id, s.telegram_api_hash)
        await client.start()  # prompts for phone / code / 2FA password
        me = await client.get_me()
        print(f"Telegram login OK as: {me.first_name} (@{me.username})")
        await client.disconnect()

    asyncio.run(_login())


# -------------------------------------------------------------- Twitter
def fetch_twitter(s: Settings) -> list[RawItem]:
    """Fetch recent tweets via the official X API v2 (needs a bearer token).

    NOTE: X's free tier has very tight read limits (or none). To use another
    provider (e.g. an RSS bridge), just return RawItem objects from here.
    """
    import tweepy

    items: list[RawItem] = []
    client = tweepy.Client(bearer_token=s.twitter_bearer_token, wait_on_rate_limit=False)
    for username in s.twitter_usernames:
        try:
            user = client.get_user(username=username)
            if not user.data:
                logger.warning("Twitter user not found: %s", username)
                continue
            resp = client.get_users_tweets(
                user.data.id,
                max_results=max(5, min(s.fetch_limit_per_source, 100)),
                exclude=["retweets", "replies"],
                tweet_fields=["created_at"],
            )
            for tw in resp.data or []:
                if len(tw.text) < s.min_text_length:
                    continue
                items.append(RawItem(
                    source_type="twitter",
                    source_name=f"@{username}",
                    uid=f"tw:{tw.id}",
                    text=tw.text,
                    url=f"https://x.com/{username}/status/{tw.id}",
                    published=tw.created_at,
                ))
            logger.info("Twitter @%s fetched", username)
        except tweepy.TweepyException:
            logger.exception("Twitter fetch failed for @%s", username)
    return items


# ----------------------------------------------------------- Aggregator
def fetch_all(s: Settings) -> list[RawItem]:
    """Fetch from every configured source; one failing source never stops the rest."""
    items: list[RawItem] = []
    if s.rss_feeds:
        items += fetch_rss(s.rss_feeds, s.fetch_limit_per_source)
    if s.telegram_channels and s.telegram_api_id:
        try:
            items += fetch_telegram(s)
        except Exception:
            logger.exception("Telegram ingestion crashed")
    if s.twitter_usernames and s.twitter_bearer_token:
        try:
            items += fetch_twitter(s)
        except Exception:
            logger.exception("Twitter ingestion crashed")

    unique = {i.uid: i for i in items if len(i.full_text()) >= s.min_text_length}
    ordered = sorted(unique.values(), key=lambda i: i.published or _EPOCH, reverse=True)
    logger.info("Fetched %d usable items in total", len(ordered))
    return ordered
