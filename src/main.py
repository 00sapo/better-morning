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
) -> tuple[str, str, List[Article], List[str], dict]:
    """
    Processes a single news collection: fetches, extracts, summarizes.
    Returns the collection name, its summary, the list of summarized articles, skipped sources, and fetch report.
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
        new_articles = rss_fetcher.fetch_articles(collection_config.name, collection_config.max_age)
        print(f"Found {len(new_articles)} new articles for {collection_config.name}.")
        if not new_articles:
            fetch_report = rss_fetcher.get_fetch_report()
            return (
                collection_config.name,
                "No new articles found.",
                [],
                [],
                fetch_report,
            )

        # 2. Get digest context from DocumentGenerator
        document_generator = DocumentGenerator(global_config.output_settings, global_config)
        digest_context = document_generator.get_context_for_llm()
        
        # 3. Use LLM to select which articles to fetch content for
        articles_to_fetch = await llm_summarizer.select_articles_for_fetching(
            new_articles, collection_config.collection_prompt, digest_context
        )
        if not articles_to_fetch:
            print(
                f"LLM did not select any articles to fetch for '{collection_config.name}'."
            )
            fetch_report = rss_fetcher.get_fetch_report()
            return (
                collection_config.name,
                "No articles selected for fetching.",
                [],
                [],
                fetch_report,
            )

        # 3. Extract content for the selected articles with batching
        batch_size = global_config.content_extraction_batch_size
        processed_articles = []

        print(
            f"Extracting content for {len(articles_to_fetch)} selected articles in batches of {batch_size}..."
        )
        for i in range(0, len(articles_to_fetch), batch_size):
            batch = articles_to_fetch[i : i + batch_size]
            print(
                f"Processing articles {i + 1}-{min(i + batch_size, len(articles_to_fetch))} of {len(articles_to_fetch)}"
            )

            content_extraction_tasks = [
                content_extractor.get_content(article) for article in batch
            ]
            batch_results = await asyncio.gather(*content_extraction_tasks)

            # Flatten the results
            for article_list in batch_results:
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
            fetch_report = rss_fetcher.get_fetch_report()
            return (
                collection_config.name,
                "No articles with extractable content.",
                [],
                list(skipped_sources),
                fetch_report,
            )

        # 4. Summarize the collection and individual articles
        print(
            f"Summarizing {len(articles_with_content)} articles for {collection_config.name}..."
        )
        (
            collection_summary,
            summarized_articles,
        ) = await llm_summarizer.summarize_articles_collection(
            articles_with_content,
            collection_prompt=collection_config.collection_prompt,
            previous_digests_context=digest_context,
        )

        # Get fetch report
        fetch_report = rss_fetcher.get_fetch_report()

        return (
            collection_config.name,
            collection_summary,
            summarized_articles,
            list(skipped_sources),
            fetch_report,
        )
    finally:
        await content_extractor.close_browser()


async def main():
    print("Starting better-morning daily digest generation...")

    # 1. Load global configuration
    global_config = load_global_config()
    print("Global configuration loaded successfully.")

    # 2. Find and process all collections concurrently
    collection_files = sorted(glob.glob("collections/*.toml"))
    if not collection_files:
        print("No collection TOML files found. Exiting.")
        return

    print(f"Found {len(collection_files)} collections to process.")

    collection_results = []
    collection_errors: Dict[str, str] = {}
    # Process collections sequentially to avoid overwhelming the LLM API
    for filepath in collection_files:
        try:
            result = await process_collection(filepath, global_config)
            collection_results.append(result)
        except Exception as e:
            print(
                f"FATAL: An unexpected error occurred while processing {filepath}: {e}"
            )
            # Create a dummy result so the aggregation logic doesn't fail
            collection_name = os.path.basename(filepath).replace(".toml", "")
            # Track the error for reporting in the digest
            collection_errors[collection_name] = str(e)
            collection_results.append(
                (
                    collection_name,
                    f"[ERROR: Processing failed: {e}]",
                    [],
                    [f"Collection {collection_name} failed"],
                    {"successful": [], "failed": [], "total_feeds": 0},
                )
            )

    collection_results: List[tuple[str, str, List[Article], List[str], dict]] = (
        collection_results
    )

    # 3. Aggregate results
    collection_summaries: Dict[str, str] = {
        name: summary for name, summary, _, _, _ in collection_results
    }
    articles_by_collection: Dict[str, List[Article]] = {
        name: articles for name, _, articles, _, _ in collection_results
    }

    # Collect all unique skipped sources from all processing runs
    skipped_sources = set()
    fetch_reports = {}
    for name, _, _, sources, fetch_report in collection_results:
        skipped_sources.update(sources)
        fetch_reports[name] = fetch_report

    # 4. Generate and output the final markdown digest
    today = datetime.now(timezone.utc)
    document_generator = DocumentGenerator(global_config.output_settings, global_config)
    final_markdown_digest = document_generator.generate_markdown_digest(
        collection_summaries,
        articles_by_collection,
        list(skipped_sources),
        today,
        fetch_reports,
        collection_errors or None,
    )

    # 5. Output the digest based on global settings
    output_type = global_config.output_settings.output_type

    if output_type == "github_release":
        repo_slug = os.getenv(
            "GITHUB_REPOSITORY"
        )  # e.g., 'owner/repo' from GitHub Actions
        github_token_env = global_config.output_settings.github_token_env
        if not repo_slug or not (github_token_env and os.getenv(github_token_env)):
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
            recipient_email = get_secret(
                global_config.output_settings.recipient_email_env, "Recipient Email"
            )
        except ValueError:
            recipient_email = None

        smtp_username_env = global_config.output_settings.smtp_username_env
        smtp_password_env = global_config.output_settings.smtp_password_env
        if (
            not recipient_email
            or not global_config.output_settings.smtp_server
            or not (smtp_username_env and os.getenv(smtp_username_env))
            or not (smtp_password_env and os.getenv(smtp_password_env))
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

    # Print feed processing summary
    print("\n=== FEED PROCESSING SUMMARY ===")
    total_successful = 0
    total_failed = 0
    total_articles = 0

    for collection_name, report in fetch_reports.items():
        # Handle missing or empty keys gracefully
        successful_feeds = report.get("successful", [])
        failed_feeds = report.get("failed", [])
        
        successful_count = len(successful_feeds)
        failed_count = len(failed_feeds)
        articles_count = sum(s.get("articles_fetched", 0) for s in successful_feeds)

        total_successful += successful_count
        total_failed += failed_count
        total_articles += articles_count

        success_rate = (
            successful_count / (successful_count + failed_count)
            if (successful_count + failed_count) > 0
            else 0
        )
        print(
            f"{collection_name}: {successful_count}/{successful_count + failed_count} feeds successful ({success_rate:.1%}) • {articles_count} articles"
        )

        if failed_count > 0:
            print(f"  Failed feeds in {collection_name}:")
            for failed_feed in failed_feeds:
                print(f"    - {failed_feed.get('name', 'Unknown')}: {failed_feed.get('error', 'Unknown error')}")
                print(f"      URL: {failed_feed.get('url', 'N/A')}")

    # Print collection errors if any
    if collection_errors:
        print("\n=== COLLECTION PROCESSING ERRORS ===")
        for collection_name, error_msg in collection_errors.items():
            print(f"  {collection_name}: {error_msg}")

    overall_success_rate = (
        total_successful / (total_successful + total_failed)
        if (total_successful + total_failed) > 0
        else 0
    )
    print(
        f"\nOVERALL: {total_successful}/{total_successful + total_failed} feeds successful ({overall_success_rate:.1%}) • {total_articles} total articles"
    )

    if total_failed > 0:
        print(
            f"\n⚠️  {total_failed} feeds failed. Check the detailed report in the digest for URLs to potentially remove."
        )

    # 6. Save the generated digest to history for future context
    document_generator.save_digest_to_history(collection_summaries, today)
    print(f"Saved digest to history for future context")

    # 7. Only save articles to history after digest has been successfully output
    # This ensures that if any step fails, no articles are marked as processed
    for collection_file in collection_files:
        collection_config = load_collection(collection_file, global_config)
        collection_name = collection_config.name
        if (
            collection_name in articles_by_collection
            and articles_by_collection[collection_name]
        ):
            rss_fetcher = RSSFetcher(feeds=collection_config.feeds)

            # We save the articles that were successfully summarized to history.
            # This prevents them from being re-processed in the next run.
            rss_fetcher.save_selected_articles_to_history(
                collection_name, articles_by_collection[collection_name]
            )
            
            # Save the current digest timestamp for max_age="last-digest" functionality
            rss_fetcher.save_digest_time(collection_name, today)
            
            print(
                f"Saved {len(articles_by_collection[collection_name])} articles to history for {collection_name}"
            )


if __name__ == "__main__":
    asyncio.run(main())
