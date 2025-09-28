import os
from typing import Optional
import litellm

class LLMSummarizer:
    def __init__(self, model: str = "gpt-4o", api_key: Optional[str] = None):
        self.model = model
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key # litellm can use OPENAI_API_KEY for various models
        elif "OPENAI_API_KEY" not in os.environ and "ANTHROPIC_API_KEY" not in os.environ and "COHERE_API_KEY" not in os.environ:
            print("Warning: No API key provided and no OPENAI_API_KEY, ANTHROPIC_API_KEY, or COHERE_API_KEY found in environment variables. LLM calls might fail.")

    def summarize_text(self, title: str, text_content: str) -> Optional[str]:
        if not text_content:
            return None

        prompt = f"Please summarize the following article titled \"{title}\" in about 3-5 sentences:\n\n{text_content}"

        try:
            response = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}]
            )
            summary = response.choices[0].message.content
            return summary
        except Exception as e:
            print(f"Error summarizing text with LLM: {e}")
            return None
