This document outlines the design decisions for the "better-morning" project.

## Project Goal

The goal of this project is to create a system that generates a daily news digest from a collection of RSS feeds. The digest is created by summarizing new articles using a Language Model (LLM) and then publishing the result as a GitHub release or sending it via email.

## Core Features

- **RSS Feed Collections**: Users can define multiple collections of RSS feeds.
- **Configuration**: Each collection is configured using a TOML file.
- **Content Extraction**: The system can extract content from the article's link, not just the RSS feed.
- **Summarization**: Summaries are generated using an LLM of the user's choice, supported by `litellm`.
- **Automation**: The process is automated using a GitHub Action.
- **Output**: The final digest can be published as a GitHub release or sent via email.

## Technology Stack

- **Programming Language**: Python
- **Package Manager**: `uv`
- **LLM Interaction**: `litellm`
- **Configuration Format**: TOML

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

-   **`LLMSettings`**: Defines parameters for LLM interactions (model, temperature, number of news, words per summary, prompt template).
-   **`ContentExtractionSettings`**: Configures how article content is extracted (whether to follow links, parser type).
-   **`OutputSettings`**: Specifies the output method (GitHub Release or email) and related credentials/settings.
-   **`GlobalConfig`**: Holds application-wide settings, including default LLM, content extraction, and output settings, along with environment variable names for secrets.
-   **`RSSFeed`**: A simple model to define an individual RSS feed URL and its optional name.
-   **`CollectionOverrides`**: A temporary model used during TOML parsing to capture collection-specific overrides before merging with global settings.
-   **`Collection`**: Represents a fully resolved collection configuration, merging `GlobalConfig` defaults with collection-specific overrides.
-   **`load_global_config(path: str) -> GlobalConfig`**: Loads the global configuration from `config.toml`. It handles cases where the file might be missing by providing default `GlobalConfig` values.
-   **`load_collection(collection_path: str, global_config: GlobalConfig) -> Collection`**: Loads a collection's TOML file (e.g., `collections/default_news.toml`), merges its settings with the provided `global_config`, and returns a fully resolved `Collection` object.
-   **`get_secret(env_var_name: Optional[str], config_name: str) -> str`**: A utility function to retrieve secrets from environment variables, raising an error if the variable is not found or configured.

### 2. RSS Feed Fetching (`src/better_morning/rss_fetcher.py`)

This module handles fetching RSS feeds, parsing entries, and managing historical articles to identify new content.

-   **`Article`**: Pydantic model representing a single news article, including `id` (link used as unique ID), `title`, `link`, `published_date`, `summary` (from RSS or LLM), and `content` (full article text).
-   **`ArticleEncoder`**: A custom `json.JSONEncoder` to properly serialize `datetime` and `HttpUrl` objects when saving historical articles.
-   **`RSSFetcher`**: A class responsible for:
    -   Loading historical articles from a JSON file (e.g., `history/{collection_name}_articles.json`).
    -   Fetching articles from configured RSS feeds using `feedparser`.
    -   Comparing newly fetched articles against historical data to identify and return only new entries.
    -   Saving all fetched articles (new and existing) back to the history file to maintain state.
    -   Robust date parsing with fallbacks.

### 3. Content Extraction (`src/better_morning/content_extractor.py`)

This module is responsible for extracting the full text content of an article, either from its RSS summary or by following the article link and parsing the webpage.

-   **`ContentExtractor`**: A class that:
    -   Takes `ContentExtractionSettings` in its constructor.
    -   **`_extract_from_html(html_content: str) -> Optional[str]`**: Parses HTML content using `BeautifulSoup` with configured `parser_type`. It attempts to locate common article content elements (`<article>`, `<main>`, specific `div` classes) and extracts text from paragraphs within them. Falls back to extracting all paragraphs if specific article containers are not found.
    -   **`get_content(article: Article) -> Article`**: This is the main method that orchestrates content retrieval. If `follow_article_links` is `False`, it directly uses `article.summary` as `article.content`. Otherwise, it makes an HTTP request to `article.link`, fetches the HTML, and uses `_extract_from_html` to get the content. It includes error handling and falls back to `article.summary` on failure.

### 4. LLM Summarization (`src/better_morning/llm_summarizer.py`)

This module interfaces with large language models via `litellm` to summarize articles and collections of articles.

