import os
from typing import Optional, List
import litellm
from math import ceil  # For basic token estimation

from .config import LLMSettings, GlobalConfig, get_secret
from .rss_fetcher import Article

# Rough estimate: 1 token = 4 characters (common for English text)
TOKEN_TO_CHAR_RATIO = 4


class LLMSummarizer:
    def __init__(self, settings: LLMSettings, global_config: GlobalConfig):
        self.settings = settings
        self.global_config = global_config

    def _truncate_text_to_token_limit(
        self, text: str, token_limit: int
    ) -> tuple[str, bool]:
        # Convert token limit to character limit based on approximation
        char_limit = token_limit * TOKEN_TO_CHAR_RATIO

        if len(text) > char_limit:
            print(
                f"Warning: Text content exceeds token size threshold ({token_limit} tokens / {char_limit} characters). Truncating."
            )
            return text[:char_limit], True
        return text, False

    def summarize_text(
        self, article: Article, prompt_override: Optional[str] = None
    ) -> Article:
        if not article.content and not article.raw_content:
            print(
                f"Warning: No content available for article '{article.title}'. Skipping summarization."
            )
            return article  # Return article as is if no content

        # Determine the prompt to use
        final_prompt_template = prompt_override or self.settings.prompt_template

        # Construct the message payload for litellm
        # This now supports multimodal content (e.g., text and PDF)
        messages = []
        if article.content_type == "application/pdf" and article.raw_content:
            # Multimodal message for models that support it (like GPT-4o)
            print(f"Preparing multimodal summary request for PDF: {article.title}")
            # The text part of the prompt can be simpler, as the main content is the PDF
            text_prompt = (
                f"Please summarize the attached PDF document titled '{article.title}' "
                f"in approximately {self.settings.k_words_each_summary} words."
            )
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        # litellm expects a dictionary for image/pdf inputs
                        {"type": "pdf", "pdf": article.raw_content},
                    ],
                }
            ]
            # For multimodal, we don't truncate the (binary) content, but we ensure the text prompt isn't excessively long.
            # We assume the model's context window is large enough for the PDF.
            # A simple check on the text part:
            truncated_prompt_text, was_truncated = self._truncate_text_to_token_limit(
                text_prompt, self.global_config.token_size_threshold
            )
            if was_truncated:
                print(
                    f"Warning: Text part of multimodal prompt for '{article.title}' was truncated."
                )
                # Rebuild message with truncated text
                messages[0]["content"][0]["text"] = truncated_prompt_text

        else:  # Default to text-based summarization
            if final_prompt_template:
                prompt = final_prompt_template.format(
                    title=article.title,
                    content=article.content,
                    k_words=self.settings.k_words_each_summary,
                )
            else:
                # Default prompt if no template is provided
                prompt = (
                    f'Please summarize the following article titled "{article.title}" in approximately '
                    f"{self.settings.k_words_each_summary} words. Focus on the most important points.\n\n"
                    f"{article.content}"
                )

            # Truncate prompt if it's too long
            truncated_prompt, was_truncated = self._truncate_text_to_token_limit(
                prompt, self.global_config.token_size_threshold
            )
            if was_truncated:
                print(
                    f"Warning: Summarization prompt for '{article.title}' was truncated."
                )

            messages = [{"role": "user", "content": truncated_prompt}]

        try:
            # Ensure we have messages to send
            if not messages:
                raise ValueError("Message list for LLM completion is empty.")

            response = litellm.completion(
                model=self.settings.model,
                messages=messages,
                temperature=self.settings.temperature,
                api_key=self.settings.api_key,
            )
            article.summary = response.choices[0].message.content
            return article
        except Exception as e:
            print(f"Error summarizing article '{article.title}' with LLM: {e}")
            # Fallback for multimodal errors
            if " multimodal " in str(e).lower():
                article.summary = "[Error: Could not summarize the provided document with the current model. It may not support this file type.]"
            else:
                article.summary = "[Error: Could not summarize article]"
            return article

    async def summarize_articles_collection(
        self, articles: List[Article], collection_prompt: Optional[str] = None
    ) -> str:
        if not articles:
            return "No articles to summarize for this collection."

        # 1. Summarize each individual article
        summarized_articles: List[Article] = []
        for article in articles:
            # Ensure summary exists. If content is raw (PDF), this will trigger multimodal summarization.
            if not article.summary:
                summarized_articles.append(self.summarize_text(article))
            else:
                summarized_articles.append(article)

        # Filter out articles that couldn't be summarized
        effectively_summarized_articles = [
            a
            for a in summarized_articles
            if a.summary and not a.summary.startswith("[Error:")
        ]

        if not effectively_summarized_articles:
            return "No articles with valid summaries to process for the collection summary."

        # Sort by date to get the most important news
        effectively_summarized_articles.sort(
            key=lambda x: x.published_date, reverse=True
        )
        top_n_articles = effectively_summarized_articles[
            : self.settings.n_most_important_news
        ]

        # Concatenate summaries for the collection-level summary
        concatenated_summaries = "\n\n".join(
            [f"Title: {art.title}\nSummary: {art.summary}" for art in top_n_articles]
        )

        if not concatenated_summaries:
            return "No content available for collection summary."

        # 2. Summarize the concatenated summaries for the collection digest
        collection_summary_prompt = collection_prompt or (
            f"Given the following news summaries, provide a concise overall summary for the day's digest "
            f"in approximately {self.settings.k_words_each_summary * self.settings.n_most_important_news} words. "
            f"Highlight the main themes and most significant events.\n\n{concatenated_summaries}"
        )

        # Use a dummy Article object to reuse the summarization logic
        collection_article = Article(
            id="collection-summary-digest",
            title="Daily Digest Collection Summary",
            link="http://example.com/digest",  # Dummy URL
            published_date=datetime.now(timezone.utc),
            content=concatenated_summaries,
        )

        final_collection_summary_article = self.summarize_text(
            collection_article, prompt_override=collection_summary_prompt
        )

        return (
            final_collection_summary_article.summary
            or "Could not generate collection summary."
        )

    async def summarize_articles_collection(
        self, articles: List[Article], collection_prompt: Optional[str] = None
    ) -> str:
        if not articles:
            return "No articles to summarize for this collection."

        # 1. Summarize each individual article (if not already summarized or if content is new)
        # For simplicity, we'll re-summarize or ensure summary exists for all new articles.
        summarized_articles: List[Article] = []
        for article in articles:
            if (
                not article.summary or article.content
            ):  # If no summary or new content exists, summarize
                summarized_articles.append(
                    self.summarize_text(article, prompt_override=collection_prompt)
                )
            else:
                summarized_articles.append(article)

        # Filter out articles that couldn't be summarized or have no content
        effectively_summarized_articles = [
            a
            for a in summarized_articles
            if a.summary and a.summary != "[Error: Could not summarize article]"
        ]

        if not effectively_summarized_articles:
            return "No articles with valid summaries to process for the collection summary."

        # Sort articles by published date to get the N most important (latest as a simple heuristic)
        effectively_summarized_articles.sort(
            key=lambda x: x.published_date, reverse=True
        )
        top_n_articles = effectively_summarized_articles[
            : self.settings.n_most_important_news
        ]

        # Concatenate summaries of the top N articles
        concatenated_summaries = "\n\n".join(
            [f"Title: {art.title}\nSummary: {art.summary}" for art in top_n_articles]
        )

        if not concatenated_summaries:
            return "No content for collection summary after individual article summarization."

        # 2. Summarize the concatenated summaries for the collection
        collection_summary_prompt = collection_prompt or (
            f"Given the following summaries of news articles, provide a concise overall summary "
            f"in approximately {self.settings.k_words_each_summary * self.settings.n_most_important_news} words. "
            f"Focus on the main themes and most significant news.\n\n{concatenated_summaries}"
        )

        # Create a dummy Article object for the collection summary for consistent summarization logic
        collection_article = Article(
            id="collection-summary",
            title="Collection Daily Digest Summary",
            link=HttpUrl("http://example.com/collection-summary"),  # Dummy URL
            published_date=datetime.now(timezone.utc),
            content=concatenated_summaries,
            summary=None,  # Will be filled by summarize_text
        )

        final_collection_article = self.summarize_text(
            collection_article, prompt_override=collection_summary_prompt
        )
        return (
            final_collection_article.summary
            if final_collection_article.summary
            else "Could not generate collection summary."
        )
