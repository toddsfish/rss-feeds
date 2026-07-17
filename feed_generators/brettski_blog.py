#!/usr/bin/env python3
"""Generate RSS feed for Brettski's Blog (brettski.net).

The homepage is mostly static HTML, but the "Sometimes I post random things"
section is populated client-side via jQuery from a small JSON API (an AWS
Lambda function URL) that returns every recent post as ``{date, s3Key,
title}``. We call that API directly instead of driving a browser. The page
also links to a handful of older, undated posts under ``gumbyadventures/``
in a static "really old stuff" list, which we parse straight out of the
homepage HTML.

Individual post pages don't carry a usable meta description (it always
duplicates the title), so we fetch each post's page to pull its first
paragraph as the feed description. To keep this cheap on the hourly cron,
we only fetch a post's page when it isn't already in the cache — the
incremental run typically only sees 0-1 new posts.
"""

import argparse
import html
import re
from datetime import datetime

import pytz
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

from utils import (
    DEFAULT_HEADERS,
    deserialize_entries,
    fetch_page,
    load_cache,
    merge_entries,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
    stable_fallback_date,
)

logger = setup_logging()

BLOG_URL = "https://brettski.net"
FEED_NAME = "brettski"
API_URL = "https://f6r7k5hoojxiwgtbnpyy5ta5hy0xqjeh.lambda-url.ap-southeast-2.on.aws/"


def _absolute(path: str) -> str:
    if path.startswith("http"):
        return path
    return f"{BLOG_URL}/{path.lstrip('/')}"


def fetch_recent_entries() -> list[dict]:
    """Fetch recent post metadata (title, link, date) from the blog's JSON API."""
    response = requests.get(API_URL, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    entries = []
    for item in data:
        s3_key = item.get("s3Key")
        title = html.unescape(item.get("title", "")).strip()
        if not s3_key or not title:
            continue

        link = _absolute(s3_key)
        try:
            date = datetime.strptime(item["date"], "%Y-%m-%d").replace(tzinfo=pytz.UTC)
        except (KeyError, ValueError):
            logger.warning(f"Could not parse date {item.get('date')!r} for {link}; using fallback")
            date = stable_fallback_date(link)

        entries.append({"link": link, "title": title, "date": date})

    return entries


def fetch_legacy_entries() -> list[dict]:
    """Parse the undated 'really old stuff' post list straight from the homepage."""
    soup = BeautifulSoup(fetch_page(BLOG_URL), "html.parser")

    entries = []
    for div in soup.find_all("div", class_="blogentry"):
        anchor = div.find("a", href=True)
        if not anchor:
            continue

        title = html.unescape(anchor.get_text(strip=True))
        if not title:
            continue

        link = _absolute(anchor["href"])
        entries.append({"link": link, "title": title, "date": stable_fallback_date(link)})

    return entries


def fetch_description(link: str) -> str:
    """Fetch an individual post page and return its first paragraph as a description."""
    try:
        page_html = fetch_page(link)
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {link} for description: {e}")
        return ""

    soup = BeautifulSoup(page_html, "html.parser")
    container = soup.find("div", id="blogcontent") or soup
    for p in container.find_all("p"):
        text = p.get_text(" ", strip=True)
        if text:
            return re.sub(r"\s+", " ", html.unescape(text)).strip()

    return ""


def build_posts(entries: list[dict], existing_links: set[str]) -> list[dict]:
    """Build full post dicts (with description) for entries not already cached.

    Skipping already-cached links avoids re-fetching every post's page on
    every incremental run.
    """
    posts = []
    for entry in entries:
        link = entry["link"]
        if link in existing_links:
            continue

        description = fetch_description(link) or entry["title"]
        posts.append(
            {
                "link": link,
                "title": entry["title"],
                "description": description,
                "date": entry["date"],
            }
        )

    return posts


def generate_rss_feed(posts: list[dict]) -> FeedGenerator:
    """Build the RSS feed from the list of posts."""
    fg = FeedGenerator()
    fg.title("Brettski's Blog")
    fg.description("Random posts on AWS, cloud engineering, Linux, and other technical adventures from Brettski.")
    fg.language("en")
    fg.author({"name": "Brettski"})
    fg.subtitle("Latest posts from Brettski's Blog")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    for post in posts:
        fe = fg.add_entry()
        fe.title(post["title"])
        fe.description(post["description"])
        fe.link(href=post["link"])
        fe.id(post["link"])
        date = post.get("date")
        if date:
            try:
                dt = date if not isinstance(date, str) else datetime.fromisoformat(date)
                fe.published(dt)
            except (ValueError, TypeError):
                pass

    logger.info(f"Generated RSS feed with {len(posts)} entries")
    return fg


def main(full_reset: bool = False) -> bool:
    """Fetch the blog, merge with cache, and write the feed.

    Args:
        full_reset: If True, ignore the cache and re-fetch every post's description.
    """
    cache = load_cache(FEED_NAME)
    cached_entries = deserialize_entries(cache.get("entries", []))
    existing_links = set() if full_reset else {e["link"] for e in cached_entries}

    logger.info(f"Fetching {API_URL}")
    entries = fetch_recent_entries()
    logger.info(f"Found {len(entries)} recent posts")

    logger.info(f"Fetching legacy posts from {BLOG_URL}")
    legacy_entries = fetch_legacy_entries()
    logger.info(f"Found {len(legacy_entries)} legacy posts")
    entries.extend(legacy_entries)

    new_posts = build_posts(entries, existing_links)
    logger.info(f"Fetched descriptions for {len(new_posts)} new/updated posts")

    if full_reset or not cached_entries:
        mode = "full reset" if full_reset else "no cache exists"
        logger.info(f"Building feed from live data only ({mode})")
        posts = sort_posts_for_feed(new_posts, date_field="date")
    else:
        posts = merge_entries(new_posts, cached_entries)

    if not posts:
        logger.warning("No posts fetched — skipping feed update to avoid overwriting with empty feed")
        return False

    save_cache(FEED_NAME, posts)
    feed = generate_rss_feed(posts)
    save_rss_feed(feed, FEED_NAME)

    logger.info("Done!")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Brettski's Blog RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (re-fetch every post's description)")
    args = parser.parse_args()
    main(full_reset=args.full)
