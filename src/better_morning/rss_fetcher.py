from typing import List, Optional
from pydantic import BaseModel, HttpUrl
import feedparser
from datetime import datetime, timezone
import email.utils

from .config import RSSFeed

class Article(BaseModel):
    title: str
    link: HttpUrl
    published_date: datetime
    summary: Optional[str] = None

class RSSFetcher:
    def __init__(self, feeds: List[RSSFeed]):
        self.feeds = feeds

    def fetch_articles(self) -> List[Article]:
        articles: List[Article] = []
        for feed_config in self.feeds:
            print(f"Fetching articles from {feed_config.name} ({feed_config.url})")
            try:
                feed = feedparser.parse(feed_config.url)
                for entry in feed.entries:
                    published_parsed = entry.get('published_parsed')
                    if published_parsed:
                        # Convert to datetime object and ensure timezone awareness
                        published_date = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    else:
                        # Fallback if published_parsed is not available
                        published_date = datetime.now(timezone.utc)

                    articles.append(Article(
                        title=entry.title,
                        link=entry.link,
                        published_date=published_date
                    ))
            except Exception as e:
                print(f"Error fetching feed {feed_config.name}: {e}")
        return articles
