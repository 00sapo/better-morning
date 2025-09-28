import requests
from typing import Optional, List
import magic
import trafilatura

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

    def get_content(self, article: Article) -> Article:
        if not self.settings.follow_article_links:
            article.content = article.summary
            return article

        try:
            print(f"Fetching content for: {article.title} from {article.link}")
            response = requests.get(str(article.link), timeout=15)
            response.raise_for_status()

            # Use python-magic to reliably determine the content type from the response body
            mime_type = magic.from_buffer(response.content, mime=True)
            article.content_type = mime_type

            if "application/pdf" in mime_type:
                print(f"Identified PDF content for: {article.title}")
                article.raw_content = response.content
                # The text `content` field can be a short placeholder.
                article.content = f"PDF document with title '{article.title}' is attached for summarization."
            elif "text/html" in mime_type:
                print(f"Identified HTML content for: {article.title}")
                article.content = self._extract_from_html(response.text)
            else:
                print(
                    f"Warning: Unsupported content type '{mime_type}' for {article.link}."
                )
                article.content = article.summary  # Fallback

            # Fallback if extraction fails for some reason
            if not article.content and not article.raw_content:
                article.content = article.summary

            return article
        except requests.exceptions.RequestException as e:
            print(f"Error fetching article {article.link}: {e}")
            article.content = article.summary
            return article
        except Exception as e:
            print(
                f"An unexpected error occurred during content extraction for {article.link}: {e}"
            )
            article.content = article.summary
            return article