-   **`TOKEN_TO_CHAR_RATIO`**: A constant for rough estimation of tokens based on character count.
-   **`LLMSummarizer`**: A class that:
    -   Initializes with `LLMSettings` and `GlobalConfig`, setting the LLM API key from environment variables using `get_secret`.
    -   **`_truncate_text_to_token_limit(text: str, token_limit: int) -> tuple[str, bool]`**: Truncates text to a character limit estimated from `token_limit` to prevent exceeding LLM input constraints. Emits a warning if truncation occurs.
    -   **`summarize_text(article: Article, prompt_override: Optional[str] = None) -> Article`**: Summarizes an individual article. It constructs a prompt using the article's title, content, and `k_words_each_summary` (from `LLMSettings`), allowing for a `prompt_override`. The prompt is truncated if necessary, and `litellm.completion` is called to get the summary, which is then assigned to `article.summary`.
    -   **`summarize_articles_collection(articles: List[Article], collection_prompt: Optional[str] = None) -> str`**: An asynchronous method that takes a list of articles for a collection. It first ensures each new article has an individual summary (calling `summarize_text` if needed). Then, it filters for the `n_most_important_news` (based on latest published date) from the summarized articles, concatenates their summaries, and finally uses `summarize_text` again to generate an overall collection summary based on `collection_prompt`.

### 5. Document Generation and Output (`src/better_morning/document_generator.py`)

This module is responsible for formatting the summarized news into a document and handling its output (GitHub Release or email).

-   **`DocumentGenerator`**: A class that:
    -   Initializes with `OutputSettings` and `GlobalConfig`.
    -   **`generate_markdown_digest(collection_summaries: Dict[str, str], date: datetime) -> str`**: Takes a dictionary of collection names to their summary strings and a date. It formats these into a single Markdown string, including a main title and sub-sections for each collection summary.
    -   **`send_via_email(subject: str, body: str, recipient_email: str)`**: Sends the generated digest via email using `smtplib` and `MIMEMultipart`. It retrieves SMTP credentials using `get_secret` and requires `smtp_server`, `smtp_port`, `smtp_username_env`, `smtp_password_env`, and `recipient_email` to be configured.
    -   **`create_github_release(tag_name: str, release_name: str, body: str, repo_slug: str)`**: Creates a new GitHub Release using the GitHub API. It retrieves the GitHub token via `get_secret` (using `github_token_env`), constructs the API request, and posts the digest as the release body. Requires `repo_slug` (e.g., `owner/repo`) to be available.

### 6. Main Application Flow (`src/main.py`)

This is the entry point of the application, orchestrating the entire news digest generation process.

-   **`process_collection(collection_path: str, global_config: GlobalConfig) -> tuple[str, str]` (async)**:
    -   Loads a specific collection's configuration.
    -   Initializes `RSSFetcher`, `ContentExtractor`, and `LLMSummarizer` with the appropriate settings (merged from global and collection-specific).
    -   Fetches new articles for the collection.
    -   Extracts content for these new articles.
    -   Summarizes the collection's articles into a single digest summary.
    -   Returns the collection's name and its summary.
-   **`main()` (async)**:
    -   Loads the `global_config`.
    -   Discovers all collection TOML files in the `collections/` directory.
    -   Uses `asyncio.gather` to concurrently call `process_collection` for each discovered collection, improving performance.
    -   Aggregates the summaries from all collections.
    -   Generates the `final_markdown_digest` using `DocumentGenerator`.
    -   Based on `global_config.default_output_settings.output_type`, it either calls `document_generator.create_github_release` or `document_generator.send_via_email`. It gracefully handles missing secrets/environment variables for local runs by saving the digest to a local Markdown file if external output is not fully configured.
    -   Prints the final digest to the console.

### 7. Local Execution Script (`run_local.py`)

This script provides a convenient way to run the `better-morning` application locally, outside of GitHub Actions.

-   Sets up dummy environment variables for `GITHUB_REPOSITORY` and `BETTER_MORNING_LLM_API_KEY` to allow the `main` function to run without immediate errors, even if real secrets are not configured. Users are prompted to replace the dummy LLM API key for functional summarization.
-   Imports and executes the `main()` asynchronous function from `src/main.py`.
-   Prints informative messages about local execution and potential fallback to local Markdown file output if external publishing is not fully set up.

### 8. GitHub Actions Workflow (`.github/workflows/daily_digest.yml`)

Automates the daily execution of the `main.py` script.

-   **`on: workflow_dispatch`**: Allows manual triggering of the workflow from the GitHub UI.
-   **`on: schedule: - cron: '0 0 * * *'`**: Configures the workflow to run daily at 00:00 UTC.
-   **`env`**: Sets `PYTHONUNBUFFERED` and maps GitHub Secrets (like `BETTER_MORNING_LLM_API_KEY`, `BETTER_MORNING_SMTP_USERNAME`, `BETTER_MORNING_SMTP_PASSWORD`) to environment variables that `main.py` and its modules expect.
-   **`jobs.build.steps`**: Defines the sequence of actions:
    1.  `Checkout repository`: Retrieves the code.
    2.  `Set up Python`: Configures Python 3.13 environment.
    3.  `Install uv and dependencies`: Installs `uv` and then uses it to install project dependencies from `pyproject.toml`.
    4.  `Run Daily Digest Generation`: Executes `src/main.py` using the Python interpreter in the virtual environment.
