import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from better_morning.config import GlobalConfig, LLMSettings
from better_morning.llm_summarizer import LLMSummarizer
from better_morning.rss_fetcher import Article


def test_truncate_text_to_token_limit():
    settings = LLMSettings()
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    text = "12345"
    truncated, was_truncated = summarizer._truncate_text_to_token_limit(text, 1)

    assert was_truncated is True
    assert truncated == "1234"


@pytest.fixture
def sample_articles():
    return [
        Article(
            id=f"test-{i}",
            title=f"Article {i}",
            link=f"https://example.com/{i}",
            published_date=datetime(2025, 1, i, tzinfo=timezone.utc),
            summary=f"Summary {i}",
        )
        for i in range(1, 11)
    ]


@pytest.mark.asyncio
async def test_select_articles_for_fetching_with_llm(sample_articles):
    """Test LLM-based article selection"""
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        n_most_important_news=3,
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    # Mock the LLM response
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(content=json.dumps({"selected_indices": [1, 3, 5]}))
        )
    ]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion", return_value=mock_response
    ):
        selected = await summarizer.select_articles_for_fetching(
            sample_articles, collection_prompt="Test prompt"
        )

    assert len(selected) == 3
    assert selected[0].id == "test-1"
    assert selected[1].id == "test-3"
    assert selected[2].id == "test-5"


@pytest.mark.asyncio
async def test_select_articles_fallback_on_error(sample_articles):
    """Test fallback to most recent articles when LLM fails"""
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        n_most_important_news=3,
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion",
        side_effect=Exception("API error"),
    ):
        selected = await summarizer.select_articles_for_fetching(sample_articles)

    # Should return 9 most recent (3 * n_most_important_news)
    assert len(selected) == 9
    # Most recent should be first
    assert selected[0].id == "test-10"


@pytest.mark.asyncio
async def test_select_all_articles_when_below_threshold(sample_articles):
    """Test that all articles are selected when count is below threshold"""
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        n_most_important_news=5,  # 3*5 = 15, more than available
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    selected = await summarizer.select_articles_for_fetching(sample_articles)

    # Should return all without calling LLM
    assert len(selected) == 10


@pytest.mark.asyncio
async def test_summarize_text_article():
    """Test text article summarization"""
    settings = LLMSettings(
        light_model="openai/gpt-3.5-turbo",
        k_words_each_summary=50,
        output_language="English",
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    article = Article(
        id="test-1",
        title="Test Article",
        link="https://example.com/1",
        published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        content="This is a long article content that needs to be summarized.",
        feed_name="Test Feed",
    )

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Summarized content"))]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion", return_value=mock_response
    ):
        result = await summarizer.summarize_text(article)

    assert "Summarized content" in result.summary
    assert "[Test Feed]" in result.summary


@pytest.mark.asyncio
async def test_summarize_pdf_article():
    """Test PDF article summarization with multimodal"""
    settings = LLMSettings(
        light_model="openai/gpt-4o",
        k_words_each_summary=50,
        output_language="English",
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    article = Article(
        id="test-1",
        title="Test PDF",
        link="https://example.com/1.pdf",
        published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        raw_content=b"%PDF-1.4 test content",
        content_type="application/pdf",
        feed_name="Test Feed",
    )

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="PDF summary"))]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion", return_value=mock_response
    ):
        result = await summarizer.summarize_text(article)

    assert "PDF summary" in result.summary


@pytest.mark.asyncio
async def test_summarize_articles_collection():
    """Test collection-level summarization"""
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        light_model="openai/gpt-3.5-turbo",
        n_most_important_news=2,
        k_words_each_summary=50,
        output_language="English",
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    articles = [
        Article(
            id="test-1",
            title="Article 1",
            link="https://example.com/1",
            published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            content="Content 1",
            feed_name="Feed 1",
        ),
        Article(
            id="test-2",
            title="Article 2",
            link="https://example.com/2",
            published_date=datetime(2025, 1, 2, tzinfo=timezone.utc),
            content="Content 2",
            feed_name="Feed 2",
        ),
    ]

    mock_summary_response = MagicMock()
    mock_summary_response.choices = [
        MagicMock(message=MagicMock(content="Individual summary"))
    ]

    mock_collection_response = MagicMock()
    mock_collection_response.choices = [
        MagicMock(message=MagicMock(content="Collection overview"))
    ]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion",
        side_effect=[
            mock_summary_response,
            mock_summary_response,
            mock_collection_response,
        ],
    ):
        collection_summary, summarized = await summarizer.summarize_articles_collection(
            articles, collection_prompt="Test collection"
        )

    assert collection_summary == "Collection overview"
    assert len(summarized) == 2


@pytest.mark.asyncio
async def test_filter_article_include_true():
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    article = Article(
        id="test-1",
        title="Test Article",
        link="https://example.com/1",
        published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        content="Some content",
    )

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=json.dumps({"include": True})))
    ]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion", return_value=mock_response
    ):
        include = await summarizer.filter_article(
            article, filter_query="Include this", model_name="openai/gpt-4o"
        )

    assert include is True


@pytest.mark.asyncio
async def test_filter_article_retry_and_fallback_to_json_extract():
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    article = Article(
        id="test-1",
        title="Test Article",
        link="https://example.com/1",
        published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        content="Some content",
    )

    first_response = MagicMock()
    first_response.choices = [MagicMock(message=MagicMock(content="Not JSON"))]

    second_response = MagicMock()
    second_response.choices = [
        MagicMock(message=MagicMock(content='Here is JSON: {"include": true}'))
    ]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion",
        side_effect=[first_response, second_response],
    ):
        include = await summarizer.filter_article(
            article, filter_query="Include this", model_name="openai/gpt-4o"
        )

    assert include is True


@pytest.mark.asyncio
async def test_filter_article_invalid_response_excludes():
    settings = LLMSettings(
        reasoner_model="openai/gpt-4o",
        api_key="test-key",
    )
    global_config = GlobalConfig()
    summarizer = LLMSummarizer(settings, global_config)

    article = Article(
        id="test-1",
        title="Test Article",
        link="https://example.com/1",
        published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        content="Some content",
    )

    first_response = MagicMock()
    first_response.choices = [MagicMock(message=MagicMock(content="Nope"))]

    second_response = MagicMock()
    second_response.choices = [MagicMock(message=MagicMock(content="Still not JSON"))]

    with patch(
        "better_morning.llm_summarizer.litellm.acompletion",
        side_effect=[first_response, second_response],
    ):
        include = await summarizer.filter_article(
            article, filter_query="Include this", model_name="openai/gpt-4o"
        )

    assert include is False
