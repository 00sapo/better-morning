from typing import List, Optional
from pydantic import BaseModel, HttpUrl
import feedparser
from datetime import datetime, timezone, timedelta
import email.utils
import json
import os
import time
import random
from urllib.parse import urlparse
import re

from .config import RSSFeed


class Article(BaseModel):
    id: str  # Unique identifier, e.g., link
    title: str
    link: HttpUrl
    source_url: Optional[HttpUrl] = None
    feed_name: Optional[str] = None
    published_date: datetime
    summary: Optional[str] = None
    content: Optional[str] = None  # For text-based content
    raw_content: Optional[bytes] = None  # For binary content like PDFs
    content_type: Optional[str] = None  # E.g., 'application/pdf'
    follow_article_links: Optional[bool] = None  # Per-article link following setting from source


# Custom JSON encoder for datetime objects
class ArticleEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, HttpUrl):
            return str(o)
        return json.JSONEncoder.default(self, o)


class RSSFetcher:
    def __init__(self, feeds: List[RSSFeed]):
        self.feeds = feeds
        # Track domains to implement per-domain rate limiting
        self._domain_last_access = {}
        # Track fetch statistics
        self.fetch_stats = {}

    def _get_history_file_path(self, collection_name: str) -> str:
        history_dir = "history"
        os.makedirs(history_dir, exist_ok=True)
        return os.path.join(history_dir, f"{collection_name}_articles.json")
    
    def _get_digest_history_file_path(self, collection_name: str) -> str:
        history_dir = "history"
        os.makedirs(history_dir, exist_ok=True)
        return os.path.join(history_dir, f"{collection_name}_digest_history.json")
    
    def _parse_time_span(self, time_span: str) -> timedelta:
        """Parse time span like '1h', '2d', '30m' into timedelta."""
        match = re.match(r'^(\d+)([hdm])$', time_span)
        if not match:
            raise ValueError(f"Invalid time span format: {time_span}")
        
        value, unit = match.groups()
        value = int(value)
        
        if unit == 'h':
            return timedelta(hours=value)
        elif unit == 'd':
            return timedelta(days=value)
        elif unit == 'm':
            return timedelta(minutes=value)
        
        raise ValueError(f"Unknown time unit: {unit}")
    
    def _get_last_digest_time(self, collection_name: str) -> Optional[datetime]:
        """Get the timestamp of the last digest for this collection."""
        digest_history_file = self._get_digest_history_file_path(collection_name)
        if not os.path.exists(digest_history_file):
            return None
        
        try:
            with open(digest_history_file, "r") as f:
                data = json.load(f)
                last_digest_str = data.get("last_digest_time")
                if last_digest_str:
                    return datetime.fromisoformat(last_digest_str)
        except (json.JSONDecodeError, ValueError, KeyError):
            return None
        
        return None
    
    def save_digest_time(self, collection_name: str, digest_time: datetime):
        """Save the timestamp when the digest was created."""
        digest_history_file = self._get_digest_history_file_path(collection_name)
        data = {"last_digest_time": digest_time.isoformat()}
        
        with open(digest_history_file, "w") as f:
            json.dump(data, f, indent=4)
    
    def _calculate_cutoff_date(self, max_age: Optional[str], collection_name: str) -> Optional[datetime]:
        """Calculate the cutoff date based on max_age setting."""
        if not max_age:
            return None
        
        current_time = datetime.now(timezone.utc)
        
        if max_age == "last-digest":
            last_digest_time = self._get_last_digest_time(collection_name)
            return last_digest_time
        else:
            # Parse time span and calculate cutoff
            try:
                time_delta = self._parse_time_span(max_age)
                return current_time - time_delta
            except ValueError as e:
                print(f"Warning: Invalid max_age format '{max_age}': {e}")
                return None
    
    def _is_article_too_old(self, article_date: datetime, cutoff_date: Optional[datetime]) -> bool:
        """Check if an article is older than the cutoff date."""
        if cutoff_date is None:
            return False
        
        # Ensure both dates are timezone-aware for comparison
        if article_date.tzinfo is None:
            article_date = article_date.replace(tzinfo=timezone.utc)
        if cutoff_date.tzinfo is None:
            cutoff_date = cutoff_date.replace(tzinfo=timezone.utc)
        
        return article_date < cutoff_date

    def _load_historical_articles(self, collection_name: str) -> List[Article]:
        history_file = self._get_history_file_path(collection_name)
        if not os.path.exists(history_file):
            return []
        with open(history_file, "r") as f:
            data = json.load(f)
            # Deserialize datetime strings back to datetime objects
            for item in data:
                if "published_date" in item and isinstance(item["published_date"], str):
                    item["published_date"] = datetime.fromisoformat(
                        item["published_date"]
                    )
            return [Article(**item) for item in data]

    def _save_articles_to_history(self, collection_name: str, articles: List[Article]):
        history_file = self._get_history_file_path(collection_name)
        # Convert Pydantic models to dictionaries for JSON serialization
        # Ensure only unique articles are saved based on their ID
        unique_articles = {article.id: article for article in articles}
        articles_to_save = [
            article.model_dump() for article in unique_articles.values()
        ]
        with open(history_file, "w") as f:
            json.dump(articles_to_save, f, cls=ArticleEncoder, indent=4)

    def save_selected_articles_to_history(self, collection_name: str, selected_articles: List[Article]):
        """Save only the selected articles to history, merging with existing historical articles."""
        historical_articles = self._load_historical_articles(collection_name)
        
        # Create a dictionary of existing articles by ID
        all_articles = {article.id: article for article in historical_articles}
        
        # Add the selected articles to the dictionary (will overwrite if same ID)
        for article in selected_articles:
            all_articles[article.id] = article
        
        # Save the merged list
        self._save_articles_to_history(collection_name, list(all_articles.values()))

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL for rate limiting purposes."""
        try:
            return urlparse(str(url)).netloc.lower()
        except Exception:
            return "unknown"

    def _apply_rate_limit(self, domain: str, min_delay: float = 1.0, max_delay: float = 3.0):
        """Apply rate limiting per domain with randomized delays."""
        current_time = time.time()
        last_access = self._domain_last_access.get(domain, 0)
        
        # Calculate time since last access to this domain
        time_since_last = current_time - last_access
        
        # Add random delay between min_delay and max_delay seconds
        delay = random.uniform(min_delay, max_delay)
        
        # If we accessed this domain recently, wait additional time
        if time_since_last < delay:
            additional_wait = delay - time_since_last
            print(f"Rate limiting {domain}: waiting {additional_wait:.1f}s")
            time.sleep(additional_wait)
        
        # Update last access time
        self._domain_last_access[domain] = time.time()

    def _fetch_feed_with_retry(self, feed_url: str, timeout: int = 30, max_retries: int = 3) -> Optional[feedparser.FeedParserDict]:
        """Fetch RSS feed with exponential backoff retry logic."""
        import socket
        
        original_timeout = socket.getdefaulttimeout()
        
        for attempt in range(max_retries):
            try:
                # Set socket timeout
                socket.setdefaulttimeout(timeout)
                
                # Parse the feed
                feed = feedparser.parse(feed_url)
                
                # Restore original timeout
                socket.setdefaulttimeout(original_timeout)
                
                # Check if feed was successfully parsed
                status = getattr(feed, 'status', None)
                if status is not None and status >= 400:
                    raise Exception(f"HTTP error {status}")
                
                if not feed.entries and hasattr(feed, 'bozo') and feed.bozo:
                    raise Exception(f"Feed parsing error: {getattr(feed, 'bozo_exception', 'Unknown error')}")
                
                return feed
                
            except Exception as e:
                print(f"Attempt {attempt + 1}/{max_retries} failed for {feed_url}: {e}")
                
                # Restore timeout on error
                try:
                    socket.setdefaulttimeout(original_timeout)
                except:
                    pass
                
                if attempt < max_retries - 1:
                    # Exponential backoff: 2^attempt seconds + random jitter
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    print(f"Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    print(f"Failed to fetch {feed_url} after {max_retries} attempts")
                    return None
        
        return None

    def _record_fetch_result(self, feed_config: RSSFeed, success: bool, error_msg: Optional[str] = None, article_count: int = 0):
        """Record the result of a feed fetch attempt."""
        feed_key = str(feed_config.url)
        if feed_key not in self.fetch_stats:
            self.fetch_stats[feed_key] = {
                'name': feed_config.name or 'Unnamed',
                'url': str(feed_config.url),
                'success': False,
                'error': None,
                'articles_fetched': 0,
                'last_attempt': None
            }
        
        self.fetch_stats[feed_key].update({
            'success': success,
            'error': error_msg if not success else None,
            'articles_fetched': article_count if success else 0,
            'last_attempt': time.time()
        })

    def get_fetch_report(self) -> dict:
        """Get a summary report of all fetch attempts."""
        successful = []
        failed = []
        
        for feed_key, stats in self.fetch_stats.items():
            if stats['success']:
                successful.append({
                    'name': stats['name'],
                    'url': stats['url'],
                    'articles_fetched': stats['articles_fetched']
                })
            else:
                failed.append({
                    'name': stats['name'],
                    'url': stats['url'],
                    'error': stats['error']
                })
        
        return {
            'successful': successful,
            'failed': failed,
            'total_feeds': len(self.fetch_stats),
            'success_rate': len(successful) / len(self.fetch_stats) if self.fetch_stats else 0
        }

    def fetch_articles(self, collection_name: str, max_age: Optional[str] = None) -> List[Article]:
        new_articles: List[Article] = []
        historical_articles = {
            article.id: article
            for article in self._load_historical_articles(collection_name)
        }
        all_fetched_articles_for_history: List[Article] = list(
            historical_articles.values()
        )
        
        # Calculate cutoff date for age filtering
        cutoff_date = self._calculate_cutoff_date(max_age, collection_name)
        if cutoff_date:
            print(f"Filtering articles older than {cutoff_date.isoformat()}")
        else:
            print("No age filtering applied")

        for feed_config in self.feeds:
            print(f"Fetching articles from {feed_config.name} ({feed_config.url})")
            try:
                # Apply rate limiting per domain
                domain = self._get_domain(str(feed_config.url))
                self._apply_rate_limit(domain)
                
                # Fetch feed with retry logic using per-feed settings
                timeout = feed_config.timeout or 30
                max_retries = feed_config.max_retries or 3
                feed = self._fetch_feed_with_retry(str(feed_config.url), timeout, max_retries)
                
                if feed is None:
                    self._record_fetch_result(feed_config, False, "Failed to fetch feed after retries")
                    print(f"Skipping {feed_config.name} due to fetch failures")
                    continue

                # Filter out articles that are already in history, then apply max_articles limit
                entries = feed.entries
                available_entries = []
                
                for entry in entries:
                    article_id = entry.link  # Using link as a unique ID
                    # Skip articles already in history (previously selected)
                    if article_id not in historical_articles:
                        available_entries.append(entry)
                
                # Now apply max_articles limit to the filtered entries
                if (
                    feed_config.max_articles is not None
                    and feed_config.max_articles > 0
                    and len(available_entries) > feed_config.max_articles
                ):
                    print(
                        f"Limiting to the latest {feed_config.max_articles} new articles for this feed (excluding previously selected)."
                    )
                    available_entries = available_entries[:feed_config.max_articles]

                for entry in available_entries:
                    article_link = entry.link
                    article_id = article_link  # Using link as a unique ID for now

                    published_parsed = entry.get("published_parsed")
                    published_date = None
                    if published_parsed:
                        try:
                            published_date = datetime(
                                *published_parsed[:6], tzinfo=timezone.utc
                            )
                        except ValueError:
                            # Fallback for incorrect time tuples or if timezone info is missing
                            # Try parsing published string directly with email.utils.parsedate_to_datetime
                            published_date_str = entry.get("published")
                            if published_date_str:
                                try:
                                    parsed_dt = email.utils.parsedate_to_datetime(
                                        published_date_str
                                    )
                                    if parsed_dt.tzinfo is None:
                                        published_date = parsed_dt.replace(
                                            tzinfo=timezone.utc
                                        )
                                    else:
                                        published_date = parsed_dt
                                except (TypeError, ValueError):
                                    print(
                                        f"Warning: Could not parse date '{published_date_str}' for article '{entry.title}'. Using current time."
                                    )
                                    published_date = datetime.now(timezone.utc)
                            else:
                                print(
                                    f"Warning: No publish date found for article '{entry.title}'. Using current time."
                                )
                                published_date = datetime.now(timezone.utc)
                    else:
                        print(
                            f"Warning: No 'published_parsed' found for article '{entry.title}'. Using current time."
                        )
                        published_date = datetime.now(timezone.utc)

                    # Check if article is too old based on max_age setting
                    if self._is_article_too_old(published_date, cutoff_date):
                        print(f"Skipping article '{entry.title}' (published {published_date.isoformat()}) - older than cutoff")
                        continue

                    # Prioritize 'content' over 'summary' if available, as it's often the full article.
                    # feedparser returns a list of content objects; we take the first one.
                    content_html = ""
                    if "content" in entry and entry.content:
                        content_html = entry.content[0].value
                    
                    summary_text = content_html or entry.get("summary")

                    article = Article(
                        id=article_id,
                        title=entry.title,
                        link=HttpUrl(article_link),
                        source_url=feed_config.url,
                        feed_name=feed_config.name,
                        published_date=published_date,
                        summary=summary_text,
                        follow_article_links=feed_config.follow_article_links,
                    )
                    new_articles.append(article)
                    all_fetched_articles_for_history.append(
                        article
                    )  # Add to list for saving history

                # Record successful fetch
                self._record_fetch_result(feed_config, True, article_count=len(available_entries))

            except Exception as e:
                error_msg = f"Error fetching feed: {str(e)}"
                self._record_fetch_result(feed_config, False, error_msg)
                print(f"Error fetching feed {feed_config.name}: {e}")

        # Don't save articles to history here - let the caller decide which articles to save
        return new_articles
