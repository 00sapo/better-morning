import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from better_morning.config import GlobalConfig, load_collection


def _write_toml(path, content):
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_full_collection_processing_flow(tmp_path, monkeypatch):
    """Test the full flow from RSS fetch to final summary"""
    monkeypatch.chdir(tmp_path)

    # Create test collection
    collection_path = tmp_path / "test_collection.toml"
    _write_toml(
        collection_path,
        """
name = "Test Collection"
max_age = "7d"

[[feeds]]
url = "https://example.com/rss"
name = "Test Feed"
max_articles = 5
""",
    )

    global_config = GlobalConfig()
    collection_config = load_collection(str(collection_path), global_config)

    # Mock RSS feed
    mock_feed = MagicMock()
    mock_feed.status = 200  # Add status attribute
    mock_feed.bozo = False  # Add bozo attribute
    mock_feed.entries = []

    for i in range(1, 4):
        entry = MagicMock()
        entry.title = f"Article {i}"
        entry.link = f"https://example.com/{i}"
        entry.published_parsed = (2025, 1, i, 12, 0, 0, 0, 0, 0)
        entry.summary = f"Summary {i}"
        entry.content = []
        entry.get = MagicMock(
            side_effect=lambda k, d=None: {
                "published_parsed": entry.published_parsed,
                "published": None,
            }.get(k, d)
        )
        mock_feed.entries.append(entry)

    with patch("better_morning.rss_fetcher.feedparser.parse", return_value=mock_feed):
        from better_morning.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(collection_config.feeds)
        articles = fetcher.fetch_articles(collection_config.name)

    assert len(articles) == 3
    assert all(a.title.startswith("Article") for a in articles)


@pytest.mark.asyncio
async def test_error_handling_in_collection(tmp_path, monkeypatch):
    """Test that collection errors are handled gracefully"""
    monkeypatch.chdir(tmp_path)

    collection_path = tmp_path / "bad_collection.toml"
    _write_toml(
        collection_path,
        """
name = "Bad Collection"

[[feeds]]
url = "https://invalid-domain-does-not-exist.com/rss"
name = "Bad Feed"
""",
    )

    global_config = GlobalConfig()
    collection_config = load_collection(str(collection_path), global_config)

    from better_morning.rss_fetcher import RSSFetcher

    fetcher = RSSFetcher(collection_config.feeds)
    articles = fetcher.fetch_articles(collection_config.name)

    # Should return empty list without crashing
    assert len(articles) == 0

    # Check that failure was recorded
    report = fetcher.get_fetch_report()
    assert len(report["failed"]) == 1
    assert report["success_rate"] == 0


@pytest.mark.asyncio
async def test_history_prevents_duplicate_processing(tmp_path, monkeypatch):
    """Test that articles in history are not re-processed"""
    monkeypatch.chdir(tmp_path)

    collection_name = "Test"
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    history_file = history_dir / f"{collection_name}_articles.json"

    # Pre-populate history with one article
    history_data = [
        {
            "id": "https://example.com/1",
            "title": "Article 1",
            "link": "https://example.com/1",
            "published_date": "2025-01-01T00:00:00+00:00",
            "summary": "Summary 1",
        }
    ]
    history_file.write_text(json.dumps(history_data), encoding="utf-8")

    # Create collection
    collection_path = tmp_path / "test.toml"
    _write_toml(
        collection_path,
        """
name = "Test"

[[feeds]]
url = "https://example.com/rss"
name = "Test Feed"
""",
    )

    global_config = GlobalConfig()
    collection_config = load_collection(str(collection_path), global_config)

    # Mock RSS with 2 articles (one is already in history)
    mock_feed = MagicMock()
    mock_feed.status = 200
    mock_feed.bozo = False
    mock_feed.entries = []

    for i, (title, link, date_tuple) in enumerate(
        [
            ("Article 1", "https://example.com/1", (2025, 1, 1, 0, 0, 0, 0, 0, 0)),
            ("Article 2", "https://example.com/2", (2025, 1, 2, 0, 0, 0, 0, 0, 0)),
        ]
    ):
        entry = MagicMock()
        entry.title = title
        entry.link = link
        entry.published_parsed = date_tuple
        entry.summary = f"Summary {i + 1}"
        entry.content = []
        entry.get = MagicMock(
            side_effect=lambda k, d=None, pp=date_tuple: {
                "published_parsed": pp,
                "published": None,
            }.get(k, d)
        )
        mock_feed.entries.append(entry)

    with patch("better_morning.rss_fetcher.feedparser.parse", return_value=mock_feed):
        from better_morning.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(collection_config.feeds)
        articles = fetcher.fetch_articles(collection_config.name)

    # Only the new article should be returned
    assert len(articles) == 1
    assert articles[0].id == "https://example.com/2"


@pytest.mark.asyncio
async def test_max_age_filtering(tmp_path, monkeypatch):
    """Test that max_age filters out old articles"""
    monkeypatch.chdir(tmp_path)

    collection_path = tmp_path / "test.toml"
    _write_toml(
        collection_path,
        """
name = "Test"
max_age = "2d"

[[feeds]]
url = "https://example.com/rss"
name = "Test Feed"
""",
    )

    global_config = GlobalConfig()
    collection_config = load_collection(str(collection_path), global_config)

    # Mock RSS with articles from different dates
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    mock_feed = MagicMock()
    mock_feed.status = 200
    mock_feed.bozo = False
    mock_feed.entries = []

    recent_date = (now - timedelta(days=1)).timetuple()[:9]
    old_date = (now - timedelta(days=5)).timetuple()[:9]

    for title, link, date_tuple in [
        ("Recent Article", "https://example.com/recent", recent_date),
        ("Old Article", "https://example.com/old", old_date),
    ]:
        entry = MagicMock()
        entry.title = title
        entry.link = link
        entry.published_parsed = date_tuple
        entry.summary = title.split()[0]
        entry.content = []
        entry.get = MagicMock(
            side_effect=lambda k, d=None, pp=date_tuple: {
                "published_parsed": pp,
                "published": None,
            }.get(k, d)
        )
        mock_feed.entries.append(entry)

    with patch("better_morning.rss_fetcher.feedparser.parse", return_value=mock_feed):
        from better_morning.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(collection_config.feeds)
        articles = fetcher.fetch_articles(collection_config.name, max_age="2d")

    # Only the recent article should pass the filter
    assert len(articles) == 1
    assert articles[0].title == "Recent Article"
