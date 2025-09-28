I want to build a github repository to create a document each day that resumes the news of the day before.

The user should be able to setup a few collections of RSS urls.

Every day at a certain time (decided by the user), a GitHub action is run.
For each collection, the action will:

- retrieve the XML
- compare it with the latest one
- select only the new articles
- get the article content: the user should choose if using the default content or follow the article links and get the content using a web page parser
- concatenate all the article contents and cut it at a certain token size (default: 128K)
- if token size was exceeding the threshold, emit a warning
- ask an LLM of user's choice to summarize the N most important news (N defined by the user, default 5) in K words each (K defaults to 100).
- concatenate all the summaries in the collection and repeat the summarization procedure (with different options for the LLM, N, and K, but same defaults)
- at the end, generate a document with the summaries from each collection in a release (or send them via mail, depending on the user choice)

So, there should be a number of options, at the level of each collection and globally, including at least two secrets (email SMTP credentials and LLM API token).
Ideally, each collection is defined by a text file with the options at the top, with some standardized format (e.g. toml).
And each collection should have the option for a prompt that is appended when filtering news and asking summary.

I want to use litellm to access the various LLM providers.
Also, I want to use uv in place of pip.

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

