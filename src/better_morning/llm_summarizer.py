from typing import Optional, List
import litellm

from .config import LLMSettings, GlobalConfig
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

    def _summarize_text_content(
        self, text_content: str, prompt: str, title: str = "Untitled"
    ) -> str:
        """Helper to summarize raw text content using the configured LLM."""
        # Truncate prompt if it's too long
        truncated_prompt, was_truncated = self._truncate_text_to_token_limit(
            prompt, self.global_config.token_size_threshold
        )
        if was_truncated:
            print(
                f"Warning: Summarization prompt for text content '{title}' was truncated."
            )

        messages = [{"role": "user", "content": truncated_prompt}]

        try:
            response = litellm.completion(
                model=self.settings.model,
                messages=messages,
                temperature=self.settings.temperature,
                api_key=self.settings.api_key,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"Error summarizing text content '{title}' with LLM: {e}")
            return f"[Error: Could not summarize text content '{title}']"

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

        # --- Debugging: Print concatenated summaries ---
        print("\n--- Concatenated Summaries for Collection-Level Prompt ---")
        print(concatenated_summaries)
        print("----------------------------------------------------------\n")

        if not concatenated_summaries:
            return "No content available for collection summary."

        # 2. Summarize the concatenated summaries for the collection digest
        collection_summary_prompt = collection_prompt or (
            f"Given the following news summaries, provide a concise overall summary for the day's digest "
            f"in approximately {self.settings.k_words_each_summary * self.settings.n_most_important_news} words. "
            f"Highlight the main themes and most significant events.\n\n{concatenated_summaries}"
        )

        # Use the new helper to summarize the concatenated text directly
        final_summary = self._summarize_text_content(
            text_content=concatenated_summaries,
            prompt=collection_summary_prompt,
            title="Daily Digest Collection Summary",
        )

        # --- Debugging: Print final collection summary ---
        print("\n--- Final Generated Collection Summary ---")
        if final_summary:
            print(final_summary)
        else:
            print("[Empty summary returned by LLM]")
        print("------------------------------------------\n")

        return final_summary or "Could not generate collection summary."
