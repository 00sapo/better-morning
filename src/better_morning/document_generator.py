import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List
from datetime import datetime
import os

from .config import OutputSettings, GlobalConfig, get_secret
from .rss_fetcher import Article # Still needed for type hinting potentially elsewhere

class DocumentGenerator:
    def __init__(self, output_settings: OutputSettings, global_config: GlobalConfig):
        self.output_settings = output_settings
        self.global_config = global_config

    def generate_markdown_digest(self, collection_summaries: Dict[str, str], date: datetime) -> str:
        if not collection_summaries:
            return "# Daily News Digest\n\nNo news to report for today.\n"

        digest_content = f"# Daily News Digest - {date.strftime("%Y-%m-%d")}\n\n"

        for collection_name, summary in collection_summaries.items():
            digest_content += f"## {collection_name}\n\n"
            digest_content += f"{summary}\n\n"
            digest_content += "---\n\n" # Separator for collections

        return digest_content

    def send_via_email(self, subject: str, body: str, recipient_email: str):
        if not self.output_settings.smtp_server or not self.output_settings.smtp_username_env or not self.output_settings.smtp_password_env:
            print("Error: SMTP settings are incomplete. Cannot send email.")
            return

        try:
            smtp_username = get_secret(self.output_settings.smtp_username_env, "SMTP Username")
            smtp_password = get_secret(self.output_settings.smtp_password_env, "SMTP Password")

            msg = MIMEMultipart()
            msg['From'] = smtp_username
            msg['To'] = recipient_email
            msg['Subject'] = subject

            with smtplib.SMTP(self.output_settings.smtp_server, self.output_settings.smtp_port) as server:
                server.starttls()  # Secure the connection
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            print(f"Email digest sent successfully to {recipient_email}")
        except Exception as e:
            print(f"Error sending email digest: {e}")

    def create_github_release(self, tag_name: str, release_name: str, body: str, repo_slug: str):
        if not self.output_settings.github_token_env:
            print("Error: GitHub token environment variable not configured. Cannot create GitHub release.")
            return
        
        try:
            github_token = get_secret(self.output_settings.github_token_env, "GitHub Token")
            headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            data = {
                "tag_name": tag_name,
                "name": release_name,
                "body": body,
                "draft": False,
                "prerelease": False
            }
            
            # repo_slug should be in format 'owner/repo'
            api_url = f"https://api.github.com/repos/{repo_slug}/releases"
            
            response = requests.post(api_url, headers=headers, json=data)
            response.raise_for_status() # Raise an exception for HTTP errors
            print(f"GitHub release '{release_name}' created successfully at {response.json()['html_url']}")
        except requests.exceptions.RequestException as e:
            print(f"Error creating GitHub release: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while creating GitHub release: {e}")
