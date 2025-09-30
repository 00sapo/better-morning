This document outlines the design decisions for the "better-morning" project.

## Project Goal

The goal of this project is to create a system that generates a daily news digest from a collection of RSS feeds. The digest is created by summarizing new articles using a Language Model (LLM) and then publishing the result as a GitHub release or sending it via email.

## Core Features

- **RSS Feed Collections**: Users can define multiple collections of RSS feeds.
- **Configuration**: Each collection is configured using a TOML file.
- **Smart Article Selection**: The `max_articles` setting per feed selects from only new articles (excluding previously processed ones). The system also uses an LLM to intelligently select the most relevant articles for content extraction, making the process more efficient.
- **Content Extraction**: The system can extract content from the article's link, handling both HTML pages and PDF documents.
- **Summarization**: Summaries are generated using an LLM of the user's choice, supported by `litellm`.
- **Robust Fetching**: Both RSS fetching and content extraction are implemented with rate limiting, exponential backoff retries, and timeouts to handle network errors gracefully and avoid being blocked.
- **Robust History Management**: Articles are only marked as "processed" in history after successful completion of the entire pipeline (including summarization), preventing data loss on failures.
- **Detailed Reporting**: The final digest includes a detailed report of successful and failed feed fetches, which is useful for monitoring and maintenance.
- **Automation**: The process is automated using a GitHub Action.
- **Output**: The final digest can be published as a GitHub release or sent via email.

## Technology Stack

- **Programming Language**: Python
- **Package Manager**: `uv`
- **LLM Interaction**: `litellm` (for multimodal summarization)
- **Content-Type Detection**: `python-magic`
- **Configuration Format**: TOML
- **HTML Content Extraction**: `trafilatura`
- **Web Browser Automation**: `playwright` (for dynamic content and fallback when requests fails)

## Directory Structure

```
.
├── .github/
│   └── workflows/
│       └── daily_digest.yml
├── collections/
│   └── default_news.toml
├── src/
│   ├── better_morning/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── content_extractor.py
│   │   ├── document_generator.py
│   │   ├── llm_summarizer.py
│   │   └── rss_fetcher.py
│   └── main.py
├── .gitignore
├── pyproject.toml
├── README.md
└── run_local.py
```

## Implementation Details

### 1. Configuration Management (`src/better_morning/config.py`)

This module defines Pydantic models for structured configuration and provides functions to load and merge these configurations.

-   **`LLMSettings`**: Defines parameters for LLM interactions (reasoner_model, light_model, temperature, number of news, words per summary, prompt template, output language).
-   **`ContentExtractionSettings`**: Configures how article content is extracted (whether to follow links, parser type, link filter pattern for selective link following).
-   **`OutputSettings`**: Specifies the output method (GitHub Release or email) and related credentials/settings.
-   **`GlobalConfig`**: Holds application-wide settings, including default LLM, content extraction, and output settings, along with environment variable names for secrets. It also includes settings for `max_articles_per_collection` and `content_extraction_batch_size`.
-   **`RSSFeed`**: A model to define an individual RSS feed, including its `url`, optional `name`, an optional `max_articles` limit, and per-feed `timeout` and `max_retries` settings.
-   **`CollectionOverrides`**: A temporary model used during TOML parsing to capture collection-specific overrides before merging with global settings.
-   **`Collection`**: Represents a fully resolved collection configuration, merging `GlobalConfig` defaults with collection-specific overrides.
-   **`load_global_config(path: str) -> GlobalConfig`**: Loads the global configuration from `config.toml`. It handles cases where the file might be missing by providing default `GlobalConfig` values.
-   **`load_collection(collection_path: str, global_config: GlobalConfig) -> Collection`**: Loads a collection's TOML file (e.g., `collections/default_news.toml`), merges its settings with the provided `global_config`, and returns a fully resolved `Collection` object.
-   **`get_secret(env_var_name: Optional[str], config_name: str) -> str`**: A utility function to retrieve secrets from environment variables, raising an error if the variable is not found or configured.

### 2. RSS Feed Fetching (`src/better_morning/rss_fetcher.py`)

This module handles fetching RSS feeds, parsing entries, and managing historical articles to identify new content.

