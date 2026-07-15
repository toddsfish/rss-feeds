#!/usr/bin/env python3
"""Generate RSS feed for Jamie Hurst's Blog (jamiehurst.co.uk).

The blog is static HTML: the landing page renders each post as an
``<article>`` containing a title link, a "Posted on <weekday> <Month> <day>,
<year>" date line, and a short excerpt. Older posts live on paginated
``?page=N`` listing pages, so on a full reset we walk pagination until no
new posts are found; on incremental runs we only look at page 1 and merge
with the cache to avoid re-crawling history every hour.
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

BLOG_URL = "https://jamiehurst.co.uk"
FEED_NAME = "jamiehurst"
BASE_URL = "https://jamiehurst.co.uk"


def fetch_page(url: str) -> str:
    """Fetch a page and return its HTML content, forcing UTF-8 decoding.

    The site's server omits a charset in its Content-Type header, so
    requests falls back to ISO-8859-1 per HTTP spec, mangling characters
    like em dashes. The site is actually UTF-8 encoded.
    """
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text

# Listing dates look like "Posted on Sunday May 24, 2026".
DATE_PREFIX_RE = re.compile(r"^Posted on\s+", re.IGNORECASE)
DATE_FORMATS = [
    "%A %B %d, %Y",  # Sunday May 24, 2026
    "%A %b %d, %Y",  # Sunday May 24, 2026 (abbreviated month, just in case)
]

# Post slugs are date-prefixed, e.g. /2026-05-24_ai-sustainable.
SLUG_DATE_RE = re.compile(r"/(\d{4}-\d{2}-\d{2})_")


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{BASE_URL}{href}"


def parse_date(date_text: str, href: str) -> datetime:
    """Parse a listing date string, falling back to the slug date, then a stable fallback."""
    cleaned = DATE_PREFIX_RE.sub("", date_text.strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=pytz.UTC)
        except ValueError:
            continue

    slug_match = SLUG_DATE_RE.search(href)
    if slug_match:
        try:
            return datetime.strptime(slug_match.group(1), "%Y-%m-%d").replace(tzinfo=pytz.UTC)
        except ValueError:
            pass

    logger.warning(f"Could not parse date '{date_text}' for {href}; using fallback")
    return stable_fallback_date(_absolute(href))


def parse_posts(html_content: str) -> list[dict]:
    """Parse a Jamie Hurst blog listing page into a list of post dicts."""
    soup = BeautifulSoup(html_content, "html.parser")
    content = soup.find("div", id="content")
    if not content:
        return []

    posts_by_url: dict[str, dict] = {}

    for article in content.find_all("article"):
        heading = article.find("h1")
        anchor = heading.find("a", href=True) if heading else None
        if not anchor:
            continue

        title = anchor.get_text(strip=True)
        href = anchor["href"]
        if not title or not href:
            continue

        full_url = _absolute(href)
        if full_url in posts_by_url:
            continue

        date_el = article.find("p", class_="date")
        date_text = date_el.get_text(strip=True) if date_el else ""
        post_date = parse_date(date_text, href) if date_text else parse_date("", href)

        description = None
        summary = article.find("div", class_="summary")
        if summary:
            for p in summary.find_all("p"):
                text = p.get_text(" ", strip=True)
                if text and text.lower() != "read more":
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


def fetch_all_pages() -> list[dict]:
    """Follow pagination (?page=N) until no new posts are found. Returns all posts."""
    logger.info(f"Fetching {BLOG_URL}")
    all_posts = parse_posts(fetch_page(BLOG_URL))
    logger.info(f"Found {len(all_posts)} posts on page 1")

    seen_urls = {p["link"] for p in all_posts}

    page = 2
    consecutive_empty = 0
    while consecutive_empty < 2 and page <= 50:
        page_url = f"{BLOG_URL}/?page={page}"
        logger.info(f"Fetching: {page_url}")
        try:
            page_html = fetch_page(page_url)
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch page {page}: {e}")
            break

        page_posts = parse_posts(page_html)
        new_posts = [p for p in page_posts if p["link"] not in seen_urls]

        if not new_posts:
            consecutive_empty += 1
            logger.info(f"  No new posts (attempt {consecutive_empty})")
        else:
            consecutive_empty = 0
            logger.info(f"  Found {len(new_posts)} new posts")
            all_posts.extend(new_posts)
            seen_urls.update(p["link"] for p in new_posts)

        page += 1

    sorted_posts = sort_posts_for_feed(all_posts, date_field="date")
    logger.info(f"Total unique posts across all pages: {len(sorted_posts)}")
    return sorted_posts


def generate_rss_feed(posts: list[dict]) -> FeedGenerator:
    """Build the RSS feed from the list of posts."""
    fg = FeedGenerator()
    fg.title("Jamie Hurst's Blog")
    fg.description("Software and systems engineering writing from Jamie Hurst.")
    fg.language("en")
    fg.author({"name": "Jamie Hurst"})
    fg.subtitle("Latest posts from Jamie Hurst's Blog")
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
        full_reset: If True, ignore the cache and rebuild by walking pagination.
    """
    cache = load_cache(FEED_NAME)
    cached_entries = deserialize_entries(cache.get("entries", []))

    if full_reset or not cached_entries:
        mode = "full reset" if full_reset else "no cache exists"
        logger.info(f"Running full fetch ({mode})")
        posts = fetch_all_pages()
    else:
        logger.info("Running incremental update (page 1 only)")
        new_posts = parse_posts(fetch_page(BLOG_URL))
        logger.info(f"Found {len(new_posts)} posts on page 1")
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
    parser = argparse.ArgumentParser(description="Generate Jamie Hurst's Blog RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (walk pagination)")
    args = parser.parse_args()
    main(full_reset=args.full)
