import asyncio
from typing import Optional, List
import litellm
import os

from .config import LLMSettings, GlobalConfig, get_secret
from .rss_fetcher import Article


# Rough estimate: 1 token = 4 characters (common for English text)
TOKEN_TO_CHAR_RATIO = 4


class LLMSummarizer:
    def __init__(self, settings: LLMSettings, global_config: GlobalConfig):
        self.settings = settings
        self.global_config = global_config
        # Ensure the API key is loaded from the environment if not already set
        if not self.settings.api_key:
            try:
                self.settings.api_key = get_secret(
                    global_config.llm_api_token_env, "LLM API Key"
                )
            except ValueError as e:
                # This allows for local runs where secrets might not be set up for other purposes.
                print(f"Warning: {e}")
                self.settings.api_key = None

    def _get_masked_api_key(self) -> str:
        """Returns a masked version of the API key for debugging."""
        if self.settings.api_key:
            return f"{self.settings.api_key[:4]}...{self.settings.api_key[-4:]}"
        return "None"

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

    async def summarize_text(
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
        messages = []
        if article.content_type == "application/pdf" and article.raw_content:
            # Multimodal message for models that support it (like GPT-4o)
            print(f"Preparing multimodal summary request for PDF: {article.title}")
            text_prompt = (
                f"Please summarize the attached PDF document titled '{article.title}' "
                f"in approximately {self.settings.k_words_each_summary} words. "
                f"The summary must be in {self.settings.output_language}."
            )
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        {"type": "pdf", "pdf": article.raw_content},
                    ],
                }
            ]
            truncated_prompt_text, was_truncated = self._truncate_text_to_token_limit(
                text_prompt, self.global_config.token_size_threshold
            )
            if was_truncated:
                print(
                    f"Warning: Text part of multimodal prompt for '{article.title}' was truncated."
                )
                messages[0]["content"][0]["text"] = truncated_prompt_text

        else:  # Default to text-based summarization
            if final_prompt_template:
                prompt = final_prompt_template.format(
                    title=article.title,
                    content=article.content,
                    k_words_each_summary=self.settings.k_words_each_summary,
                )
            else:
                # Default prompt if no template is provided
                prompt = (
                    f'Please summarize the following article titled "{article.title}" in approximately '
                    f"{self.settings.k_words_each_summary} words. Focus on the most important points.\n\n"
                    f"The summary must be in {self.settings.output_language}.\n\n"
                    f"Article content:\n{article.content}"
                )

            truncated_prompt, _ = self._truncate_text_to_token_limit(
                prompt, self.global_config.token_size_threshold
            )
            messages = [{"role": "user", "content": truncated_prompt}]

        try:
            if not messages:
                raise ValueError("Message list for LLM completion is empty.")

            print(f"Summarizing '{article.title}' with model '{self.settings.model}'. API Key: {self._get_masked_api_key()}")
            response = await litellm.acompletion(
                model=self.settings.model,
                messages=messages,
                temperature=self.settings.temperature,
                api_key=self.settings.api_key,
                timeout=120,  # Add a 2-minute timeout
            )
            summary_text = response.choices[0].message.content
            article.summary = f"{summary_text.strip()}\n\n[Source]({article.link})"
            return article
        except Exception as e:
            print(f"Error summarizing article '{article.title}' with LLM: {e}")
            if " multimodal " in str(e).lower():
                article.summary = f"[Error: Could not summarize the provided document.]\n\n[Source]({article.link})"
            else:
                article.summary = f"[Error: Could not summarize article.]\n\n[Source]({article.link})"
            return article

    async def _summarize_text_content(
        self, text_content: str, prompt: str, title: str = "Untitled"
    ) -> str:
        """Helper to summarize raw text content using the configured LLM."""
        truncated_prompt, _ = self._truncate_text_to_token_limit(
            prompt, self.global_config.token_size_threshold
        )
        messages = [{"role": "user", "content": truncated_prompt}]

        try:
            response = await litellm.acompletion(
                model=self.settings.model,
                messages=messages,
                temperature=self.settings.temperature,
                api_key=self.settings.api_key,
                timeout=120,  # Add a 2-minute timeout
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"Error summarizing text content '{title}' with LLM: {e}")
            return f"[Error: Could not summarize text content '{title}']"

    async def summarize_articles_collection(
        self, articles: List[Article], collection_prompt: Optional[str] = None
    ) -> tuple[str, List[Article]]:
        if not articles:
            return "No articles to summarize for this collection.", []

        # 1. Summarize each individual article concurrently
        articles_to_summarize = [a for a in articles if not a.summary]
        already_summarized = [a for a in articles if a.summary]

        tasks = []
        if articles_to_summarize:
            print(f"Summarizing {len(articles_to_summarize)} individual articles...")
            for article in articles_to_summarize:
                tasks.append(self.summarize_text(article))
            
            newly_summarized = await asyncio.gather(*tasks)
            summarized_articles = already_summarized + newly_summarized
        else:
            summarized_articles = already_summarized

        effectively_summarized_articles = [
            a for a in summarized_articles if a.summary and not a.summary.startswith("[Error:")
        ]

        if not effectively_summarized_articles:
            return "No articles with valid summaries.", []

        concatenated_summaries = "\n\n".join(
            [
                f"Title: {art.title}\nLink: {art.link}\nSummary: {art.summary}"
                for art in effectively_summarized_articles
            ]
        )

        if not concatenated_summaries:
            return "No content available for collection summary.", effectively_summarized_articles

        # 2. Build the final prompt for the collection overview
        user_guideline = ""
        if collection_prompt:
            user_guideline = f"Additionally, please follow this specific guideline: '{collection_prompt}'"

        collection_summary_prompt = (
            f"From the following list of article summaries, please identify the {self.settings.n_most_important_news} "
            f"most important news stories. Then, write a cohesive and concise summary of those top stories for a daily news digest. "
            f"The final summary should be approximately {self.settings.k_words_each_summary * self.settings.n_most_important_news} words. "
            f"The final summary must be in {self.settings.output_language}. "
            f"**Crucially, for every piece of information you include, you MUST cite the source using a Markdown link like this: [Source](Link).** "
            f"Highlight the main themes and most significant events. {user_guideline}\n\n"
            f"Here are the summaries:\n{concatenated_summaries}"
        )

        final_summary = await self._summarize_text_content(
            text_content=concatenated_summaries,
            prompt=collection_summary_prompt,
            title="Daily Digest Collection Summary",
        )

        return final_summary or "Could not generate collection summary.", effectively_summarized_articles

