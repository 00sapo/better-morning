This document outlines the design decisions for the "better-morning" project.

## Project Goal

The goal of this project is to create a system that generates a daily news digest from a collection of RSS feeds. The digest is created by summarizing new articles using a Language Model (LLM) and then publishing the result as a GitHub release or sending it via email.

## Core Features

*   **RSS Feed Collections**: Users can define multiple collections of RSS feeds.
*   **Configuration**: Each collection is configured using a TOML file.
*   **Content Extraction**: The system can extract content from the article's link, not just the RSS feed.
*   **Summarization**: Summaries are generated using an LLM of the user's choice, supported by `litellm`.
*   **Automation**: The process is automated using a GitHub Action.
*   **Output**: The final digest can be published as a GitHub release or sent via email.

## Technology Stack

*   **Programming Language**: Python
*   **Package Manager**: `uv`
*   **LLM Interaction**: `litellm`
*   **Configuration Format**: TOML

## Directory Structure

```
.
├── .github/
│   └── workflows/
│       └── daily_digest.yml
├── collections/
│   ├── example1.toml
│   └── example2.toml
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
└── README.md
```