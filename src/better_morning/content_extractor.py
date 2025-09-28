import requests
from bs4 import BeautifulSoup
from typing import Optional

from .rss_fetcher import Article

class ContentExtractor:
    def extract_article_text(self, article: Article) -> Optional[str]:
        try:
            response = requests.get(str(article.link), timeout=10)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Attempt to find common article content containers
            # This is a basic approach and might need refinement for different websites
            article_body = soup.find('article') or soup.find('main') or soup.find(class_="story-content")

            if article_body:
                paragraphs = article_body.find_all('p')
                text_content = '\n\n'.join([p.get_text() for p in paragraphs])
                return text_content.strip()
            else:
                # Fallback to extracting all paragraph text if no specific article body found
                all_paragraphs = soup.find_all('p')
                text_content = '\n\n'.join([p.get_text() for p in all_paragraphs])
                return text_content.strip() if text_content else None

        except requests.exceptions.RequestException as e:
            print(f"Error fetching article {article.link}: {e}")
            return None
        except Exception as e:
            print(f"Error extracting content from {article.link}: {e}")
            return None
