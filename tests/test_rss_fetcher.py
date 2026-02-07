from datetime import datetime, timedelta, timezone

from better_morning.rss_fetcher import RSSFetcher


def test_parse_time_span():
    fetcher = RSSFetcher(feeds=[])

    assert fetcher._parse_time_span("2d") == timedelta(days=2)
    assert fetcher._parse_time_span("1h") == timedelta(hours=1)
    assert fetcher._parse_time_span("30m") == timedelta(minutes=30)


def test_is_article_too_old_handles_naive_datetime():
    fetcher = RSSFetcher(feeds=[])

    cutoff = datetime(2025, 1, 2, tzinfo=timezone.utc)
    article_date = datetime(2025, 1, 1)

    assert fetcher._is_article_too_old(article_date, cutoff) is True
