from datetime import datetime

import pytest

from better_morning.config import GlobalConfig, load_collection


def _write_collection_toml(path, content):
    path.write_text(content, encoding="utf-8")


def test_collection_follow_article_links_override(tmp_path):
    collection_path = tmp_path / "collection.toml"
    _write_collection_toml(
        collection_path,
        """
name = "Test Collection"
follow_article_links = true

[[feeds]]
url = "https://example.com/rss"
name = "Example Feed"
follow_article_links = false
""",
    )

    collection = load_collection(str(collection_path), GlobalConfig())

    assert collection.content_extraction_settings.follow_article_links is True
    assert collection.feeds[0].follow_article_links is False


def test_invalid_max_age_raises(tmp_path):
    collection_path = tmp_path / "collection.toml"
    _write_collection_toml(
        collection_path,
        """
name = "Test Collection"
max_age = "2weeks"

[[feeds]]
url = "https://example.com/rss"
""",
    )

    with pytest.raises(ValueError):
        load_collection(str(collection_path), GlobalConfig())


def test_filter_settings_collection_and_feed_override(tmp_path):
    collection_path = tmp_path / "collection.toml"
    _write_collection_toml(
        collection_path,
        """
name = "Test Collection"

[filter_settings]
filter_query = "Include only policy news"
filter_model = "openai/gpt-4o"

[[feeds]]
url = "https://example.com/rss"
name = "Example Feed"
filter_query = "Include only EU policy updates"
filter_model = "openai/gpt-4o-mini"
""",
    )

    collection = load_collection(str(collection_path), GlobalConfig())

    assert collection.filter_settings.filter_query == "Include only policy news"
    assert collection.filter_settings.filter_model == "openai/gpt-4o"
    assert collection.feeds[0].filter_query == "Include only EU policy updates"
    assert collection.feeds[0].filter_model == "openai/gpt-4o-mini"
