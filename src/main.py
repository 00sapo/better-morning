import os
from datetime import datetime

from better_morning.config import load_config
from better_morning.rss_fetcher import RSSFetcher
from better_morning.content_extractor import ContentExtractor
from better_morning.llm_summarizer import LLMSummarizer
from better_morning.document_generator import DocumentGenerator

def main():
    print("Starting better-morning daily digest generation...")

    # 1. Load configuration
    config_path = os.getenv("BETTER_MORNING_CONFIG", "collections/example1.toml")
    try:
        collection_config = load_config(config_path)
        print(f"Loaded configuration for collection: {collection_config.name}")
    except Exception as e:
        print(f"Failed to load configuration from {config_path}: {e}")
        return

    # 2. Fetch RSS articles
    rss_fetcher = RSSFetcher(feeds=collection_config.feeds)
    articles = rss_fetcher.fetch_articles()
    print(f"Fetched {len(articles)} articles.")

    if not articles:
        print("No new articles found. Exiting.")
        return

    # 3. Extract content and summarize
    content_extractor = ContentExtractor()
    # For LLMSummarizer, you might want to get the model and API key from config or environment
    llm_summarizer = LLMSummarizer(model=os.getenv("LLM_MODEL", "gpt-4o"), api_key=os.getenv("LLM_API_KEY"))

    summarized_articles = []
    for article in articles:
        print(f"Processing article: {article.title}")
        full_text = content_extractor.extract_article_text(article)
        if full_text:
            summary = llm_summarizer.summarize_text(article.title, full_text)
            if summary:
                article.summary = summary
                summarized_articles.append(article)
            else:
                print(f"Could not summarize article: {article.title}")
        else:
            print(f"Could not extract content for article: {article.title}")

    print(f"Successfully summarized {len(summarized_articles)} articles.")

    if not summarized_articles:
        print("No articles were summarized. Exiting.")
        return

    # 4. Generate Markdown digest
    document_generator = DocumentGenerator()
    today = datetime.now()
    markdown_digest = document_generator.generate_markdown_digest(summarized_articles, today)

    # 5. Output the digest (for now, print to console)
    print("\n--- Generated Daily Digest ---\n")
    print(markdown_digest)
    print("\n--- End of Digest ---\n")


if __name__ == "__main__":
    main()
