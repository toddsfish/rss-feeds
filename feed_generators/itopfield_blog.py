#!/usr/bin/env python3
"""Generate RSS feed for ITop Field News (itopfield.com.au).

The homepage is static HTML that effectively lists the whole article
archive in three places: a single hero lead story, a short "Top stories"
sidecar, and a large grid of ``<article class="mp-card">`` cards spanning
every topic section. None of these listing views include a body excerpt,
so each post's short description is pulled from its own page's
``<meta name="description">`` tag. To keep the hourly run cheap, that
per-post fetch only happens for posts not already in the cache -- a full
reset (``--full``) re-fetches every listed post's description from scratch.
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

BLOG_URL = "https://itopfield.com.au"
FEED_NAME = "itopfield"
BASE_URL = "https://itopfield.com.au"

# Listing dates look like "July 15, 2026".
DATE_FORMATS = [
    "%B %d, %Y",
]


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{BASE_URL}{href}"


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


def _add_entry(posts_by_url: dict[str, dict], link: str, title: str, date_text: str, category: str | None) -> None:
    link = _absolute(link).rstrip("/")
    title = html.unescape(title).strip()
    if not title or link in posts_by_url:
        return
    posts_by_url[link] = {
        "link": link,
        "title": title,
        "date": parse_date(date_text, link) if date_text else stable_fallback_date(link),
        "category": category,
    }


def parse_listing(html_content: str) -> list[dict]:
    """Parse the ITop Field News homepage into post stubs (no description yet)."""
    soup = BeautifulSoup(html_content, "html.parser")
    posts_by_url: dict[str, dict] = {}

    # Hero lead story.
    hero = soup.select_one("article.mp-hero__lead")
    if hero:
        anchor = hero.select_one("h1.mp-hero__title a[href]")
        category_el = hero.select_one("a.mp-cat")
        byline = hero.select_one("div.mp-hero__byline")
        date_text = ""
        if byline:
            spans = byline.find_all("span")
            if spans:
                date_text = spans[-1].get_text(strip=True)
        if anchor:
            _add_entry(
                posts_by_url,
                anchor["href"],
                anchor.get_text(strip=True),
                date_text,
                category_el.get_text(strip=True) if category_el else None,
            )

    # "Top stories" sidecar.
    for item in soup.select("li.mp-sidecar__item"):
        anchor = item.select_one("h3.mp-sidecar__title a[href]")
        category_el = item.select_one("a.mp-cat")
        date_el = item.select_one("div.mp-meta")
        if anchor:
            _add_entry(
                posts_by_url,
                anchor["href"],
                anchor.get_text(strip=True),
                date_el.get_text(strip=True) if date_el else "",
                category_el.get_text(strip=True) if category_el else None,
            )

    # Topic card grid (spans every "Xyz" section on the homepage).
    for card in soup.select("article.mp-card"):
        anchor = card.select_one("h3.mp-card__title a[href]")
        category_el = card.select_one("a.mp-cat")
        date_el = card.select_one("div.mp-meta--mt")
        if anchor:
            _add_entry(
                posts_by_url,
                anchor["href"],
                anchor.get_text(strip=True),
                date_el.get_text(strip=True) if date_el else "",
                category_el.get_text(strip=True) if category_el else None,
            )

    return list(posts_by_url.values())


def fetch_description(link: str) -> str:
    """Fetch an individual post page and return its meta description."""
    try:
        page_html = fetch_page(link)
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {link} for description: {e}")
        return ""

    soup = BeautifulSoup(page_html, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return re.sub(r"\s+", " ", html.unescape(meta["content"])).strip()
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
        post = {
            "link": link,
            "title": entry["title"],
            "description": description,
            "date": entry["date"],
        }
        if entry.get("category"):
            post["category"] = entry["category"]
        posts.append(post)

    return posts


def generate_rss_feed(posts: list[dict]) -> FeedGenerator:
    """Build the RSS feed from the list of posts."""
    fg = FeedGenerator()
    fg.title("ITop Field News")
    fg.description("Technology news and analysis for Australian IT professionals and businesses.")
    fg.language("en")
    fg.author({"name": "ITop Field News"})
    fg.subtitle("Latest posts from ITop Field News")
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
        full_reset: If True, ignore the cache and re-fetch every post's description.
    """
    cache = load_cache(FEED_NAME)
    cached_entries = deserialize_entries(cache.get("entries", []))
    existing_links = set() if full_reset else {e["link"] for e in cached_entries}

    logger.info(f"Fetching {BLOG_URL}")
    entries = parse_listing(fetch_page(BLOG_URL))
    logger.info(f"Found {len(entries)} posts on homepage")

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
    parser = argparse.ArgumentParser(description="Generate ITop Field News RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (re-fetch every post's description)")
    args = parser.parse_args()
    main(full_reset=args.full)
