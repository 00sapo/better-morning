import asyncio
from typing import Optional, List
import litellm
import datetime
import base64
import json

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

    async def select_articles_for_fetching(
        self,
        articles: List[Article],
        collection_prompt: Optional[str] = None,
        previous_digests_context: Optional[str] = None,
    ) -> List[Article]:
        """
        Uses the reasoner LLM to select the most relevant articles for content fetching
        based on their titles and RSS summaries.
        """
        if not articles:
            return []

        # Determine the number of articles to select: 3x the number for the final summary,
        # but not more than the total number of available articles.
        num_to_select = min(len(articles), 3 * self.settings.n_most_important_news)
        if num_to_select == 0:
            return []

        # Prepare a numbered list of articles for the LLM prompt
        article_lines = []
        for i, article in enumerate(articles):
            # Use RSS summary if available, otherwise just title
            summary_text = f" - {article.summary}" if article.summary else ""
            article_lines.append(
                f"{i + 1}. {article.title} ({article.published_date}) - {summary_text[:40]}"
            )
        articles_str = "\n".join(article_lines)

        # Build the prompt with optional previous digests context
        context_section = ""
        if previous_digests_context:
            context_section = f"{previous_digests_context}\n\n"

        prompt = f"""From the following list of articles, select the top {num_to_select} most relevant and important ones according to the impact they have in the world.
Provide your answer as a JSON object with a single key "selected_indices" containing a list of the chosen article numbers (e.g., [1, 5, 10]).
The selected articles will be included in a news digest summary that responds to this description: "{collection_prompt or "A general news digest."}"

{"IMPORTANT: Avoid repeating news that was already covered in the previous digests below. Focus on new developments and different stories. If there are no truly new stories, it is better to say so rather than repeat old news." if previous_digests_context else ""}"
----------------
Previous digests:
{context_section}

----------------
Articles:
{articles_str}
"""

        try:
            print(
                f"Asking LLM to select the best {num_to_select} articles from a list of {len(articles)}..."
            )
            # Prepare completion parameters
            completion_params = {
                "model": self.settings.reasoner_model,
                "messages": [{"content": prompt, "role": "user"}],
                "temperature": self.settings.temperature,
                "response_format": {"type": "json_object"},
                "api_key": self.settings.api_key,
                "timeout": 180,
            }

            # Add thinking effort for reasoner model if configured
            if self.settings.thinking_effort_reasoner is not None:
                if isinstance(self.settings.thinking_effort_reasoner, int):
                    completion_params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": self.settings.thinking_effort_reasoner,
                    }
                else:
                    completion_params["reasoning_effort"] = (
                        self.settings.thinking_effort_reasoner
                    )

            response = await litellm.acompletion(**completion_params)
            choice = response.choices[0].message.content
            selected_data = json.loads(choice)
            selected_indices = selected_data.get("selected_indices", [])

            if not isinstance(selected_indices, list) or not all(
                isinstance(i, int) for i in selected_indices
            ):
                raise ValueError("Invalid format for selected_indices")

            # Convert 1-based indices from LLM to 0-based list indices
            selected_articles = [
                articles[i - 1] for i in selected_indices if 0 < i <= len(articles)
            ]
            print(f"LLM selected {len(selected_articles)} articles for fetching.")
            return selected_articles

        except Exception as e:
            print(f"Error during LLM article selection: {e}")
            # Fallback: return the most recent 'n' articles if LLM selection fails
            print(
                f"Falling back to selecting the {num_to_select} most recent articles."
            )
            sorted_articles = sorted(
                articles, key=lambda a: a.published_date, reverse=True
            )
            return sorted_articles[:num_to_select]

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

            # Base64-encode the PDF content
            base64_pdf = base64.b64encode(article.raw_content).decode("utf-8")
            base64_url = f"data:application/pdf;base64,{base64_pdf}"

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
                        {
                            "type": "file",
                            "file": {"file_data": base64_url},
                        },
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

            print(
                f"Summarizing '{article.title}' with model '{self.settings.light_model}'. API Key: {self._get_masked_api_key()}"
            )
            # Prepare completion parameters
            completion_params = {
                "model": self.settings.light_model,
                "messages": messages,
                "temperature": self.settings.temperature,
                "api_key": self.settings.api_key,
                "timeout": 120,  # Add a 2-minute timeout
            }

            # Add thinking effort for light model if configured
            if self.settings.thinking_effort_light is not None:
                if isinstance(self.settings.thinking_effort_light, int):
                    completion_params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": self.settings.thinking_effort_light,
                    }
                else:
                    completion_params["reasoning_effort"] = (
                        self.settings.thinking_effort_light
                    )

            response = await litellm.acompletion(**completion_params)
            summary_text = response.choices[0].message.content
            article.summary = f"{summary_text.strip()}\n\n[{article.feed_name or 'Source'}]({article.link})"
            return article
        except Exception as e:
            print(f"Error summarizing article '{article.title}' with LLM: {e}")
            if " multimodal " in str(e).lower():
                article.summary = f"[Error: Could not summarize the provided document.]\n\n[{article.feed_name or 'Source'}]({article.link})"
            else:
                article.summary = f"[Error: Could not summarize article.]\n\n[{article.feed_name or 'Source'}]({article.link})"
            return article

    async def _summarize_text_content(
        self,
        text_content: str,
        prompt: str,
        model_name: str,
        title: str = "Untitled",
        timeout=120,
    ) -> str:
        """Helper to summarize raw text content using the configured LLM."""
        truncated_prompt, _ = self._truncate_text_to_token_limit(
            prompt, self.global_config.token_size_threshold
        )
        messages = [{"role": "user", "content": truncated_prompt}]

        try:
            # Prepare completion parameters
            completion_params = {
                "model": model_name,
                "messages": messages,
                "temperature": self.settings.temperature,
                "api_key": self.settings.api_key,
                "timeout": timeout,  # Add a 2-minute timeout
            }

            # Add thinking effort based on which model is being used
            if (
                model_name == self.settings.reasoner_model
                and self.settings.thinking_effort_reasoner is not None
            ):
                if isinstance(self.settings.thinking_effort_reasoner, int):
                    completion_params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": self.settings.thinking_effort_reasoner,
                    }
                else:
                    completion_params["reasoning_effort"] = (
                        self.settings.thinking_effort_reasoner
                    )
            elif (
                model_name == self.settings.light_model
                and self.settings.thinking_effort_light is not None
            ):
                if isinstance(self.settings.thinking_effort_light, int):
                    completion_params["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": self.settings.thinking_effort_light,
                    }
                else:
                    completion_params["reasoning_effort"] = (
                        self.settings.thinking_effort_light
                    )

            response = await litellm.acompletion(**completion_params)
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"Error summarizing text content '{title}' with LLM: {e}")
            return f"[Error: Could not summarize text content '{title}']"

    async def summarize_articles_collection(
        self,
        articles: List[Article],
        collection_prompt: Optional[str] = None,
        previous_digests_context: Optional[str] = None,
    ) -> tuple[str, List[Article]]:
        if not articles:
            return "No articles to summarize for this collection.", []

        # 1. Summarize each individual article concurrently
        # 1. Generate LLM summaries for all articles
        # Note: Articles coming from RSS always have some summary, but we want LLM summaries for the digest
        print(f"Generating LLM summaries for all {len(articles)} articles...")

        tasks = [self.summarize_text(article) for article in articles]
        summarized_articles = await asyncio.gather(*tasks)

        effectively_summarized_articles = [
            a
            for a in summarized_articles
            if a.summary and not a.summary.startswith("[Error:")
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
            return (
                "No content available for collection summary.",
                effectively_summarized_articles,
            )

        # 2. Build the final prompt for the collection overview
        user_guideline = ""
        if collection_prompt:
            user_guideline = f"8. The final summary MUST respond to this description: *{collection_prompt}*"

        # Add context from previous digests if available
        context_section = ""
        if previous_digests_context:
            context_section = f"{previous_digests_context}\n\n"

        collection_summary_prompt = (
            f"Here are a few digests of previous news and some articles summarized. You should select the most important stories presented in the summarized articles below, avoiding previously covered stories.\n\n"
            f"Consider that today is {datetime.datetime.now().strftime('%Y %B, %-d')}.\n\n"
            f"1. Identify the {self.settings.n_most_important_news} most important stories."
            f"2. Considering that the same story may be repeated in multitiple articles from different perspectives and with different details, write a cohesive and concise summary of those top stories. "
            f"3. The final summary must be in {self.settings.output_language}. "
            f"4. **Crucially, for every piece of information you include, you MUST cite the source using a Markdown link like this: ([feed name](Link)).** "
            f"5. The final summary MUST be of {self.settings.k_words_each_summary * min(self.settings.n_most_important_news, len(effectively_summarized_articles))} words. "
            f"6. Answer with only the final summary, without introductions nor conclusions. "
            f"7. {'IMPORTANT: Avoid repeating news that was already covered in the previous digests below. Focus on new developments and different stories. If there are no truly new stories, it is better to say so rather than repeat old news.' if previous_digests_context else ''}\n\n"
            f"{user_guideline}\n\n"
            f"Previous digests:\n"
            f"{context_section}\n\n----------------"
            f"Article summaries:\n\n{concatenated_summaries}"
        )

        final_summary = await self._summarize_text_content(
            text_content=concatenated_summaries,
            prompt=collection_summary_prompt,
            model_name=self.settings.reasoner_model,
            title="Daily Digest Collection Summary",
            timeout=300,
        )

        return (
            final_summary or "Could not generate collection summary.",
            effectively_summarized_articles,
        )
