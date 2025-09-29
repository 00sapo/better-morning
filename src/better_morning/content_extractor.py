from typing import Optional
import trafilatura
from playwright.async_api import async_playwright, Browser
import requests
import asyncio
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from .rss_fetcher import Article
from .config import ContentExtractionSettings


class ContentExtractor:
    def __init__(self, settings: ContentExtractionSettings):
        self.settings = settings
        self.browser: Optional[Browser] = None
        self._playwright = None
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )

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
        text_content = trafilatura.extract(
            html_content, include_comments=False, include_tables=False
        )
        return text_content.strip() if text_content else None

    async def _fetch_with_requests(self, url: str) -> Optional[str]:
        """Fetches content using requests, suitable for static pages."""
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    timeout=15,
                ),
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            print(f"Info: requests fetch failed for {url}: {e}.")
            return None

    async def get_content(self, article: Article) -> Article:
        # If summary is long enough, use it without fetching
        if article.summary and len(article.summary.split()) > 400:
            print(f"Info: Using RSS summary for '{article.title}' as it's over 400 words.")
            article.content = article.summary
            article.content_type = "text/plain"
            return article

        # First, try fetching with requests
        html_content = await self._fetch_with_requests(str(article.link))

        # If requests fails, fall back to Playwright
        if html_content is None:
            print("Falling back to Playwright.")
            if not self.browser:
                raise RuntimeError("Browser not started. Call start_browser() first.")
            try:
                page = await self.browser.new_page(user_agent=self.user_agent)
                print(f"Fetching content with Playwright for: {article.title} from {article.link}")
                await page.goto(str(article.link), timeout=30000)
                html_content = await page.content()
                await page.close()
            except Exception as e:
                print(f"Error fetching article with Playwright {article.link}: {e}")
                html_content = None # Ensure html_content is None on failure

        if not html_content:
            article.content = article.summary # Fallback to summary if all fetching fails
            return article

        # Debug: Save HTML content to a file
        debug_dir = "debug"
        os.makedirs(debug_dir, exist_ok=True)
        sanitized_title = re.sub(r'[^\w\s-]', '', article.title).strip()
        sanitized_title = re.sub(r'[-\s]+', '-', sanitized_title)
        filename = f"{debug_dir}/debug_{sanitized_title[:50]}.html"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"Debug HTML for '{article.title}' saved to {filename}")
        except Exception as e:
            print(f"Error writing debug file for '{article.title}': {e}")

        # Extract text from the main article
        main_text_content = self._extract_from_html(html_content)
        all_text_contents = [main_text_content] if main_text_content else []

        # If follow_article_links is True, find and fetch content from internal links
        if self.settings.follow_article_links:
            print(f"Following links for '{article.title}'...")
            soup = BeautifulSoup(html_content, "html.parser")
            links_to_follow = []
            for a_tag in soup.find_all("a", href=True, limit=15):
                href = a_tag["href"]
                abs_url = urljoin(str(article.link), href)
                if abs_url.startswith("http") and abs_url != str(article.link):
                    links_to_follow.append(abs_url)

            unique_links = list(dict.fromkeys(links_to_follow))[:5]
            for link in unique_links:
                print(f"  -> Fetching sub-link: {link}")
                sub_html_content = await self._fetch_with_requests(link)
                if sub_html_content:
                    sub_text_content = self._extract_from_html(sub_html_content)
                    if sub_text_content:
                        all_text_contents.append(sub_text_content)

        # Combine all text content
        article.content = "\n\n--- LINKED CONTENT ---\n\n".join(all_text_contents)
        article.content_type = "text/plain"

        if not article.content:
            article.content = article.summary

        return article
