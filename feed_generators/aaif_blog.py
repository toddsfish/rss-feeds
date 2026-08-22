#!/usr/bin/env python3
"""Generate RSS feed for the AAIF Blog (aaif.io/blog)."""

import argparse
import re
from datetime import datetime

import pytz
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils import (
    deserialize_entries,
    load_cache,
    merge_entries,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    setup_selenium_driver,
    sort_posts_for_feed,
    stable_fallback_date,
)

logger = setup_logging()

FEED_NAME = "aaif"
BLOG_URL = "https://aaif.io/blog"
BASE_URL = "https://aaif.io"

DATE_PATTERN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
)


def fetch_blog_content(url=BLOG_URL):
    """Fetch the fully loaded HTML content of the AAIF blog page using Selenium."""
    driver = None
    try:
        logger.info(f"Fetching content from URL: {url}")
        driver = setup_selenium_driver()
        driver.get(url)

        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "article")))
            logger.info("Blog articles loaded successfully")
        except Exception:
            logger.warning("Could not confirm articles loaded, proceeding anyway...")

        html_content = driver.page_source
        logger.info("Successfully fetched HTML content")
        return html_content

    except Exception as e:
        logger.error(f"Error fetching content: {e}")
        raise
    finally:
        if driver:
            driver.quit()


def extract_title(card):
    """Extract title using multiple fallback selectors."""
    selectors = [
        "h3 a",
        "h2 a",
        "h3",
        "h2",
        "h1",
    ]
    for selector in selectors:
        elem = card.select_one(selector)
        if elem and elem.text.strip():
            title = " ".join(elem.text.split())
            if len(title) >= 5:
                return title
    return None


def extract_link(card):
    """Extract the article link from the title anchor (or any /blog/ link)."""
    anchors = card.select("h3 a[href^='/blog/'], h2 a[href^='/blog/'], a[href^='/blog/']")
    for anchor in anchors:
        href = anchor.get("href", "")
        if href and href not in ("/blog", "/blog/"):
            return BASE_URL + href if href.startswith("/") else href
    return None


def extract_date(card):
    """Extract the publish date using a regex over the card's text content."""
    text = card.get_text(" ", strip=True)
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    try:
        date = datetime.strptime(match.group(0), "%B %d, %Y")
        return date.replace(tzinfo=pytz.UTC)
    except ValueError:
        return None


def extract_description(card):
    """Extract the summary/description text for the article."""
    selectors = [
        "p.line-clamp-5",
        "p[class*='line-clamp']",
        "p",
    ]
    for selector in selectors:
        elem = card.select_one(selector)
        if elem and elem.text.strip():
            return elem.text.strip()
    return None


def extract_category(card):
    """Extract the topic tag/category, skipping author-type pills like 'Staff'."""
    tag_container = card.select_one("div.w-max")
    if not tag_container:
        return "Blog"

    tags = [d.get_text(strip=True) for d in tag_container.find_all("div") if d.get_text(strip=True)]
    if not tags:
        return "Blog"

    # When there are two pills, the second is the topic (the first is an
    # author-type label such as "Staff", "AAIF", or "Ambassador").
    return tags[-1]


def validate_article(article):
    """Validate that article has all required fields with reasonable values."""
    if not article.get("title") or len(article["title"]) < 5:
        logger.warning(f"Invalid title for article: {article.get('link', 'unknown')}")
        return False
    if not article.get("link") or not article["link"].startswith("http"):
        logger.warning(f"Invalid link for article: {article.get('title', 'unknown')}")
        return False
    return True


def parse_blog_html(html_content):
    """Parse the AAIF blog HTML content and extract article information."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        articles = []
        seen_links = set()

        cards = soup.select("article")
        logger.info(f"Found {len(cards)} potential article cards")

        for card in cards:
            try:
                link = extract_link(card)
                if not link:
                    logger.debug("Could not extract link for a card, skipping")
                    continue

                if link in seen_links:
                    continue
                seen_links.add(link)

                title = extract_title(card)
                if not title:
                    logger.debug(f"Could not extract title for link: {link}")
                    continue

                date = extract_date(card)
                if not date:
                    logger.warning(f"Could not extract date for article: {title}, using fallback")
                    date = stable_fallback_date(link)

                description = extract_description(card) or title
                category = extract_category(card)

                article = {
                    "title": title,
                    "link": link,
                    "date": date,
                    "category": category,
                    "description": description,
                }

                if validate_article(article):
                    articles.append(article)

            except Exception as e:
                logger.warning(f"Error parsing article card: {e!s}")
                continue

        logger.info(f"Successfully parsed {len(articles)} valid articles")
        return articles

    except Exception as e:
        logger.error(f"Error parsing HTML content: {e!s}")
        raise


def generate_rss_feed(articles):
    """Generate RSS feed from AAIF blog articles."""
    try:
        fg = FeedGenerator()
        fg.title("The AAIF Blog")
        fg.description("The latest blog posts from the Agentic AI Foundation (AAIF).")
        fg.language("en")

        fg.author({"name": "Agentic AI Foundation (AAIF)"})
        fg.logo("https://cdn.sanity.io/images/4o10fa7h/production/cc9de238d88dab9eebd13cb1e1cfd858ba39509e-32x32.png")
        fg.subtitle("Latest updates from the AAIF blog")
        setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

        articles_sorted = sort_posts_for_feed(articles, date_field="date")

        for article in articles_sorted:
            fe = fg.add_entry()
            fe.title(article["title"])
            fe.description(article["description"])
            fe.link(href=article["link"])
            fe.published(article["date"])
            fe.category(term=article["category"])
            fe.id(article["link"])

        logger.info("Successfully generated RSS feed")
        return fg

    except Exception as e:
        logger.error(f"Error generating RSS feed: {e!s}")
        raise


def main(full_reset=False):
    """Main function to generate RSS feed from the AAIF blog.

    Args:
        full_reset: If True, ignore cache and rebuild from a fresh fetch.
                   If False, merge freshly fetched articles with the cache.
    """
    try:
        cache = load_cache(FEED_NAME)
        cached_articles = deserialize_entries(cache.get("entries", []))

        if full_reset or not cached_articles:
            mode = "full reset" if full_reset else "no cache exists"
            logger.info(f"Running full fetch ({mode})")
        else:
            logger.info("Running incremental update")

        html_content = fetch_blog_content()
        new_articles = parse_blog_html(html_content)

        if not new_articles and not cached_articles:
            logger.warning("No articles found. Please check the HTML structure.")
            return False

        if cached_articles and not full_reset:
            articles = merge_entries(new_articles, cached_articles)
        else:
            articles = new_articles

        save_cache(FEED_NAME, articles)

        feed = generate_rss_feed(articles)
        save_rss_feed(feed, FEED_NAME)

        logger.info(f"Successfully generated RSS feed with {len(articles)} articles")
        return True

    except Exception as e:
        logger.error(f"Failed to generate RSS feed: {e!s}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate AAIF Blog RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (ignore cache)")
    args = parser.parse_args()
    main(full_reset=args.full)
