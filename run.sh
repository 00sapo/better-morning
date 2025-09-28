#!/bin/sh

export BETTER_MORNING_SMTP_USERNAME="federicosimonetta@zoho.com"
export BETTER_MORNING_SMTP_PASSWORD=$(rbw get "zoho mail app password")
export BETTER_MORNING_LLM_API_KEY=$(rbw get gemini_api_key)
export PYTHONPATH=src:$PYTHONPATH # Add this line
uv run run_local.py
