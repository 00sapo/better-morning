import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from better_morning.content_extractor import ContentExtractor
from better_morning.config import ContentExtractionSettings
from better_morning.rss_fetcher import Article


@pytest.fixture
def extractor():
    settings = ContentExtractionSettings()
    return ContentExtractor(settings)


@pytest.fixture
def sample_article():
    return Article(
        id="test-123",
        title="Test Article",
        link="https://example.com/article",
        published_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        summary="Short summary",
    )


@pytest.mark.asyncio
async def test_use_rss_summary_when_long_enough(extractor, sample_article):
    """Test that we skip fetching when RSS summary is >= 400 words"""
    sample_article.summary = " ".join(["word"] * 400)

    result = await extractor.get_content(sample_article)

    assert len(result) == 1
    assert result[0].content == sample_article.summary
    assert result[0].content_type == "text/plain"


@pytest.mark.asyncio
async def test_fetch_content_when_summary_short(extractor, sample_article):
    """Test that we fetch full content when RSS summary is < 400 words"""
    sample_article.summary = "Short"

    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "text/html"}
    mock_response.url = str(sample_article.link)
    mock_response.text = "<html><body><p>Full article content here</p></body></html>"
    mock_response.content = (
        b"<html><body><p>Full article content here</p></body></html>"
    )

    async def mock_fetch(url):
        return mock_response

    with patch.object(extractor, "_fetch_with_requests", side_effect=mock_fetch):
        with patch.object(
            extractor, "_extract_from_html", return_value="Full article content here"
        ):
            result = await extractor.get_content(sample_article)

    assert len(result) == 1
    assert result[0].content == "Full article content here"
    assert result[0].content_type == "text/plain"


@pytest.mark.asyncio
async def test_detect_pdf_content(extractor, sample_article):
    """Test PDF detection and handling"""
    sample_article.summary = "Short"

    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/pdf"}
    mock_response.url = str(sample_article.link)
    mock_response.content = b"%PDF-1.4 fake pdf content"

    async def mock_fetch(url):
        return mock_response

    with patch.object(extractor, "_fetch_with_requests", side_effect=mock_fetch):
        with patch(
            "better_morning.content_extractor.magic.from_buffer",
            return_value="application/pdf",
        ):
            result = await extractor.get_content(sample_article)

    assert len(result) == 1
    assert result[0].raw_content == b"%PDF-1.4 fake pdf content"
    assert result[0].content_type == "application/pdf"


@pytest.mark.asyncio
async def test_follow_article_links_when_enabled(extractor, sample_article):
    """Test that internal links are followed when follow_article_links is True"""
    sample_article.summary = "Short"
    sample_article.follow_article_links = True

    main_html = """
    <html>
    <body>
        <p>Main article content</p>
        <a href="https://example.com/linked">Link 1</a>
    </body>
    </html>
    """

    linked_html = """
    <html>
    <head><title>Linked Article Title</title></head>
    <body><p>Linked content here</p></body>
    </html>
    """

    mock_main_response = MagicMock()
    mock_main_response.headers = {"Content-Type": "text/html"}
    mock_main_response.url = str(sample_article.link)
    mock_main_response.text = main_html
    mock_main_response.content = main_html.encode()

    mock_linked_response = MagicMock()
    mock_linked_response.headers = {"Content-Type": "text/html"}
    mock_linked_response.url = "https://example.com/linked"
    mock_linked_response.text = linked_html
    mock_linked_response.content = linked_html.encode()

    async def fetch_side_effect(url):
        if url == str(sample_article.link):
            return mock_main_response
        elif url == "https://example.com/linked":
            return mock_linked_response
        return None

    with patch.object(extractor, "_fetch_with_requests", side_effect=fetch_side_effect):
        with patch.object(extractor, "_extract_from_html") as mock_extract:
            mock_extract.side_effect = ["Main article content", "Linked content here"]
            with patch(
                "better_morning.content_extractor.magic.from_buffer",
                return_value="text/html",
            ):
                result = await extractor.get_content(sample_article)

    # Should return main article + 1 linked article
    assert len(result) == 2
    assert result[0].content == "Main article content"
    assert result[1].title == "Linked Article Title"
    assert result[1].content == "Linked content here"


