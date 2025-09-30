from typing import Optional, List
import trafilatura
from playwright.async_api import async_playwright, Browser
import requests
import asyncio
import os
import re
import time
import random
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
import magic
from pydantic import HttpUrl

from .rss_fetcher import Article
from .config import ContentExtractionSettings


class ContentExtractor:
    def __init__(self, settings: ContentExtractionSettings):
        self.settings = settings
        self.browser: Optional[Browser] = None
        self._playwright = None
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
        ]
        # Track domains for rate limiting
        self._domain_last_access = {}
        # Track active pages for resource management
        self._active_pages = 0
        self._max_concurrent_pages = 5

    @property
    def user_agent(self):
        return random.choice(self.user_agents)

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

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL for rate limiting purposes."""
        try:
            return urlparse(str(url)).netloc.lower()
        except Exception:
            return "unknown"

    async def _apply_rate_limit(self, domain: str, min_delay: float = 0.5, max_delay: float = 2.0):
        """Apply rate limiting per domain with randomized delays."""
        current_time = time.time()
        last_access = self._domain_last_access.get(domain, 0)
        
        # Calculate time since last access to this domain
        time_since_last = current_time - last_access
        
        # Add random delay between min_delay and max_delay seconds
        delay = random.uniform(min_delay, max_delay)
        
        # If we accessed this domain recently, wait additional time
        if time_since_last < delay:
            additional_wait = delay - time_since_last
            print(f"Rate limiting {domain}: waiting {additional_wait:.1f}s")
            await asyncio.sleep(additional_wait)
        
        # Update last access time
        self._domain_last_access[domain] = time.time()

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
                    print(
                        f"  -> Google Scholar link found, fetching direct URL: {direct_url}"
                    )
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
                        print(
                            f"  -> Meta refresh found, fetching final URL: {redirect_url}"
                        )
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

    async def get_content(self, article: Article) -> List[Article]:
        try:
            return await asyncio.wait_for(self._get_content_impl(article), timeout=120.0)
        except asyncio.TimeoutError:
            print(f"Timeout processing article '{article.title}', falling back to RSS summary")
            article.content = article.summary or "Content unavailable due to timeout"
            article.content_type = "text/plain"
            return [article]
    
    async def _get_content_impl(self, article: Article) -> List[Article]:
        overall_start_time = time.time()
        # If RSS summary is long enough (≥400 words), use it without fetching the article
        if article.summary and len(article.summary.split()) >= 400:
            print(
                f"Info: Using RSS summary for '{article.title}' as it has {len(article.summary.split())} words (≥400)."
            )
            article.content = article.summary
            article.content_type = "text/plain"
            return [article]

        # If RSS summary is short (<400 words), fetch the main article content
        print(
            f"Info: RSS summary for '{article.title}' has only {len(article.summary.split()) if article.summary else 0} words (<400). Fetching article content..."
        )

        # Apply rate limiting before fetching
        domain = self._get_domain(str(article.link))
        await self._apply_rate_limit(domain)

        # First, try fetching with requests
        requests_start_time = time.time()
        response = await self._fetch_with_requests(str(article.link))
        requests_duration = time.time() - requests_start_time
        print(f"TIMER: requests fetch for '{article.title}' took {requests_duration:.2f}s")

        html_content = None
        if response:
            content_type_header = response.headers.get("Content-Type", "").lower()
            final_url = response.url

            # Use python-magic to detect actual content type from the content
            try:
                detected_mime = magic.from_buffer(response.content, mime=True)
                print(
                    f"Content analysis for '{article.title}': Header={content_type_header}, Detected={detected_mime}, URL={final_url}"
                )
            except Exception as e:
                print(
                    f"Warning: python-magic detection failed for '{article.title}': {e}"
                )
                detected_mime = ""

            # Check for PDF using multiple methods: magic detection, header, and URL
            is_pdf = (
                detected_mime == "application/pdf"
                or "application/pdf" in content_type_header
                or "pdf" in content_type_header
                or str(final_url).lower().endswith(".pdf")
            )

            if is_pdf:
                print(f"PDF content confirmed for '{article.title}'.")
                article.raw_content = response.content
                article.content_type = "application/pdf"
                # No HTML content to process, so we can return early.
                return [article]
            else:
                html_content = response.text
        
        # If requests fails or content is not PDF, fall back to Playwright for HTML
        if html_content is None:
            playwright_start_time = time.time()
            print("Falling back to Playwright.")
            if not self.browser:
                raise RuntimeError("Browser not started. Call start_browser() first.")
            page = None
            try:
                # Limit concurrent browser pages
                if self._active_pages >= self._max_concurrent_pages:
                    print(f"Too many active pages ({self._active_pages}), waiting...")
                    await asyncio.sleep(1.0)
                
                self._active_pages += 1
                page = await self.browser.new_page(user_agent=self.user_agent)
                print(
                    f"Fetching content with Playwright for: {article.title} from {article.link}"
                )
                # Add timeout wrapper for the entire page operation
                await asyncio.wait_for(page.goto(str(article.link), timeout=30000), timeout=45.0)
                html_content = await asyncio.wait_for(page.content(), timeout=10.0)
            except asyncio.TimeoutError:
                print(f"Timeout fetching article with Playwright {article.link}")
                html_content = None
            except Exception as e:
                print(f"Error fetching article with Playwright {article.link}: {e}")
                html_content = None
            finally:
                if page:
                    try:
                        await page.close()
                        self._active_pages = max(0, self._active_pages - 1)
                    except Exception as e:
                        print(f"Warning: Failed to close page: {e}")
                        self._active_pages = max(0, self._active_pages - 1)
            playwright_duration = time.time() - playwright_start_time
            print(f"TIMER: Playwright fetch for '{article.title}' took {playwright_duration:.2f}s")

        if not html_content:
            article.content = (
                article.summary
            )  # Fallback to summary if all fetching fails
            return [article]

        # Extract text from the main article
        trafilatura_start_time = time.time()
        main_text_content = self._extract_from_html(html_content)
        trafilatura_duration = time.time() - trafilatura_start_time
        print(f"TIMER: Trafilatura extraction for '{article.title}' took {trafilatura_duration:.2f}s")
        
        # Set the main article content
        article.content = main_text_content or article.summary
        article.content_type = "text/plain"

        # Determine whether to follow links: use article's setting first, then collection's setting
        should_follow_links = (
            article.follow_article_links
            if article.follow_article_links is not None
            else self.settings.follow_article_links
        )

        # If follow_article_links is False, return just the main article
        if not should_follow_links:
            overall_duration = time.time() - overall_start_time
            print(f"TIMER: Total processing for '{article.title}' (no links) took {overall_duration:.2f}s")
            return [article]

        # If follow_article_links is True, create separate articles for each followed link
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
            :30
        ]  # Limit to 30 unique links to avoid excessive requests

        all_articles = [article]  # Start with the main article

        for i, link in enumerate(unique_links):
            print(f"  -> Fetching sub-link: {link}")
            # Apply rate limiting for sub-links too
            sub_domain = self._get_domain(link)
            await self._apply_rate_limit(sub_domain)
            
            sub_response = await self._fetch_with_requests(link)
            if sub_response:
                sub_content_type = sub_response.headers.get("Content-Type", "").lower()
                sub_final_url = sub_response.url
                print(
                    f"  -> Sub-link details: URL={sub_final_url}, Content-Type={sub_content_type}"
                )

                # Create a new article for this linked content
                linked_article = Article(
                    id=f"{article.id}_link_{i + 1}",  # Unique ID based on original article
                    title=f"{article.title} - Linked Content {i + 1}",
                    link=HttpUrl(str(sub_final_url)),
                    source_url=article.source_url,  # Keep the original source
                    published_date=article.published_date,
                    follow_article_links=False,  # Don't follow links recursively
                )

                try:
                    sub_detected_mime = magic.from_buffer(
                        sub_response.content, mime=True
                    )
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
                    linked_article.raw_content = sub_response.content
                    linked_article.content_type = "application/pdf"
                    linked_article.title = f"{article.title} - Linked PDF {i + 1}"
                else:
                    sub_html_content = sub_response.text
                    sub_text_content = self._extract_from_html(sub_html_content)
                    if sub_text_content:
                        linked_article.content = sub_text_content
                        linked_article.content_type = "text/plain"

                        # Try to extract a better title from the linked page
                        sub_soup = BeautifulSoup(sub_html_content, "html.parser")
                        title_tag = sub_soup.find("title")
                        if title_tag and title_tag.get_text(strip=True):
                            linked_article.title = title_tag.get_text(strip=True)
                    else:
                        # Skip this link if no content could be extracted
                        continue

                all_articles.append(linked_article)
        
        overall_duration = time.time() - overall_start_time
        print(f"TIMER: Total processing for '{article.title}' (with links) took {overall_duration:.2f}s")
        return all_articles