-   **`Article`**: Pydantic model for a news article. Includes `id`, `title`, `link`, `feed_name`, `published_date`, `summary` (from RSS or LLM), `content` (for extracted text), `raw_content` (for binary data like PDFs), and `content_type` (e.g., "application/pdf").
-   **`ArticleEncoder`**: A custom `json.JSONEncoder` to properly serialize `datetime` and `HttpUrl` objects when saving historical articles.
-   **`RSSFetcher`**: A class responsible for:
    -   Loading historical articles from a JSON file (e.g., `history/{collection_name}_articles.json`).
    -   Fetching articles from configured RSS feeds using `feedparser`. It respects the `max_articles` setting on each feed, but crucially applies this limit only to articles that haven't been previously selected (not in history).
    -   Implementing robust fetching with rate limiting and exponential backoff retries.
    -   Tracking fetch statistics and generating a report of successful and failed feeds.
    -   Comparing newly fetched articles against historical data to identify and return only new entries that haven't been processed before.
    -   **`save_selected_articles_to_history()`**: A method to save only the articles that were successfully processed and selected to the history file. This ensures that only articles that made it through the complete processing pipeline are marked as "processed".
    -   Robust date parsing with fallbacks.

### 3. Content Extraction (`src/better_morning/content_extractor.py`)

This module is responsible for fetching content from an article's link and preparing it for summarization. It intelligently handles different content types and implements smart content fetching strategies.

-   **`ContentExtractor`**: A class that:
        -   Initializes with `ContentExtractionSettings` and manages a Playwright browser instance for dynamic content.
        -   Implements rate limiting per domain and rotates user agents to ensure robust and respectful scraping.
        -   Limits the number of concurrent Playwright pages to manage system resources.
        -   **`start_browser()` and `close_browser()`**: Manages the lifecycle of a Playwright browser instance for efficient resource usage.
        -   **`get_content(article: Article) -> Article`**: The main method for content retrieval with intelligent decision-making:
            -   **Smart RSS Length Check**: If the RSS summary is ≥400 words, uses it directly without fetching the article, reducing unnecessary requests.
            -   **Dual Fetching Strategy**: First attempts to fetch content using `requests` for static pages, then falls back to Playwright for dynamic content if needed.
            -   **PDF Handling**: Detects PDF content via Content-Type headers and stores raw binary data in `article.raw_content` for multimodal processing.
            -   **Link Following**: When `follow_article_links` is enabled, can follow and extract content from related links using optional regex pattern filtering via `link_filter_pattern`.
        -   **`_extract_from_html()`**: Uses the **`trafilatura`** library to robustly extract main article text while filtering out boilerplate content like ads and navigation.
        -   **`_fetch_with_requests()`**: Handles static content fetching with proper error handling and User-Agent headers.
        -   The system gracefully handles failures by falling back to RSS summaries when content extraction fails.

### 4. LLM Summarization (`src/better_morning/llm_summarizer.py`)

This module interfaces with large language models via `litellm` to summarize articles. It is designed to handle multimodal inputs, allowing it to summarize both text content and PDF documents directly.

-   **`TOKEN_TO_CHAR_RATIO`**: A constant for rough estimation of tokens based on character count.
-   **`LLMSummarizer`**: A class that:
        -   Initializes with `LLMSettings` and `GlobalConfig`.
        -   Uses the `light_model` for single-article summaries and the `reasoner_model` for the final collection summary and for selecting articles.
        -   **`select_articles_for_fetching(articles: List[Article]) -> List[Article]`**: Uses an LLM to select the most relevant articles for content extraction based on their titles and RSS summaries. It has a fallback to select the most recent articles if the LLM fails.
        -   **`summarize_text(article: Article, prompt_override: Optional[str] = None) -> Article`**: The core summarization method. It checks the `article.content_type` to determine how to process the content.
        -   **For standard text content**: It constructs a text-based prompt from the article's title and content, truncates it if necessary, and calls `litellm.completion`.
        -   **For PDF content (`application/pdf`)**: It constructs a **multimodal message** for `litellm`. This message includes a text part (e.g., "Summarize this PDF") and the raw PDF data from `article.raw_content`. This allows a capable LLM (like GPT-4o) to "read" the PDF directly.
        -   It includes robust error handling for API calls, with specific feedback if a multimodal request fails.
        -   **`summarize_articles_collection(articles: List[Article], collection_prompt: Optional[str] = None) -> str`**: An asynchronous method that takes a list of articles for a collection. It first ensures each new article has an individual summary (calling `summarize_text` if needed). Then, it filters for the `n_most_important_news` (based on latest published date) from the summarized articles, concatenates their summaries, and finally uses `summarize_text` again to generate an overall collection summary based on `collection_prompt`.

