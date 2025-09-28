import os
from typing import Optional, List
import litellm
from math import ceil # For basic token estimation

from .config import LLMSettings, GlobalConfig, get_secret
from .rss_fetcher import Article

# Rough estimate: 1 token = 4 characters (common for English text)
TOKEN_TO_CHAR_RATIO = 4

class LLMSummarizer:
    def __init__(self, settings: LLMSettings, global_config: GlobalConfig):
        self.settings = settings
        self.global_config = global_config

        try:
            api_key = get_secret(global_config.llm_api_token_env, "LLM API Token")
            # Set the API key for litellm. It automatically detects the provider.
            # For example, if using OpenAI, it would set OPENAI_API_KEY.
            # litellm handles mapping to specific provider env vars internally.
            os.environ[global_config.llm_api_token_env] = api_key
        except ValueError as e:
            print(f"Error initializing LLMSummarizer: {e}")
            print("LLM calls might fail due to missing API token.")

    def _truncate_text_to_token_limit(self, text: str, token_limit: int) -> tuple[str, bool]:
        # Convert token limit to character limit based on approximation
        char_limit = token_limit * TOKEN_TO_CHAR_RATIO
        
        if len(text) > char_limit:
            print(f"Warning: Text content exceeds token size threshold ({token_limit} tokens / {char_limit} characters). Truncating.")
            return text[:char_limit], True
        return text, False

    def summarize_text(self, article: Article, prompt_override: Optional[str] = None) -> Article:
        if not article.content:
            print(f"Warning: No content available for article '{article.title}'. Skipping summarization.")
            return article # Return article as is if no content

        # Determine the prompt to use
        final_prompt_template = prompt_override or self.settings.prompt_template
        if final_prompt_template:
            prompt = final_prompt_template.format(title=article.title, content=article.content, k_words=self.settings.k_words_each_summary)
        else:
            # Default prompt if no template is provided
            prompt = (
                f"Please summarize the following article titled \"{article.title}\" in approximately "
                f"{self.settings.k_words_each_summary} words. Focus on the most important points.\n\n"
                f"{article.content}"
            )
        
        # Truncate prompt if it's too long
        truncated_prompt, was_truncated = self._truncate_text_to_token_limit(
            prompt, self.global_config.token_size_threshold
        )
        if was_truncated:
            print(f"Warning: Summarization prompt for '{article.title}' was truncated.")

        try:
            response = litellm.completion(
                model=self.settings.model,
                messages=[{"role": "user", "content": truncated_prompt}],
                temperature=self.settings.temperature,
            )
            article.summary = response.choices[0].message.content
            return article
        except Exception as e:
            print(f"Error summarizing article '{article.title}' with LLM: {e}")
            article.summary = "[Error: Could not summarize article]"
            return article

    async def summarize_articles_collection(self, articles: List[Article], collection_prompt: Optional[str] = None) -> str:
        if not articles:
            return "No articles to summarize for this collection."

        # 1. Summarize each individual article (if not already summarized or if content is new)
        # For simplicity, we'll re-summarize or ensure summary exists for all new articles.
        summarized_articles: List[Article] = []
        for article in articles:
            if not article.summary or article.content: # If no summary or new content exists, summarize
                summarized_articles.append(self.summarize_text(article, prompt_override=collection_prompt))
            else:
                summarized_articles.append(article)
        
        # Filter out articles that couldn't be summarized or have no content
        effectively_summarized_articles = [a for a in summarized_articles if a.summary and a.summary != "[Error: Could not summarize article]"]

        if not effectively_summarized_articles:
            return "No articles with valid summaries to process for the collection summary."

        # Sort articles by published date to get the N most important (latest as a simple heuristic)
        effectively_summarized_articles.sort(key=lambda x: x.published_date, reverse=True)
        top_n_articles = effectively_summarized_articles[:self.settings.n_most_important_news]

        # Concatenate summaries of the top N articles
        concatenated_summaries = "\n\n".join([
            f"Title: {art.title}\nSummary: {art.summary}"
            for art in top_n_articles
        ])

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
            link=HttpUrl("http://example.com/collection-summary"), # Dummy URL
            published_date=datetime.now(timezone.utc),
            content=concatenated_summaries,
            summary=None # Will be filled by summarize_text
        )

        final_collection_article = self.summarize_text(collection_article, prompt_override=collection_summary_prompt)
        return final_collection_article.summary if final_collection_article.summary else "Could not generate collection summary."
