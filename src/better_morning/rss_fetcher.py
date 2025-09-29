from typing import List, Optional
from pydantic import BaseModel, HttpUrl
import feedparser
from datetime import datetime, timezone
import email.utils
import json
import os

from .config import RSSFeed


class Article(BaseModel):
    id: str  # Unique identifier, e.g., link
    title: str
    link: HttpUrl
    source_url: HttpUrl = None
    published_date: datetime
    summary: Optional[str] = None
    content: Optional[str] = None  # For text-based content
    raw_content: Optional[bytes] = None  # For binary content like PDFs
    content_type: Optional[str] = None  # E.g., 'application/pdf'


# Custom JSON encoder for datetime objects
class ArticleEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, HttpUrl):
            return str(obj)
        return json.JSONEncoder.default(self, obj)


class RSSFetcher:
    def __init__(self, feeds: List[RSSFeed]):
        self.feeds = feeds

    def _get_history_file_path(self, collection_name: str) -> str:
        history_dir = "history"
        os.makedirs(history_dir, exist_ok=True)
        return os.path.join(history_dir, f"{collection_name}_articles.json")

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

    def fetch_articles(self, collection_name: str) -> List[Article]:
        new_articles: List[Article] = []
        historical_articles = {
            article.id: article
            for article in self._load_historical_articles(collection_name)
        }
        all_fetched_articles_for_history: List[Article] = list(
            historical_articles.values()
        )

        for feed_config in self.feeds:
            print(f"Fetching articles from {feed_config.name} ({feed_config.url})")
            try:
                feed = feedparser.parse(
                    str(feed_config.url)
                )  # Convert HttpUrl to string

                # Limit the number of entries if max_articles is set for the feed
                entries = feed.entries
                if (
                    feed_config.max_articles is not None
                    and feed_config.max_articles > 0
                ):
                    print(
                        f"Limiting to the latest {feed_config.max_articles} articles for this feed."
                    )
                    entries = feed.entries[: feed_config.max_articles]

                for entry in entries:
                    article_link = entry.link
                    article_id = article_link  # Using link as a unique ID for now

                    # If the article is already in history, skip it
                    if article_id in historical_articles:
                        # Update existing article in history if any fields might have changed (e.g. summary from default to actual)
                        # For now, we assume content is fetched later.
                        # We can decide later if we want to update other fields here or only add new.
                        continue

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
                        published_date=published_date,
                        summary=summary_text,
                    )
                    new_articles.append(article)
                    all_fetched_articles_for_history.append(
                        article
                    )  # Add to list for saving history

            except Exception as e:
                print(f"Error fetching feed {feed_config.name}: {e}")

        # Save all fetched articles (including historical and new ones)
        self._save_articles_to_history(
            collection_name, all_fetched_articles_for_history
        )
        return new_articles
