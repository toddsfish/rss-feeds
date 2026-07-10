#!/usr/bin/env python3
"""Generate RSS feed for the Pulumi Blog (pulumi.com/blog).

The blog landing page is static HTML: each post is rendered as an ``<article>``
card containing a title, an individual post link, a publish date, and a short
excerpt. The listing only shows the ~10 most recent posts (older ones live on
paginated ``/blog/page/N/`` pages), so previously seen posts are cached and
merged in on every run to preserve history without re-crawling pagination.
"""

import argparse
import html
import re
from datetime import datetime

import pytz
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

BLOG_URL = "https://www.pulumi.com/blog/"
FEED_NAME = "pulumi"
BASE_URL = "https://www.pulumi.com"

# Dates on the listing look like "Tuesday, Jun 30, 2026".
DATE_FORMATS = [
    "%A, %b %d, %Y",  # Tuesday, Jun 30, 2026
    "%A, %B %d, %Y",  # Tuesday, June 30, 2026
    "%b %d, %Y",  # Jun 30, 2026
    "%B %d, %Y",  # June 30, 2026
]


def parse_date(date_text: str, link: str) -> datetime:
    """Parse a listing date string, falling back to a stable per-URL date."""
    cleaned = date_text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=pytz.UTC)
        except ValueError:
            continue
    logger.warning(f"Could not parse date '{date_text}' for {link}; using fallback")
    return stable_fallback_date(link)


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{BASE_URL}{href}"


def parse_posts(html_content: str) -> list[dict]:
    """Parse the Pulumi blog landing page into a list of post dicts."""
    soup = BeautifulSoup(html_content, "html.parser")
    posts_by_url: dict[str, dict] = {}

    for article in soup.find_all("article"):
        heading = article.find(["h1", "h2", "h3"])
        anchor = article.find("a", href=True)
        if not heading or not anchor:
            continue

        title = heading.get_text(strip=True)
        if not title:
            continue

        full_url = _absolute(anchor["href"]).split("?")[0].rstrip("/")
        if full_url in posts_by_url:
            continue

        time_el = article.find("time")
        date_text = time_el.get_text(strip=True) if time_el else ""
        post_date = parse_date(date_text, full_url) if date_text else stable_fallback_date(full_url)

        description = None
        for p in article.find_all("p"):
            text = p.get_text(" ", strip=True)
            if text and not text.lower().startswith("read more"):
                description = text
                break
        if not description:
            description = title

        title = html.unescape(title)
        description = re.sub(r"\s+", " ", html.unescape(description)).strip()

        posts_by_url[full_url] = {
            "link": full_url,
            "title": title,
            "description": description,
            "date": post_date,
        }

    return list(posts_by_url.values())


def generate_rss_feed(posts: list[dict]) -> FeedGenerator:
    """Build the RSS feed from the list of posts."""
    fg = FeedGenerator()
    fg.title("Pulumi Blog")
    fg.description("Product updates, tutorials, and announcements from the Pulumi blog (pulumi.com/blog).")
    fg.language("en")
    fg.author({"name": "Pulumi", "email": "noreply@pulumi.com"})
    fg.subtitle("Latest posts from the Pulumi Blog")
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
        full_reset: If True, ignore the cache and rebuild from the live page only.
    """
    cache = load_cache(FEED_NAME)
    cached_entries = deserialize_entries(cache.get("entries", []))

    logger.info(f"Fetching {BLOG_URL}")
    new_posts = parse_posts(fetch_page(BLOG_URL))
    logger.info(f"Found {len(new_posts)} posts on landing page")

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
    parser = argparse.ArgumentParser(description="Generate Pulumi Blog RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (ignore cache)")
    args = parser.parse_args()
    main(full_reset=args.full)
