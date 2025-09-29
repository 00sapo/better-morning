from typing import Optional
import trafilatura
from playwright.async_api import async_playwright, Browser
import requests
import asyncio
import os
import re
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
import magic

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

    async def _fetch_with_requests(self, url: str) -> requests.Response:
        """Fetches content using requests, suitable for static pages."""
        try:
            loop = asyncio.get_running_loop()

            # Direct handling for Google Scholar links
            parsed_url = urlparse(url)
            if "scholar.google.com" in parsed_url.netloc:
                query_params = parse_qs(parsed_url.query)
                if "url" in query_params:
                    direct_url = query_params["url"][0]
                    print(f"  -> Google Scholar link found, fetching direct URL: {direct_url}")
                    response = await loop.run_in_executor(
                        None,
                        lambda: requests.get(
                            direct_url,
                            headers={"User-Agent": self.user_agent},
                            timeout=15,
                            allow_redirects=True,
                        ),
                    )
                    response.raise_for_status()
                    return response

            # Standard fetch for all other URLs
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    timeout=15,
                    allow_redirects=True,
                ),
            )
            response.raise_for_status()

            # Handle potential meta refresh redirects (e.g., from Google Scholar)
            content_type_header = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type_header:
                soup = BeautifulSoup(response.text, "html.parser")
                meta_tag = soup.find("meta", attrs={"http-equiv": "refresh"})
                if meta_tag and meta_tag.get("content"):
                    content = meta_tag["content"]
                    match = re.search(r"url=['\"]?([^'\" >]+)", content, re.IGNORECASE)
                    if match:
                        redirect_url = match.group(1)
                        print(f"  -> Meta refresh found, fetching final URL: {redirect_url}")
                        final_response = await loop.run_in_executor(
                            None,
                            lambda: requests.get(
                                redirect_url,
                                headers={"User-Agent": self.user_agent},
                                timeout=15,
                                allow_redirects=True,
                            ),
                        )
                        final_response.raise_for_status()
                        return final_response

            return response
        except requests.RequestException as e:
            print(f"Info: requests fetch failed for {url}: {e}.")
            return None

    async def get_content(self, article: Article) -> Article:
        # If RSS summary is long enough (≥400 words), use it without fetching the article
        if article.summary and len(article.summary.split()) >= 400:
            print(f"Info: Using RSS summary for '{article.title}' as it has {len(article.summary.split())} words (≥400).")
            article.content = article.summary
            article.content_type = "text/plain"
            return article

        # If RSS summary is short (<400 words), fetch the main article content
        print(f"Info: RSS summary for '{article.title}' has only {len(article.summary.split()) if article.summary else 0} words (<400). Fetching article content...")

        # First, try fetching with requests
        response = await self._fetch_with_requests(str(article.link))

        if response:
            content_type_header = response.headers.get("Content-Type", "").lower()
            final_url = response.url
            
            # Use python-magic to detect actual content type from the content
            try:
                detected_mime = magic.from_buffer(response.content, mime=True)
                print(f"Content analysis for '{article.title}': Header={content_type_header}, Detected={detected_mime}, URL={final_url}")
            except Exception as e:
                print(f"Warning: python-magic detection failed for '{article.title}': {e}")
                detected_mime = ""
            
            # Check for PDF using multiple methods: magic detection, header, and URL
            is_pdf = (
                detected_mime == "application/pdf" or
                "application/pdf" in content_type_header or
                "pdf" in content_type_header or
                str(final_url).lower().endswith('.pdf')
            )
            
            if is_pdf:
                print(f"PDF content confirmed for '{article.title}'.")
                article.raw_content = response.content
                article.content_type = "application/pdf"
                # No HTML content to process, so we can return early.
                return article
            else:
                html_content = response.text
        else:
            html_content = None

        # If requests fails or content is not PDF, fall back to Playwright for HTML
        if html_content is None:
            print("Falling back to Playwright.")
            if not self.browser:
                raise RuntimeError("Browser not started. Call start_browser() first.")
            try:
                page = await self.browser.new_page(user_agent=self.user_agent)
                print(
                    f"Fetching content with Playwright for: {article.title} from {article.link}"
                )
                await page.goto(str(article.link), timeout=30000)
                html_content = await page.content()
                await page.close()
            except Exception as e:
                print(f"Error fetching article with Playwright {article.link}: {e}")
                html_content = None  # Ensure html_content is None on failure

        if not html_content:
            article.content = (
                article.summary
            )  # Fallback to summary if all fetching fails
            return article

        # Extract text from the main article
        main_text_content = self._extract_from_html(html_content)
        all_text_contents = [main_text_content] if main_text_content else []

        # If follow_article_links is True, find and fetch content from internal links
        if self.settings.follow_article_links:
            print(f"Following links for '{article.title}'...")
            soup = BeautifulSoup(html_content, "html.parser")
            links_to_follow = []

            # Use the final URL from the response to resolve relative links correctly
            base_url = str(response.url) if response else str(article.link)

            for a_tag in soup.find_all(
                "a", href=True, limit=25
            ):  # Increased limit to find more potential matches
                href = a_tag["href"]
                abs_url = urljoin(base_url, href)

                # Standard filtering for valid, external links
                if not abs_url.startswith("http") or abs_url == base_url:
                    continue

                # If a filter pattern is provided, only follow matching links
                if self.settings.link_filter_pattern:
                    if re.search(self.settings.link_filter_pattern, abs_url):
                        print(f"  -> Link matched filter: {abs_url}")
                        links_to_follow.append(abs_url)
                    else:
                        # Optional: log which links are being skipped for debugging
                        # print(f"  -> Link skipped (no match): {abs_url}")
                        pass
                else:
                    # If no pattern, follow all valid links
                    links_to_follow.append(abs_url)

            unique_links = list(dict.fromkeys(links_to_follow))[
                :5
            ]  # Limit to 5 unique links to avoid excessive requests
            for link in unique_links:
                print(f"  -> Fetching sub-link: {link}")
                sub_response = await self._fetch_with_requests(link)
                if sub_response:
                    sub_content_type = sub_response.headers.get("Content-Type", "").lower()
                    sub_final_url = sub_response.url
                    print(f"  -> Sub-link details: URL={sub_final_url}, Content-Type={sub_content_type}")

                    try:
                        sub_detected_mime = magic.from_buffer(sub_response.content, mime=True)
                    except Exception:
                        sub_detected_mime = ""

                    is_pdf = (
                        sub_detected_mime == "application/pdf"
                        or "application/pdf" in sub_content_type
                        or "pdf" in sub_content_type
                        or str(sub_final_url).lower().endswith(".pdf")
                    )

                    if is_pdf:
                        print(f"PDF content found at sub-link for '{article.title}'.")
                        article.raw_content = sub_response.content
                        article.content_type = "application/pdf"
                        # Found PDF, so we can return
                        return article
                    else:
                        sub_html_content = sub_response.text
                        sub_text_content = self._extract_from_html(sub_html_content)
                        if sub_text_content:
                            all_text_contents.append(sub_text_content)

        # Combine all text content
        article.content = "\n\n--- LINKED CONTENT ---\n\n".join(all_text_contents)
        article.content_type = "text/plain"

        if not article.content:
            article.content = article.summary

        return article
