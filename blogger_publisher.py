"""Publish posts to Google Blogger (API v3) using OAuth2."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import settings
from gemini_processor import BlogPost

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/blogger"]


class BloggerError(RuntimeError):
    pass


def _save_token(creds: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def get_credentials(interactive: bool = False) -> Credentials:
    """Load/refresh stored OAuth credentials; optionally launch the browser flow."""
    token_path = settings.blogger_token_file
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except RefreshError as exc:
            logger.warning("Blogger token refresh failed: %s", exc)

    if not interactive:
        raise BloggerError("Blogger is not authorised (or token expired). Run: python main.py --auth")
    if not settings.blogger_client_secrets.exists():
        raise BloggerError(f"OAuth client file missing: {settings.blogger_client_secrets}")

    flow = InstalledAppFlow.from_client_secrets_file(str(settings.blogger_client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds, token_path)
    logger.info("Blogger authorisation saved to %s", token_path)
    return creds


def _service():
    return build("blogger", "v3", credentials=get_credentials(), cache_discovery=False)


def publish_post(post: BlogPost, *, as_draft: bool | None = None, retries: int = 3) -> dict:
    """Insert a post. Returns {"id", "url", "status"}. Raises BloggerError on failure."""
    as_draft = settings.publish_as_draft if as_draft is None else as_draft
    body = {
        "kind": "blogger#post",
        "title": post.title,
        "content": post.content_html,
        "labels": post.tags,
    }
    service = _service()
    for attempt in range(1, retries + 1):
        try:
            result = service.posts().insert(
                blogId=settings.blogger_blog_id, body=body, isDraft=as_draft
            ).execute()
            info = {"id": result.get("id"), "url": result.get("url", ""),
                    "status": result.get("status", "DRAFT" if as_draft else "LIVE")}
            logger.info("Blogger post created: %s [%s]", info["url"] or info["id"], info["status"])
            return info
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            retryable = status in (429, 500, 502, 503, 504)
            logger.warning("Blogger API error %s (attempt %d/%d)", status, attempt, retries)
            if not retryable or attempt == retries:
                raise BloggerError(f"Blogger API error {status}: {exc}") from exc
            time.sleep(2 ** attempt * 2)
        except Exception as exc:
            raise BloggerError(f"Unexpected Blogger error: {exc}") from exc
    raise BloggerError("Publishing failed")
