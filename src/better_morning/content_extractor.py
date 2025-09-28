import requests
from bs4 import BeautifulSoup
from typing import Optional, List

from .rss_fetcher import Article
from .config import ContentExtractionSettings

class ContentExtractor:
    def __init__(self, settings: ContentExtractionSettings):
        self.settings = settings

    def _extract_from_html(self, html_content: str) -> Optional[str]:
        soup = BeautifulSoup(html_content, self.settings.parser_type)

        # Common selectors for main article content
        selectors = [
            'div.article-content',
            'div.entry-content',
            'div.post-content',
            'article',
            'main',
            '#content',
            '.content',
            '.story-body',
            '.article-body',
        ]

        for selector in selectors:
            element = None
            if selector.startswith('#'):
                element = soup.find(id=selector[1:])
            elif selector.startswith('.'):
                element = soup.find(class_=selector[1:])
            else:
                element = soup.find(selector)

            if element:
                paragraphs = element.find_all('p')
                text_content = '\n\n'.join([p.get_text() for p in paragraphs])
                return text_content.strip()

        # Fallback: if no specific article body found, extract all paragraph text
        all_paragraphs = soup.find_all('p')
        text_content = '\n\n'.join([p.get_text() for p in all_paragraphs])
        return text_content.strip() if text_content else None

    def get_content(self, article: Article) -> Article:
        if not self.settings.follow_article_links:
            # Use RSS summary if not following links
            article.content = article.summary
            return article

        # Otherwise, fetch content from the article link
        try:
            print(f"Fetching full content for: {article.title} from {article.link}")
            response = requests.get(str(article.link), timeout=10)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            article.content = self._extract_from_html(response.text)
            return article
        except requests.exceptions.RequestException as e:
            print(f"Error fetching article {article.link}: {e}")
            article.content = article.summary # Fallback to summary on error
            return article
        except Exception as e:
            print(f"Error extracting content from {article.link}: {e}")
            article.content = article.summary # Fallback to summary on error
            return article
