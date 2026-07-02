# Discord News Monitor

Replaces X/Twitter as a news source. Polls RSS feeds (Google News topic
searches, Reddit, The Real Deal) every 15 minutes and pushes new headlines to
Discord channels via webhooks. Discord's mobile app then gives you push
notifications — no X account needed.

## One-time setup (~2 minutes)

1. In Discord: create a server (or use an existing one) and 1–2 channels,
   e.g. `#real-estate-news` and `#breaking-news`.
2. For each channel: **Server Settings → Integrations → Webhooks → New
   Webhook** → pick the channel → **Copy Webhook URL**.
3. Paste the URLs into `.env` in this folder.
4. Install the schedule (runs every 15 min, survives reboots):

   ```bash
   cp ~/Jagex/discord-news-monitor/com.gadura.discord-news.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.gadura.discord-news.plist
   ```

5. On your phone: long-press each channel → **Notification Settings →
   All Messages** so every headline pushes.

## Test it manually

```bash
python3 ~/Jagex/discord-news-monitor/monitor.py
```

First run posts the latest items; after that only genuinely new headlines post
(dedupe state lives in `seen.json`).

## Change topics

Edit `config.json`. Each topic = a name + webhook env var + list of RSS URLs.
Google News RSS pattern (make a feed out of ANY search):

```
https://news.google.com/rss/search?q=YOUR+SEARCH+TERMS&hl=en-US&gl=US&ceid=US:en
```

Reddit pattern: `https://www.reddit.com/r/SUBREDDIT/top/.rss?t=hour`

`max_items_per_run` (default 5 per topic per run) prevents channel flooding;
`max_age_hours` (default 24) skips stale backlog items.

## Following specific X accounts (optional, costs money)

There is no reliable free way to mirror individual X accounts since the API
went paid. If you ever need a specific account:
- rss.app (~$10/mo) turns an X profile into an RSS URL — just add it to a
  topic's `feeds` list here, no code changes.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.gadura.discord-news.plist
rm ~/Library/LaunchAgents/com.gadura.discord-news.plist
```
