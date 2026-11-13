# AI News Automation Tool

RSS / Telegram / Twitter(X) → Gemini (Bengali rewrite) → Google Blogger.

```
ai_news_automation/
├── main.py                # orchestrator, CLI, scheduler, logging
├── config.py              # .env loading + validation
├── fetcher.py             # RSS (feedparser), Telegram (Telethon), Twitter (tweepy)
├── gemini_processor.py    # rewrite_with_gemini() using google-genai
├── blogger_publisher.py   # OAuth2 + Blogger API v3 publishing
├── state_store.py         # de-duplication / failure tracking (data/processed.json)
├── requirements.txt
└── .env.example
```

## 1. Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

## 2. Get your credentials

### A. Gemini API key
1. Open <https://aistudio.google.com/apikey> and sign in.
2. Click **Create API key** → copy it.
3. Put it in `.env` → `GEMINI_API_KEY=...`

### B. Telegram API (for Telethon)
1. Go to <https://my.telegram.org> and log in with your phone number.
2. Open **API development tools** → create an application (any name).
3. Copy **api_id** and **api_hash** into `.env` (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`).
4. List public channel usernames (without `@`) in `TELEGRAM_CHANNELS`.
5. Run the one-time login (asks for phone, login code, 2FA password if enabled):
   ```bash
   python main.py --telegram-login
   ```
   A session file is stored in `data/`. Treat it like a password. A secondary Telegram account is recommended.

### C. Google Blogger API (OAuth2)
1. Go to <https://console.cloud.google.com/> → create a **new project**.
2. **APIs & Services → Library** → search **Blogger API v3** → **Enable**.
3. **APIs & Services → OAuth consent screen** → User type **External** → fill in app name/email →
   under **Test users** add your own Google account.
   (In "Testing" status, refresh tokens expire after 7 days; set the app to **In production** to avoid
   re-authorising weekly. You will see an "unverified app" warning for your own use, which is fine.)
4. **Credentials → Create Credentials → OAuth client ID → Application type: Desktop app** →
   **Download JSON** → save it in the project folder as `client_secret.json`.
5. Find your **Blog ID**: open your blog in the Blogger dashboard; the URL looks like
   `https://www.blogger.com/blog/posts/1234567890123456789` → that number is the ID.
   Put it in `.env` → `BLOGGER_BLOG_ID=...`
6. Authorise once (opens a browser):
   ```bash
   python main.py --auth
   ```
   On a headless server, run this step on your own computer and copy `data/blogger_token.json` to the server.

### D. Twitter / X (optional)
1. Create a project/app at <https://developer.x.com> and generate a **Bearer Token**.
2. Set `TWITTER_BEARER_TOKEN` and `TWITTER_USERNAMES` in `.env`.
3. Note: X's free tier has very restrictive (or no) read access. If it doesn't work for you, leave this blank
   or replace `fetch_twitter()` in `fetcher.py` with another provider; just return `RawItem` objects.

## 3. Run

```bash
python main.py --dry-run     # fetch + generate, print results, publish nothing (Blogger not needed)
python main.py --once        # one full run (saves DRAFTS by default)
python main.py               # run now, then repeat every RUN_INTERVAL_MINUTES
```

Set `PUBLISH_AS_DRAFT=false` in `.env` only after you've reviewed a few drafts and are happy with the quality.

Logs: console + `data/app.log` (rotating). Processed items: `data/processed.json`.

### Run in the background (Linux)
```bash
nohup python main.py > /dev/null 2>&1 &
```
or create a `systemd` service with `ExecStart=/path/to/.venv/bin/python /path/to/main.py`.

## 4. Behaviour & safeguards
- **Dedup:** each source item is published once (`data/processed.json`).
- **Freshness:** items older than `ITEM_MAX_AGE_HOURS` are skipped, so first run doesn't post stale news.
- **Rate limits:** `MAX_POSTS_PER_RUN` and `DELAY_BETWEEN_POSTS_SECONDS` protect Gemini/Blogger quotas.
- **Retries:** Gemini and Blogger calls retry with backoff; an item failing `MAX_RETRIES_PER_ITEM` runs is skipped.
- **Isolation:** one failing source or item never stops the pipeline.
- **Output safety:** `<script>`, `<iframe>`, `on*` attributes are stripped; a source attribution link is appended.

## 5. Responsible use
Respect each source's terms of service and copyright. The tool rewrites (not copies) and credits the source,
but you remain responsible for what is published. Review drafts before going live, especially for sensitive news.

## 6. Troubleshooting
| Problem | Fix |
|---|---|
| `Blogger is not authorised` | Run `python main.py --auth` again (token expired/revoked). |
| `403 ... Blogger API has not been used` | Enable **Blogger API v3** in the same Cloud project. |
| `access_denied` on login | Add your account under **Test users** on the OAuth consent screen. |
| Telegram: `session not authorised` | Run `python main.py --telegram-login`. |
| Gemini 429 / quota | Lower `MAX_POSTS_PER_RUN`, raise the delay, or use a different model in `GEMINI_MODEL`. |
| Nothing gets published | Run `--dry-run` and check `data/app.log`; items may be duplicates or older than the age limit. |
