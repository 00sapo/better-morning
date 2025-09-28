import os
from datetime import datetime, timezone
from typing import Dict, List
import asyncio
import glob

from better_morning.config import (
    load_global_config,
    load_collection,
    Collection,
    GlobalConfig,
)
from better_morning.rss_fetcher import RSSFetcher
from better_morning.content_extractor import ContentExtractor
from better_morning.llm_summarizer import LLMSummarizer
from better_morning.document_generator import DocumentGenerator


async def process_collection(
    collection_path: str, global_config: GlobalConfig
) -> tuple[str, str]:
    """Processes a single news collection: fetches, extracts, summarizes, and returns its digest."""
    print(f"\n--- Processing collection: {collection_path} ---")
    collection_config = load_collection(collection_path, global_config)

    # Initialize components with collection-specific or merged settings
    rss_fetcher = RSSFetcher(feeds=collection_config.feeds)
    content_extractor = ContentExtractor(
        settings=collection_config.content_extraction_settings
    )
    llm_summarizer = LLMSummarizer(
        settings=collection_config.llm_settings, global_config=global_config
    )

    # 1. Fetch new RSS articles
    new_articles = rss_fetcher.fetch_articles(collection_config.name)
    print(f"Found {len(new_articles)} new articles for {collection_config.name}.")

    if not new_articles:
        return (
            collection_config.name,
            "No new articles found for this collection today.\n",
        )

    # 2. Extract content for new articles concurrently
    content_extraction_tasks = [
        content_extractor.get_content(article) for article in new_articles
    ]
    processed_articles = await asyncio.gather(*content_extraction_tasks)

    articles_with_content = [
        article for article in processed_articles if article.content
    ]

    if not articles_with_content:
        return (
            collection_config.name,
            "No articles with extractable content for this collection today.\n",
        )

    print(
        f"Summarizing {len(articles_with_content)} articles for {collection_config.name}..."
    )
    # 3. Summarize the collection
    collection_digest_summary = await llm_summarizer.summarize_articles_collection(
        articles_with_content, collection_prompt=collection_config.collection_prompt
    )

    return collection_config.name, collection_digest_summary


async def main():
    print("Starting better-morning daily digest generation...")

    # 1. Load global configuration
    try:
        global_config = load_global_config()
        print("Global configuration loaded successfully.")
    except Exception as e:
        print(f"Failed to load global configuration: {e}")
        return

    # 2. Find all collection files
    collection_files = glob.glob("collections/*.toml")
    if not collection_files:
        print(
            "No collection TOML files found in the 'collections/' directory. Exiting."
        )
        return

    print(f"Found {len(collection_files)} collections to process.")

    # 3. Process each collection concurrently
    tasks = [
        process_collection(filepath, global_config) for filepath in collection_files
    ]
    collection_results: List[tuple[str, str]] = await asyncio.gather(*tasks)

    # Aggregate all collection summaries
    all_collection_summaries: Dict[str, str] = {
        name: summary for name, summary in collection_results
    }

    # 4. Generate final markdown digest
    today = datetime.now(timezone.utc)
    document_generator = DocumentGenerator(global_config.output_settings, global_config)
    final_markdown_digest = document_generator.generate_markdown_digest(
        all_collection_summaries, today
    )

    print("\n--- Generated Final Daily Digest ---\n")
    print(final_markdown_digest)
    print("\n--- End of Digest ---\n")

    # 5. Output the digest based on global settings
    output_type = global_config.output_settings.output_type

    if output_type == "github_release":
        repo_slug = os.getenv(
            "GITHUB_REPOSITORY"
        )  # e.g., 'owner/repo' from GitHub Actions
        if not repo_slug or not os.getenv(
            global_config.output_settings.github_token_env
        ):
            print(
                "\nWARNING: GITHUB_REPOSITORY or GitHub Token environment variable not set. Skipping GitHub release. If running locally, this is expected.\n"
            )
            # Optionally save to a local file instead for local testing
            with open(f"daily-digest-{today.strftime('%Y-%m-%d')}.md", "w") as f:
                f.write(final_markdown_digest)
            print(f"Digest saved to daily-digest-{today.strftime('%Y-%m-%d')}.md")
        else:
            tag_name = f"daily-digest-{today.strftime('%Y-%m-%d')}"
            release_name = f"Daily News Digest {today.strftime('%Y-%m-%d')}"
            document_generator.create_github_release(
                tag_name, release_name, final_markdown_digest, repo_slug
            )
    elif output_type == "email":
        recipient_email = global_config.output_settings.recipient_email
        if (
            not recipient_email
            or not global_config.output_settings.smtp_server
            or not os.getenv(global_config.output_settings.smtp_username_env)
            or not os.getenv(global_config.output_settings.smtp_password_env)
        ):
            print(
                "\nWARNING: Email configuration (recipient, SMTP server, or credentials) is incomplete. Skipping email. If running locally, this is expected.\n"
            )
            # Optionally save to a local file instead for local testing
            with open(f"daily-digest-{today.strftime('%Y-%m-%d')}.md", "w") as f:
                f.write(final_markdown_digest)
            print(f"Digest saved to daily-digest-{today.strftime('%Y-%m-%d')}.md")
        else:
            subject = f"Daily News Digest - {today.strftime('%Y-%m-%d')}"
            document_generator.send_via_email(
                subject, final_markdown_digest, recipient_email
            )
    else:
        print(
            f"Warning: Unknown output type '{output_type}'. Digest only printed to console."
        )


if __name__ == "__main__":
    asyncio.run(main())
