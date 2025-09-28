import requests
from typing import Optional, List
import magic
import trafilatura
from playwright.async_api import async_playwright

from .rss_fetcher import Article
from .config import ContentExtractionSettings


class ContentExtractor:
    def __init__(self, settings: ContentExtractionSettings):
        self.settings = settings

    def _extract_from_html(self, html_content: str) -> Optional[str]:
        """Extracts main textual content from HTML using the trafilatura library."""
        # trafilatura is a specialized tool for finding and extracting the
        # core article text from a webpage, filtering out boilerplate.
        text_content = trafilatura.extract(html_content, include_comments=False, include_tables=False)
        return text_content.strip() if text_content else None

    async def get_content(self, article: Article) -> Article:
        if not self.settings.follow_article_links:
            article.content = article.summary
            # Ensure content_type is set for consistency, even if it's just text
            article.content_type = "text/plain"
            return article

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                print(f"Fetching content for: {article.title} from {article.link}")
                await page.goto(str(article.link), timeout=60000)
                html_content = await page.content()
                await browser.close()
    
                # We assume the content is HTML since we're using a browser
                article.content_type = "text/html"
                article.content = self._extract_from_html(html_content)
    
                if not article.content:
                    # Fallback to RSS summary if trafilatura fails
                    article.content = article.summary
    
                return article
        except Exception as e:
            print(
                f"Error fetching article with Playwright {article.link}: {e}"
            )
            article.content = None  # Ensure content is None on error
            return article
