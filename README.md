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
[global]
llm_api_token_env = "BETTER_MORNING_LLM_API_KEY" # Environment variable name for your LLM API token
token_size_threshold = 131072 # Max tokens for LLM input (e.g., 128K characters ~ 128K tokens)

[global.default_llm_settings]
model = "gpt-4o"
temperature = 0.7
n_most_important_news = 5 # Number of top articles to summarize per collection
k_words_each_summary = 100 # Target words per individual article summary
prompt_template = "Summarize this article: {title}. Content: {content}" # Optional: Global prompt template

[global.default_content_extraction_settings]
follow_article_links = false # Set to true to fetch full article content from links
parser_type = "html.parser" # BeautifulSoup parser type (e.g., "html.parser", "lxml")

[global.default_output_settings]
output_type = "github_release" # "github_release" or "email"
github_token_env = "GITHUB_TOKEN" # Default GitHub token env var (usually provided by GitHub Actions)

# --- Email Output Configuration (Uncomment and configure if output_type = "email") ---
# smtp_server = "smtp.example.com"
# smtp_port = 587
# smtp_username_env = "BETTER_MORNING_SMTP_USERNAME"
# smtp_password_env = "BETTER_MORNING_SMTP_PASSWORD"
# recipient_email = "your_email@example.com"
```

### 3. Define RSS Feed Collections (`collections/*.toml`)

Create TOML files in the `collections/` directory to define your news categories. Each file represents a collection and can override global settings. A `default_news.toml` is provided as an example.

**Example `collections/default_news.toml`:**

```toml
# filepath: collections/default_news.toml
name = "General Daily News"

[[feeds]]
url = "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
name = "New York Times"

[[feeds]]
url = "https://www.theguardian.com/uk/rss"
name = "The Guardian"

[[feeds]]
url = "https://www.bbc.com/news/rss/newsonline_world_edition/front_page/rss.xml"
name = "BBC World News"

[llm_settings]
n_most_important_news = 5
k_words_each_summary = 100
prompt_template = "Summarize this news article for a general audience: {title}. Content: {content}"

[content_extraction_settings]
follow_article_links = true
parser_type = "html.parser"

collection_prompt = "Provide a comprehensive but concise overview of the most significant global news from various sources."
```

### 4. Set Up GitHub Secrets

For the GitHub Action to function, you need to configure secrets in your repository settings.

1. Go to your GitHub repository.
2. Navigate to `Settings` -> `Secrets and variables` -> `Actions`.
3. Click `New repository secret` for each of the following:

    - `BETTER_MORNING_LLM_API_KEY`: Your API key for the chosen LLM provider (e.g., OpenAI API Key). **Required**.
    - `BETTER_MORNING_SMTP_USERNAME`: (Optional, if using email output) Your SMTP username/email address.
    - `BETTER_MORNING_SMTP_PASSWORD`: (Optional, if using email output) Your SMTP password or app-specific password.
    - `GITHUB_TOKEN`: This is automatically provided by GitHub Actions for creating releases, so you generally don't need to set it manually unless you need extended permissions. Ensure your repository's `Settings > Actions > General > Workflow permissions` are set appropriately (e.g., `Read and write permissions`).

## 5. Set Up GitHub Action

The `.github/workflows/daily_digest.yml` file defines the GitHub Action that runs your daily digest generation.

- **Trigger**: It's set to run daily at 07:30 UTC and can also be triggered manually via `workflow_dispatch`.
- **Steps**: It checks out your code, sets up Python with `uv`, installs dependencies, and executes `src/main.py`.

You need to set the cron schedule and the branch for running it (see line `branch: personal`) in the
workflow file.

## 📝 Output Options

Based on your `output_type` in `config.toml`:

- **GitHub Release**: A new GitHub Release will be created daily with the digest content. The release will be tagged with `daily-digest-YYYY-MM-DD`.
- **Email**: The digest will be sent to the `recipient_email` specified in your `config.toml`.

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
