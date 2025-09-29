import os
import asyncio
from datetime import datetime
from src.main import main


# Set dummy environment variables for local testing to avoid errors from missing GitHub/SMTP secrets
# These values are often set by GitHub Actions in a real workflow
if "GITHUB_REPOSITORY" not in os.environ:
    os.environ["GITHUB_REPOSITORY"] = (
        "your-username/your-repo"  # Replace with your actual repo for local GitHub testing
    )

if "BETTER_MORNING_LLM_API_KEY" not in os.environ:
    # IMPORTANT: Replace with a real (local or test) LLM API key if you want to test LLM functionality locally.
    # For basic testing of the flow, a dummy value is fine if litellm is mocked or LLM calls are skipped.
    os.environ["BETTER_MORNING_LLM_API_KEY"] = "sk-dummy-key-for-local-testing"
    print(
        "WARNING: BETTER_MORNING_LLM_API_KEY not set. Using a dummy key. LLM calls will likely fail or use a default."
    )

# Optional: Set these if you want to test email output locally (requires valid SMTP server and credentials)
# if "BETTER_MORNING_SMTP_USERNAME" not in os.environ:
#     os.environ["BETTER_MORNING_SMTP_USERNAME"] = "your_email@example.com"
# if "BETTER_MORNING_SMTP_PASSWORD" not in os.environ:
#     os.environ["BETTER_MORNING_SMTP_PASSWORD"] = "your_email_password"

# Optional: Set this if you want to test GitHub release creation locally.
# This requires a real token with repo write permissions and your repo slug.
# if "BETTER_MORNING_GH_TOKEN" not in os.environ:
#     os.environ["BETTER_MORNING_GH_TOKEN"] = "ghp_your_personal_access_token"


print(f"\n--- Running better-morning locally at {datetime.now()} ---")
print("Note: If GitHub Release or Email output is configured, and secrets are not set,")
print("       the digest will be saved to a local Markdown file.")

if __name__ == "__main__":
    asyncio.run(main())
