from typing import List, Optional, Literal
from pydantic import BaseModel, HttpUrl, Field
import toml
import os


# --- LLM Settings ---
class LLMSettings(BaseModel):
    model: str = ""
    temperature: float = 0.7
    n_most_important_news: int = 5
    k_words_each_summary: int = 100
    prompt_template: Optional[str] = None  # Collection-specific prompt or for filtering
    output_language: str = "english"  # Added language setting
    api_key: Optional[str] = None  # To hold the resolved API key


# --- Content Extraction Settings ---
class ContentExtractionSettings(BaseModel):
    follow_article_links: bool = False
    parser_type: Optional[str] = "html.parser"


# --- Output Settings ---
class OutputSettings(BaseModel):
    output_type: Literal["github_release", "email"] = "github_release"
    smtp_server: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_username_env: Optional[str] = None  # Env var name for SMTP username
    smtp_password_env: Optional[str] = None  # Env var name for SMTP password
    recipient_email: Optional[str] = None
    github_token_env: Optional[str] = "GH_TOKEN"  # Env var name for GitHub token


# --- Global Configuration ---
class GlobalConfig(BaseModel):
    llm_api_token_env: str = (
        "BETTER_MORNING_LLM_API_KEY"  # Default env var for LLM API token
    )
    token_size_threshold: int = 128 * 1024  # 128K tokens
    llm_settings: LLMSettings = Field(default_factory=LLMSettings)
    content_extraction_settings: ContentExtractionSettings = Field(
        default_factory=ContentExtractionSettings
    )
    output_settings: OutputSettings = Field(default_factory=OutputSettings)


# --- RSS Feed Definition ---
class RSSFeed(BaseModel):
    url: HttpUrl
    name: Optional[str] = None  # Name is optional now
    max_articles: Optional[int] = None  # Max articles to fetch from this feed


# --- Collection-specific overrides (for parsing TOML) ---
class CollectionOverrides(BaseModel):
    llm_settings: Optional[LLMSettings] = None
    content_extraction_settings: Optional[ContentExtractionSettings] = None
    collection_prompt: Optional[str] = None  # Prompt specific to this collection


# --- Fully resolved Collection Configuration ---
class Collection(BaseModel):
    name: str
    feeds: List[RSSFeed]
    # These will hold the *resolved* settings after merging with global
    llm_settings: LLMSettings
    content_extraction_settings: ContentExtractionSettings
    collection_prompt: Optional[str] = None


# --- Main Configuration Loader ---
GLOBAL_CONFIG_FILE = "config.toml"  # Assuming global config is at project root


def load_global_config(path: str = GLOBAL_CONFIG_FILE) -> GlobalConfig:
    try:
        if not os.path.exists(path):
            print(
                f"Warning: Global config file '{path}' not found. Using default global settings."
            )
            return GlobalConfig()
        data = toml.load(path)
        return GlobalConfig(**data)
    except Exception as e:
        print(f"Error loading or validating global config from {path}: {e}")
        raise


def load_collection(collection_path: str, global_config: GlobalConfig) -> Collection:
    """
    Loads a collection configuration, merging with global settings.
    """
    try:
        collection_data = toml.load(collection_path)

        # Allow `follow_article_links` to be a top-level key in collection TOML for convenience.
        # If it exists, we move it into the `content_extraction_settings` dictionary before parsing.
        if "follow_article_links" in collection_data:
            if "content_extraction_settings" not in collection_data:
                collection_data["content_extraction_settings"] = {}
            # This will overwrite if a value also exists in the dictionary, which is desired.
            collection_data["content_extraction_settings"]["follow_article_links"] = (
                collection_data.pop("follow_article_links")
            )

        # Use CollectionOverrides to parse the collection-specific fields from TOML
        # This allows for partial override declarations without requiring all fields
        overrides = CollectionOverrides(
            llm_settings=collection_data.get("llm_settings"),
            content_extraction_settings=collection_data.get(
                "content_extraction_settings"
            ),
            collection_prompt=collection_data.get("collection_prompt"),
        )

        # Merge LLM settings: collection overrides global defaults
        # Start with the base defaults from the Pydantic model.
        resolved_llm_settings_data = LLMSettings().model_dump()
        # Layer the global config settings on top.
        resolved_llm_settings_data.update(
            global_config.llm_settings.model_dump(exclude_unset=True)
        )
        # Finally, layer the collection-specific settings, which take highest priority.
        if overrides.llm_settings:
            resolved_llm_settings_data.update(
                overrides.llm_settings.model_dump(exclude_unset=True)
            )
        resolved_llm_settings = LLMSettings(**resolved_llm_settings_data)

        # After resolving the model, resolve and set the API key for it.
        try:
            api_key = get_secret(
                global_config.llm_api_token_env,
                f"LLM API Token for model '{resolved_llm_settings.model}'",
            )
            resolved_llm_settings.api_key = api_key
        except ValueError as e:
            print(
                f"Warning: Could not resolve API key for collection '{collection_data['name']}'. LLM calls may fail. Error: {e}"
            )

        # Merge Content Extraction settings: collection overrides global defaults
        # Apply the same hierarchical merging logic.
        resolved_content_extraction_settings_data = (
            ContentExtractionSettings().model_dump()
        )
        resolved_content_extraction_settings_data.update(
            global_config.content_extraction_settings.model_dump(exclude_unset=True)
        )
        if overrides.content_extraction_settings:
            resolved_content_extraction_settings_data.update(
                overrides.content_extraction_settings.model_dump(exclude_unset=True)
            )
        resolved_content_extraction_settings = ContentExtractionSettings(
            **resolved_content_extraction_settings_data
        )

        # Construct the final Collection object with resolved settings
        return Collection(
            name=collection_data["name"],
            feeds=[RSSFeed(**f) for f in collection_data["feeds"]],
            llm_settings=resolved_llm_settings,
            content_extraction_settings=resolved_content_extraction_settings,
            collection_prompt=overrides.collection_prompt,
        )
    except Exception as e:
        print(
            f"Error loading or validating collection config from {collection_path}: {e}"
        )
        raise


def get_secret(env_var_name: Optional[str], config_name: str) -> str:
    """Retrieves a secret from environment variables."""
    if env_var_name is None:
        raise ValueError(
            f"Environment variable name for {config_name} is not configured."
        )
    secret = os.getenv(env_var_name)
    if secret is None:
        raise ValueError(
            f"Environment variable '{env_var_name}' for {config_name} is not set."
        )
    return secret
