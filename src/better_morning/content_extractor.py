import requests
from bs4 import BeautifulSoup
from typing import Optional, List
import magic

from .rss_fetcher import Article
from .config import ContentExtractionSettings


class ContentExtractor:
    def __init__(self, settings: ContentExtractionSettings):
        self.settings = settings

    def _extract_from_html(self, html_content: str) -> Optional[str]:
        """Extracts textual content from HTML using BeautifulSoup."""
        soup = BeautifulSoup(html_content, self.settings.parser_type)
        # ... (existing HTML extraction logic remains the same)
        selectors = [
            "div.article-content",
            "div.entry-content",
            "div.post-content",
            "article",
            "main",
            "#content",
            ".content",
            ".story-body",
            ".article-body",
        ]
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                paragraphs = element.find_all("p")
                text_content = "\n\n".join(p.get_text() for p in paragraphs)
                return text_content.strip()
        all_paragraphs = soup.find_all("p")
        return "\n\n".join(p.get_text() for p in all_paragraphs).strip() or None

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
