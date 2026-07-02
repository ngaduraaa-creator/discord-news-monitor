#!/usr/bin/env python3
"""RSS -> Discord webhook news monitor.

Reads topics/feeds from config.json, posts new items to Discord webhooks,
and remembers what it already posted in seen.json. Stdlib only.

Webhook URLs live in .env (never in this file or config.json):
    DISCORD_WEBHOOK_REALESTATE=https://discord.com/api/webhooks/...
    DISCORD_WEBHOOK_NEWS=https://discord.com/api/webhooks/...
"""

import json
import hashlib
import os
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
ENV_PATH = BASE_DIR / ".env"
SEEN_PATH = BASE_DIR / "seen.json"
LOG_PATH = BASE_DIR / "monitor.log"

SEEN_LIMIT = 3000  # keep the newest N ids so seen.json never grows unbounded
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        with LOG_PATH.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_seen() -> list:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            log("WARN seen.json unreadable, starting fresh")
    return []


def save_seen(seen: list) -> None:
    SEEN_PATH.write_text(json.dumps(seen[-SEEN_LIMIT:]))


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def text_of(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(raw: bytes) -> list:
    """Return [{id, title, link, published}] from RSS 2.0 or Atom bytes."""
    root = ET.fromstring(raw)
    items = []

    for item in root.iter("item"):  # RSS 2.0
        title = text_of(item.find("title"))
        creator = text_of(item.find("{http://purl.org/dc/elements/1.1/}creator"))
        if creator:  # nitter feeds: prefix tweet text with the account handle
            title = f"{creator}: {title}"
        link = text_of(item.find("link"))
        guid = text_of(item.find("guid")) or link or title
        published = None
        pub = text_of(item.find("pubDate"))
        if pub:
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                pass
        items.append({"id": guid, "title": title, "link": link, "published": published})

    if not items:  # Atom (e.g. Reddit)
        for entry in root.iter(f"{ATOM_NS}entry"):
            title = text_of(entry.find(f"{ATOM_NS}title"))
            link_el = entry.find(f"{ATOM_NS}link")
            link = link_el.get("href", "") if link_el is not None else ""
            guid = text_of(entry.find(f"{ATOM_NS}id")) or link or title
            published = None
            stamp = text_of(entry.find(f"{ATOM_NS}published")) or text_of(
                entry.find(f"{ATOM_NS}updated")
            )
            if stamp:
                try:
                    published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    pass
            items.append({"id": guid, "title": title, "link": link, "published": published})

    return items


def item_key(item: dict) -> str:
    return hashlib.sha256(item["id"].encode()).hexdigest()[:24]


def too_old(item: dict, max_age: timedelta) -> bool:
    published = item["published"]
    if published is None:
        return False  # no timestamp -> let the dedupe cache handle it
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - published > max_age


def post_to_discord(webhook_url: str, topic: str, items: list) -> bool:
    embeds = [
        {
            "title": item["title"][:250] or "(untitled)",
            "url": item["link"],
            "color": 0x1F6FEB,
        }
        for item in items
    ]
    payload = json.dumps({"content": f"**{topic}**", "embeds": embeds}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 429:  # rate limited: wait once and retry
            time.sleep(3)
            try:
                with urllib.request.urlopen(req, timeout=20):
                    return True
            except urllib.error.URLError as retry_err:
                log(f"ERROR webhook retry failed: {retry_err}")
                return False
        log(f"ERROR webhook HTTP {e.code}: {e.read()[:200]!r}")
        return False
    except urllib.error.URLError as e:
        log(f"ERROR webhook unreachable: {e}")
        return False


def run() -> int:
    config = json.loads(CONFIG_PATH.read_text())
    env = load_env(ENV_PATH)
    env.update({k: v for k, v in os.environ.items() if k.startswith("DISCORD_WEBHOOK_")})
    seen = load_seen()
    seen_set = set(seen)
    max_items = config.get("max_items_per_run", 5)
    max_age = timedelta(hours=config.get("max_age_hours", 24))
    posted_total = 0

    for topic in config["topics"]:
        webhook_url = env.get(topic["webhook_env"], "")
        if not webhook_url.startswith("https://discord.com/api/webhooks/"):
            log(f"SKIP '{topic['name']}': {topic['webhook_env']} not set in .env")
            continue

        nitter_base = config.get("nitter_base", "https://nitter.net")
        feed_urls = list(topic.get("feeds", []))
        feed_urls += [f"{nitter_base}/{acct}/rss" for acct in topic.get("x_accounts", [])]

        fresh = []
        for feed_url in feed_urls:
            is_nitter = feed_url.startswith(nitter_base)
            try:
                parsed = parse_feed(fetch(feed_url))
            except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as e:
                # Nitter periodically serves an HTML bot-challenge instead of
                # RSS (shows up as ParseError/403); the next run catches up.
                log(f"WARN feed failed ({feed_url}): {e}")
                continue
            finally:
                if is_nitter:
                    time.sleep(1.5)  # be gentle so nitter.net doesn't rate-limit us
            for item in parsed:
                key = item_key(item)
                if key in seen_set or too_old(item, max_age) or not item["link"]:
                    continue
                seen_set.add(key)
                seen.append(key)
                fresh.append(item)

        fresh = fresh[:max_items]
        if not fresh:
            continue
        if post_to_discord(webhook_url, topic["name"], fresh):
            posted_total += len(fresh)
            log(f"POSTED {len(fresh)} item(s) -> {topic['name']}")
        time.sleep(1)  # be polite between webhook calls

    save_seen(seen)
    log(f"DONE posted={posted_total}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