### 5. Document Generation and Output (`src/better_morning/document_generator.py`)

This module is responsible for formatting the summarized news into a document and handling its output (GitHub Release or email).

-   **`DocumentGenerator`**: A class that:
    -   Initializes with `OutputSettings` and `GlobalConfig`.
    -   **`generate_markdown_digest(...)`**: Takes a dictionary of collection names to their summary strings, a list of all articles, a list of skipped sources, a date, and a dictionary of fetch reports. It formats these into a single Markdown string, including a main title, a detailed feed processing report, and sub-sections for each collection summary.
    -   **`send_via_email(subject: str, body: str, recipient_email: str)`**: Sends the generated digest via email using `smtplib` and `MIMEMultipart`. It retrieves SMTP credentials using `get_secret` and requires `smtp_server`, `smtp_port`, `smtp_username_env`, `smtp_password_env`, and `recipient_email_env` to be configured.
    -   **`create_github_release(tag_name: str, release_name: str, body: str, repo_slug: str)`**: Creates a new GitHub Release using the GitHub API. It retrieves the GitHub token via `get_secret` (using `github_token_env`), constructs the API request, and posts the digest as the release body. Requires `repo_slug` (e.g., `owner/repo`) to be available.

### 6. Main Application Flow (`src/main.py`)

This is the entry point of the application, orchestrating the entire news digest generation process.

-   **`process_collection(...)` (async)**:
    -   Loads a specific collection's configuration.
    -   Initializes `RSSFetcher`, `ContentExtractor`, and `LLMSummarizer`.
    -   Manages browser lifecycle.
    -   Fetches new articles for the collection.
    -   Uses the LLM to select the most relevant articles to process.
    -   Extracts content for the selected articles in batches.
    -   Tracks skipped sources.
    -   Summarizes the collection's articles into a single digest summary.
    -   Returns the collection's name, its summary, the list of processed articles, a list of skipped sources, and a fetch report.
-   **`main()` (async)**:
    -   Loads the `global_config`.
    -   Discovers all collection TOML files in the `collections/` directory.
    -   Processes each collection sequentially.
    -   Aggregates the summaries and fetch reports from all collections.
    -   Generates the `final_markdown_digest`, including the detailed feed processing report.
    -   Outputs the digest to GitHub Release or email.
    -   Saves the processed articles to history only after the digest has been successfully output.
    -   Prints a detailed feed processing summary to the console.

### 7. Local Execution Script (`run_local.py`)

This script provides a convenient way to run the `better-morning` application locally, outside of GitHub Actions.

-   Sets up dummy environment variables for `GITHUB_REPOSITORY` and `BETTER_MORNING_LLM_API_KEY` to allow the `main` function to run without immediate errors, even if real secrets are not configured. Users are prompted to replace the dummy LLM API key for functional summarization.
-   Imports and executes the `main()` asynchronous function from `src/main.py`.
-   Prints informative messages about local execution and potential fallback to local Markdown file output if external publishing is not fully set up.

### 8. GitHub Actions Workflow (`.github/workflows/daily_digest.yml`)

Automates the daily execution of the `main.py` script.

-   **`on: workflow_dispatch`**: Allows manual triggering of the workflow from the GitHub UI.
-   **`on: schedule: - cron: '0 0 * * *'`**: Configures the workflow to run daily at 00:00 UTC.
-   **`env`**: Sets `PYTHONUNBUFFERED` and maps GitHub Secrets (like `BETTER_MORNING_LLM_API_KEY`, `BETTER_MORNING_SMTP_USERNAME`, `BETTER_MORNING_SMTP_PASSWORD`, `BETTER_MORNING_RECIPIENT_EMAIL`, `BETTER_MORNING_GITHUB_TOKEN`) to environment variables that `main.py` and its modules expect.
-   **`jobs.build.steps`**: Defines the sequence of actions:
    1.  `Checkout repository`: Retrieves the code.
    2.  `Cache article history`: Caches the `history/` directory to maintain article state between runs.
    3.  `Set up Python`: Configures Python 3.13 environment.
    4.  `Install uv and dependencies`: Installs `uv` and then uses it to install project dependencies from `pyproject.toml`.
    5.  `Install Playwright Browsers`: Installs Playwright browser binaries and system dependencies required for web scraping.
    6.  `Run Daily Digest Generation`: Executes `src/main.py` using the Python interpreter in the virtual environment.
