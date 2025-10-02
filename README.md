> [!IMPORTANT]
> This repo is 90% entirely vibe-coded, including this readme. But I use it, so it should kinda work.

# 🌅 Better Morning

**Better Morning** is an automated news digest system that fetches articles from user-defined RSS feeds, summarizes them using a Language Model (LLM) of your choice, and delivers a personalized news summary periodically (e.g. daily).

## ✨ Features

- **Configurable RSS Collections**: Define multiple collections of RSS feeds using simple TOML files.
- **Smart Content Extraction**: Optionally follow article links to extract full content, not just RSS summaries, using a web page parser.
- **LLM-Powered Summarization**: Utilizes `litellm` to interface with various LLM providers (e.g., OpenAI, Anthropic, Cohere) for intelligent summarization.
- **Customizable Prompts**: Tailor summarization prompts at a global or collection level.
- **Daily Automation**: Runs automatically via a GitHub Action at a user-defined schedule.
- **Flexible Output**: Publish your daily digest as a GitHub Release or send it directly to your email.
- **Token Management**: Automatically truncates content if it exceeds a specified token limit to prevent excessive LLM costs.
- **Google Scholar Alerts**: Automatically retrieve articles from google scholar alerts.
- **Article Age Filtering**: Only include recent articles using `max_age` (per collection).

## 🚀 Getting Started

Follow these steps to set up and run your Better Morning news digest.

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/better-morning.git
cd better-morning
```

### 2. Configure Global Settings (`config.toml`)

Create a `config.toml` file in the root of your repository (if it doesn't already exist) to define global settings. This file specifies defaults and sensitive environment variable names.

**Example `config.toml`:**

```toml
# filepath: config.toml
[llm_settings]
# Use any litellm-supported model
reasoner_model = "openai/gpt-4o"  # model used for selecting articles and for the final summaries
light_model = "openai/gpt-3.5-turbo" # model used for summarizing individual articles
# thinking_effort_reasoner/thinking_effort_light; conversion from string to integer is done by
# llmlite
thinking_effort_light = "medium"
thinking_effort_reasoner = "8192"

# optionals:
temperature = 0.7
output_language = "Italian"
prompt_template = """Summarize this article concisely in exactly {k_words_each_summary} words:

Title: {title}
Content: {content}

Summary:"""

[content_extraction_settings]
# you can set this to true to follow article links that are inside the content of the article,
# useful for including alerts and list of news
# you can also set this at the colelction and feed levels in collections/*.toml
# default is false
follow_article_links = false

[output_settings]
output_type = "email"                              # Options: "github_release", "email", github_release is not working
smtp_server = "smtp.gmail.com"                     # Required if output_type is "email"
smtp_port = 587                                    # Required if output_type is "email"
```

### 3. Define RSS Feed Collections (`collections/*.toml`)

Create TOML files in the `collections/` directory to define your news categories. Each file represents a collection and can override global settings. A `default_news.toml` is provided as an example.

**Example `collections/default_news.toml`:**

```toml
# filepath: collections/default_news.toml
name = "General Daily News"
max_age = "2d"  # Only include articles from the last 2 days (see below for details)

[[feeds]]
url = "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
name = "New York Times"

[[feeds]]
url = "https://www.theguardian.com/uk/rss"
name = "The Guardian"

[[feeds]]
url = "https://www.bbc.com/news/rss/newsonline_world_edition/front_page/rss.xml"
name = "BBC World News"
follow_article_links = false # override parent setting for this feed only

[llm_settings]
n_most_important_news = 5
k_words_each_summary = 100
prompt_template = "Summarize this news article for a general audience: {title}. Content: {content}"

[content_extraction_settings]
follow_article_links = true # if the article content contains links, this will follow them (you can enable this at the feed level only also)
link_filter_pattern = "https:\/\/scholar.google.com\/scholar_url\?url=.+" # this is a regex pattern to filter links to follow

collection_prompt = "Provide a comprehensive but concise overview of the most significant global news from various sources."
```

### Filtering Articles by Age (`max_age`)

You can control how old articles are allowed to be for each collection using the `max_age` setting in your collection TOML files. This helps ensure your digest only includes recent or relevant news.

**Supported formats:**

- **Time span:** e.g. `2d` (2 days), `1h` (1 hour), `30m` (30 minutes), `7d` (7 days)
- **Special value:** `last-digest` — Only include articles published after the last successful digest for this collection (uses a cached timestamp).

**Example:**

```toml
# filepath: collections/recent_news.toml
name = "Recent News Only"
max_age = "2d"  # Only include articles from the last 2 days

[[feeds]]
url = "https://feeds.bbci.co.uk/news/rss.xml"
name = "BBC News"
max_articles = 10
```

**How `last-digest` works:**

- When you set `max_age = "last-digest"`, the system remembers the time of the last successful digest for each collection.
- On the next run, only articles newer than that time are included.
- The timestamp is cached in a file under `history/` and persists across runs (including GitHub Actions and local runs).
- If no previous digest exists, all articles are included on the first run.

---

## 4. Set Up GitHub Secrets

For the GitHub Action to function, you need to configure secrets in your repository settings.

1. Go to your GitHub repository.
2. Navigate to `Settings` -> `Secrets and variables` -> `Actions`.
3. Click `New repository secret` for each of the following:

    - `BETTER_MORNING_LLM_API_KEY`: Your API key for the chosen LLM provider (e.g., OpenAI API Key). **Required**.
    - `BETTER_MORNING_SMTP_USERNAME`: (Optional, if using email output) Your SMTP username/email address.
    - `BETTER_MORNING_SMTP_PASSWORD`: (Optional, if using email output) Your SMTP password or app-specific password.
    - `BETTER_MORNING_RECIPIENT_EMAIL`: (Optional, if using email output) The email address to send the digest to.
    - Note: `GITHUB_TOKEN` is automatically provided by GitHub Actions for creating releases, so you don't need to set it manually. Ensure your repository's `Settings > Actions > General > Workflow permissions` are set to `Read and write permissions`.

## 5. Set Up GitHub Action

The `.github/workflows/daily_digest.yml` file defines the GitHub Action that runs your daily digest generation.

- **Trigger**: It's set to run daily at 07:30 UTC and can also be triggered manually via `workflow_dispatch`.
- **Steps**: It checks out your code, sets up Python with `uv`, installs dependencies, and executes `src/main.py`.

You need to set the cron schedule and the branch for running it (see line `branch: personal`) in the
workflow file.

## 📝 Output Options

Based on your `output_type` in `config.toml`:

- **GitHub Release**: A new GitHub Release will be created daily with the digest content. The release will be tagged with `daily-digest-YYYY-MM-DD`.
- **Email**: The digest will be sent to the email address specified in the `BETTER_MORNING_RECIPIENT_EMAIL` environment variable.

## Run Locally

#### Install Dependencies

This project uses `uv` as the package manager for speed and efficiency.

```bash
pip install uv
uv sync
```

To test the digest generation locally without deploying to GitHub Actions, you can use the `run_local.py` script. This script sets up dummy environment variables where needed and executes the main application logic. If GitHub or email output is configured but secrets are not fully set, the digest will be saved to a local Markdown file.

```bash
./.venv/bin/python run_local.py
```

**Note:** For local LLM summarization to work, you will need to set the `BETTER_MORNING_LLM_API_KEY` environment variable with a valid API key for your chosen LLM provider. For example:

```bash
export BETTER_MORNING_LLM_API_KEY="sk-your-llm-api-key"
./.venv/bin/python run_local.py
```

## 🤝 Contributing

Contributions are welcome! Please feel free to open issues or submit pull requests.

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
