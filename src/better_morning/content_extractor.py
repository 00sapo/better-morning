from typing import Optional
import trafilatura
from playwright.async_api import async_playwright, Browser

from .rss_fetcher import Article
from .config import ContentExtractionSettings


class ContentExtractor:
    def __init__(self, settings: ContentExtractionSettings):
        self.settings = settings
        self.browser: Optional[Browser] = None
        self._playwright = None

    async def start_browser(self):
        """Starts the Playwright browser instance."""
        if not self.browser:
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(args=["--no-sandbox"])

    async def close_browser(self):
        """Closes the Playwright browser instance."""
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    def _extract_from_html(self, html_content: str) -> Optional[str]:
        """Extracts main textual content from HTML using the trafilatura library."""
        # trafilatura is a specialized tool for finding and extracting the
        # core article text from a webpage, filtering out boilerplate.
        text_content = trafilatura.extract(
            html_content, include_comments=False, include_tables=False
        )
        return text_content.strip() if text_content else None

    async def get_content(self, article: Article) -> Article:
        if not self.settings.follow_article_links:
            article.content = article.summary
            article.content_type = "text/plain"
            return article

        if not self.browser:
            raise RuntimeError(
                "Browser is not started. Call start_browser() before using get_content."
            )

        try:
            page = await self.browser.new_page()
            print(f"Fetching content for: {article.title} from {article.link}")
            await page.goto(str(article.link), timeout=60000)
            html_content = await page.content()
            await page.close()

            article.content_type = "text/html"
            article.content = self._extract_from_html(html_content)

            if not article.content:
                article.content = article.summary

            return article
        except Exception as e:
            print(f"Error fetching article with Playwright {article.link}: {e}")
            article.content = None
            return article