@pytest.mark.asyncio
async def test_merge_linked_content_when_requested(extractor, sample_article):
    """Test that linked content can be merged into parent when requested"""
    sample_article.summary = "Short"
    sample_article.follow_article_links = True

    main_html = """
    <html>
    <body>
        <p>Main article content</p>
        <a href="https://example.com/linked">Link 1</a>
    </body>
    </html>
    """

    linked_html = """
    <html>
    <head><title>Linked Article Title</title></head>
    <body><p>Linked content here</p></body>
    </html>
    """

    mock_main_response = MagicMock()
    mock_main_response.headers = {"Content-Type": "text/html"}
    mock_main_response.url = str(sample_article.link)
    mock_main_response.text = main_html
    mock_main_response.content = main_html.encode()

    mock_linked_response = MagicMock()
    mock_linked_response.headers = {"Content-Type": "text/html"}
    mock_linked_response.url = "https://example.com/linked"
    mock_linked_response.text = linked_html
    mock_linked_response.content = linked_html.encode()

    async def fetch_side_effect(url):
        if url == str(sample_article.link):
            return mock_main_response
        elif url == "https://example.com/linked":
            return mock_linked_response
        return None

    with patch.object(extractor, "_fetch_with_requests", side_effect=fetch_side_effect):
        with patch.object(extractor, "_extract_from_html") as mock_extract:
            mock_extract.side_effect = ["Main article content", "Linked content here"]
            with patch(
                "better_morning.content_extractor.magic.from_buffer",
                return_value="text/html",
            ):
                result = await extractor.get_content(
                    sample_article, merge_linked_content=True
                )

    assert len(result) == 1
    assert "Main article content" in result[0].content
    assert "Linked content here" in result[0].content


@pytest.mark.asyncio
async def test_rate_limiting_applied(extractor, sample_article):
    """Test that rate limiting is applied between requests"""
    sample_article.summary = "Short"

    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "text/html"}
    mock_response.url = str(sample_article.link)
    mock_response.text = "<html><body>Content</body></html>"
    mock_response.content = b"<html><body>Content</body></html>"

    rate_limit_called = False

    async def mock_rate_limit(domain, min_delay=0.5, max_delay=2.0):
        nonlocal rate_limit_called
        rate_limit_called = True

    async def mock_fetch(url):
        return mock_response

    with patch.object(extractor, "_fetch_with_requests", side_effect=mock_fetch):
        with patch.object(extractor, "_extract_from_html", return_value="Content"):
            with patch.object(
                extractor, "_apply_rate_limit", side_effect=mock_rate_limit
            ):
                await extractor.get_content(sample_article)

    assert rate_limit_called


@pytest.mark.skip(reason="Test takes too long with asyncio timeout")
@pytest.mark.asyncio
async def test_timeout_handling(extractor, sample_article):
    """Test that timeouts are handled gracefully"""
    sample_article.summary = "Short"

    # Simulate a very slow fetch that causes timeout
    async def slow_fetch(url):
        import asyncio

        await asyncio.sleep(200)  # Exceeds the 120s timeout
        return None

    # Mock rate limiting to avoid delays
    async def mock_rate_limit(domain, min_delay=0.5, max_delay=2.0):
        pass

    with patch.object(extractor, "_fetch_with_requests", side_effect=slow_fetch):
        with patch.object(extractor, "_apply_rate_limit", side_effect=mock_rate_limit):
            result = await extractor.get_content(sample_article)

    # Should fallback to summary (content message includes "timeout")
    assert len(result) == 1
    assert (
        "timeout" in result[0].content.lower()
        or result[0].content == sample_article.summary
    )
