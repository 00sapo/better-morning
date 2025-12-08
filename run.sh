#!/bin/sh

# export BETTER_MORNING_SMTP_USERNAME="federicosimonetta@zoho.com"
# export BETTER_MORNING_RECIPIENT_EMAIL="federicosimonetta@zoho.com"
export BETTER_MORNING_SMTP_PASSWORD=$(rbw get "zoho mail app password" 2>/dev/null) # Hide rbw stderr
export BETTER_MORNING_LLM_API_KEY=$(rbw get deepseek_api_key 2>/dev/null)             # Hide rbw stderr
export PYTHONPATH=src:$PYTHONPATH                                                   # Add this line

uv run run_local.py
