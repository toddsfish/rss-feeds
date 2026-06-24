#!/usr/bin/env python3
"""Generate RSS feed for the Google Cloud Blog (cloud.google.com/blog).

The blog landing page is static HTML (content is present without JavaScript),
but it only lists ~16 of the most recent posts and provides no publish dates.
To retain history across runs we cache previously seen posts and merge new
ones in on every run. Because the listing exposes no dates, ordering uses a
stable per-URL fallback date so the feed stays deterministic and cache-stable.
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

BLOG_URL = "https://cloud.google.com/blog"
FEED_NAME = "googlecloud"
BASE_URL = "https://cloud.google.com"


def is_article_url(href: str) -> bool:
    """Return True if *href* points to an individual blog post.

    Post URLs have the shape ``/blog/{products|topics}/<section>/<slug>`` —
    i.e. at least three path segments after ``/blog/``. Section landing pages
    (e.g. ``/blog/products/ai-machine-learning``) have only two and are skipped.
    """
    if "/blog/" not in href:
        return False
    after = href.split("/blog/", 1)[1].strip("/")
    if not after:
        return False
    parts = after.split("/")
    return len(parts) >= 3


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{BASE_URL}{href}"


def parse_posts(html_content: str) -> list[dict]:
    """Parse the Google Cloud blog landing page into a list of post dicts."""
    soup = BeautifulSoup(html_content, "html.parser")
    posts_by_url: dict[str, dict] = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not is_article_url(href):
            continue

        full_url = _absolute(href).split("?")[0].rstrip("/")
        if full_url in posts_by_url:
            continue

        # Title comes from the heading inside the card. Anchors without a
        # heading are link-only references (sidebars/related lists) that would
        # only yield a low-quality slug title, so skip them — the real post
        # card with a proper heading is elsewhere on the page.
        title = None
        for tag in ("h1", "h2", "h3", "h4"):
            el = anchor.find(tag)
            if el and el.get_text(strip=True):
                title = el.get_text(strip=True)
                break
        if not title:
            continue

        # Category: derive from the URL section segment as a stable source.
        category = None
        after = full_url.split("/blog/", 1)[1].split("/")
        if len(after) >= 2:
            category = after[1].replace("-", " ").title()

        # Description: the author / read-time line if present.
        description = None
        p = anchor.find("p")
        if p and p.get_text(strip=True):
            description = p.get_text(" ", strip=True)
        if not description:
            description = title

        title = html.unescape(title)
        description = html.unescape(description)
        description = re.sub(r"\s+", " ", description).strip()

        posts_by_url[full_url] = {
            "link": full_url,
            "title": title,
            "description": description,
            "category": category,
            # The listing page has no dates. A stable URL-derived fallback is
            # used here; the real publish date is fetched per-article in main()
            # for newly-seen posts (see fetch_article_date).
            "date": stable_fallback_date(full_url).isoformat(),
        }

    return list(posts_by_url.values())


_JSON_LD_DATE_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')


def fetch_article_date(url: str) -> str | None:
    """Fetch an article page and return its publish date as an ISO string.

    Google Cloud article pages embed a JSON-LD ``datePublished`` value. Returns
    None if the page can't be fetched or no date is found, in which case the
    caller keeps the stable fallback date.
    """
    try:
        html_content = fetch_page(url, timeout=20)
    except requests.RequestException as e:
        logger.warning(f"Could not fetch article for date: {url} ({e})")
        return None

    match = _JSON_LD_DATE_RE.search(html_content)
    if not match:
        return None
    raw = match.group(1).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def generate_rss_feed(posts: list[dict]) -> FeedGenerator:
    """Build the RSS feed from the list of posts."""
    fg = FeedGenerator()
    fg.title("Google Cloud Blog")
    fg.description(
        "Product updates, technical deep dives, and announcements from the Google Cloud blog (cloud.google.com/blog)."
    )
    fg.language("en")
    fg.author({"name": "Google Cloud", "email": "noreply@google.com"})
    fg.subtitle("Latest posts from the Google Cloud Blog")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    for post in posts:
        fe = fg.add_entry()
        fe.title(post["title"])
        fe.description(post["description"])
        fe.link(href=post["link"])
        fe.id(post["link"])
        if post.get("category"):
            fe.category(term=post["category"])
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
        full_reset: If True, ignore the cache and rebuild from the live page only.
    """
    cache = load_cache(FEED_NAME)
    cached_entries = deserialize_entries(cache.get("entries", []))

    logger.info(f"Fetching {BLOG_URL}")
    new_posts = parse_posts(fetch_page(BLOG_URL))
    logger.info(f"Found {len(new_posts)} posts on landing page")

    # Enrich only posts we haven't seen before with their real publish date.
    cached_links = {e["link"] for e in cached_entries}
    to_enrich = [p for p in new_posts if p["link"] not in cached_links]
    logger.info(f"Fetching publish dates for {len(to_enrich)} new post(s)")
    for post in to_enrich:
        real_date = fetch_article_date(post["link"])
        if real_date:
            post["date"] = real_date

    if full_reset or not cached_entries:
        mode = "full reset" if full_reset else "no cache exists"
        logger.info(f"Building feed from live page only ({mode})")
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
    parser = argparse.ArgumentParser(description="Generate Google Cloud Blog RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (ignore cache)")
    args = parser.parse_args()
    main(full_reset=args.full)
