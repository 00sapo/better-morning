from typing import List
from datetime import datetime

from .rss_fetcher import Article

class DocumentGenerator:
    def generate_markdown_digest(self, articles: List[Article], date: datetime) -> str:
        if not articles:
            return "# Daily News Digest\n\nNo articles to report for today."

        digest_content = f"# Daily News Digest - {date.strftime("%Y-%m-%d")}\n\n"

        for article in articles:
            digest_content += f"## [{article.title}]({article.link})\n\n"
            if article.summary:
                digest_content += f"{article.summary}\n\n"
            else:
                digest_content += "*No summary available.*\n\n"
            digest_content += "---\n\n" # Separator for articles

        return digest_content
