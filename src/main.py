import os
from datetime import datetime, timezone
from typing import Dict, List
import asyncio
import glob

from better_morning.config import (
    load_global_config,
    load_collection,
    GlobalConfig,
    get_secret,
)
from better_morning.rss_fetcher import RSSFetcher, Article
from better_morning.content_extractor import ContentExtractor
from better_morning.llm_summarizer import LLMSummarizer
from better_morning.document_generator import DocumentGenerator


async def process_collection(
    collection_path: str, global_config: GlobalConfig
) -> tuple[str, str, List[Article], List[str]]:
    """
    Processes a single news collection: fetches, extracts, summarizes.
    Returns the collection name, its summary, the list of summarized articles, and a list of skipped sources.
    """
    print(f"\n--- Processing collection: {collection_path} ---")
    collection_config = load_collection(collection_path, global_config)

    # Initialize components
    rss_fetcher = RSSFetcher(feeds=collection_config.feeds)
    content_extractor = ContentExtractor(
        settings=collection_config.content_extraction_settings
    )
    llm_summarizer = LLMSummarizer(
        settings=collection_config.llm_settings, global_config=global_config
    )

    skipped_sources = set()

    try:
        await content_extractor.start_browser()

        # 1. Fetch new RSS articles
        new_articles = rss_fetcher.fetch_articles(collection_config.name)
        print(f"Found {len(new_articles)} new articles for {collection_config.name}.")
        if not new_articles:
            return collection_config.name, "No new articles found.", [], []

        # 2. Extract content
        content_extraction_tasks = [
            content_extractor.get_content(article) for article in new_articles
        ]
        content_extraction_results = await asyncio.gather(*content_extraction_tasks)
        
        # Flatten the results since get_content now returns List[Article]
        processed_articles = []
        for article_list in content_extraction_results:
            processed_articles.extend(article_list)

        # Track and filter sources with high failure rates
        source_stats = {}
        for article in processed_articles:
            source_url = str(article.source_url) if article.source_url else "Unknown"
            if source_url not in source_stats:
                source_stats[source_url] = {"success": 0, "failure": 0}
            
            # Content is defined as having either text content or raw_content (for PDFs)
            if article.content or article.raw_content:
                source_stats[source_url]["success"] += 1
            else:
                source_stats[source_url]["failure"] += 1

        for source_url, stats in source_stats.items():
            total_articles = stats["success"] + stats["failure"]
            if total_articles >= 10 and (stats["failure"] / total_articles) > 0.75:
                print(
                    f"Warning: Skipping source {source_url} due to high failure rate."
                )
                skipped_sources.add(source_url)

        articles_with_content = [
            article
            for article in processed_articles
            if (article.content or article.raw_content)
            and (
                article.source_url is None
                or str(article.source_url) not in skipped_sources
            )
        ]

        if not articles_with_content:
            return collection_config.name, "No articles with extractable content.", [], list(skipped_sources)

        # 3. Summarize the collection and individual articles
        print(
            f"Summarizing {len(articles_with_content)} articles for {collection_config.name}..."
        )
        (
            collection_summary,
            summarized_articles,
        ) = await llm_summarizer.summarize_articles_collection(
            articles_with_content,
            collection_prompt=collection_config.collection_prompt,
        )

        return collection_config.name, collection_summary, summarized_articles, list(skipped_sources)
    finally:
        await content_extractor.close_browser()


async def main():
    print("Starting better-morning daily digest generation...")

    # 1. Load global configuration
    global_config = load_global_config()
    print("Global configuration loaded successfully.")

    # 2. Find and process all collections concurrently
    collection_files = glob.glob("collections/*.toml")
    if not collection_files:
        print("No collection TOML files found. Exiting.")
        return

    print(f"Found {len(collection_files)} collections to process.")
    tasks = [
        process_collection(filepath, global_config) for filepath in collection_files
    ]
    collection_results: List[tuple[str, str, List[Article], List[str]]] = await asyncio.gather(
        *tasks
    )

    # 3. Aggregate results
    collection_summaries: Dict[str, str] = {
        name: summary for name, summary, _, _ in collection_results
    }
    articles_by_collection: Dict[str, List[Article]] = {
        name: articles for name, _, articles, _ in collection_results
    }
    
    # Collect all unique skipped sources from all processing runs
    skipped_sources = set()
    for _, _, _, sources in collection_results:
        skipped_sources.update(sources)

    # 4. Generate and output the final markdown digest
    today = datetime.now(timezone.utc)
    document_generator = DocumentGenerator(global_config.output_settings, global_config)
    final_markdown_digest = document_generator.generate_markdown_digest(
        collection_summaries, articles_by_collection, list(skipped_sources), today
    )

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
        try:
            recipient_email = get_secret(global_config.output_settings.recipient_email_env, "Recipient Email")
        except ValueError:
            recipient_email = None
        
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

    # 6. Only save articles to history after digest has been successfully output
    # This ensures that if any step fails, no articles are marked as processed
    for collection_file in collection_files:
        collection_config = load_collection(collection_file, global_config)
        collection_name = collection_config.name
        if collection_name in articles_by_collection and articles_by_collection[collection_name]:
            rss_fetcher = RSSFetcher(feeds=collection_config.feeds)
            rss_fetcher.save_selected_articles_to_history(
                collection_name, articles_by_collection[collection_name]
            )
            print(f"Saved {len(articles_by_collection[collection_name])} articles to history for {collection_name}")


if __name__ == "__main__":
    asyncio.run(main())
