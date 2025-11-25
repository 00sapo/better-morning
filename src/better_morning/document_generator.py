import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import markdown2
import json
import os
from pathlib import Path

from .config import OutputSettings, GlobalConfig, get_secret
from .rss_fetcher import Article


class DocumentGenerator:
    def __init__(self, output_settings: OutputSettings, global_config: GlobalConfig):
        self.output_settings = output_settings
        self.global_config = global_config
        self.digest_history_file = "history/digest_history.json"
        
    def _ensure_history_dir(self):
        """Ensure the history directory exists."""
        Path("history").mkdir(exist_ok=True)
        
    def load_previous_digests(self) -> List[Dict]:
        """Load the last n digests from history."""
        self._ensure_history_dir()
        if not os.path.exists(self.digest_history_file):
            return []
            
        try:
            with open(self.digest_history_file, 'r', encoding='utf-8') as f:
                all_digests = json.load(f)
            # Return the most recent digests up to context_digest_size
            return all_digests[-self.global_config.context_digest_size:]
        except Exception as e:
            print(f"Warning: Could not load digest history: {e}")
            return []
    
    def save_digest_to_history(self, collection_summaries: Dict[str, str], date: datetime):
        """Save only the collection summaries to history, without feed reports and detailed article summaries."""
        self._ensure_history_dir()
        
        # Load existing digests
        all_digests = []
        if os.path.exists(self.digest_history_file):
            try:
                with open(self.digest_history_file, 'r', encoding='utf-8') as f:
                    all_digests = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load existing digest history: {e}")
        
        # Build a clean digest with only the collection summaries
        clean_digest_parts = [f"# Daily Digest - {date.strftime('%Y-%m-%d')}", "## General Overview"]
        for collection_name, summary in collection_summaries.items():
            clean_digest_parts.append(f"\n### {collection_name}\n")
            clean_digest_parts.append(summary)
        
        clean_digest_content = "\n".join(clean_digest_parts)
        
        # Add the new digest
        new_digest = {
            "date": date.strftime('%Y-%m-%d'),
            "content": clean_digest_content
        }
        all_digests.append(new_digest)
        
        # Keep only the most recent digests (double the context size to have some buffer)
        max_stored = max(self.global_config.context_digest_size * 2, 10)
        all_digests = all_digests[-max_stored:]
        
        # Save back to file
        try:
            with open(self.digest_history_file, 'w', encoding='utf-8') as f:
                json.dump(all_digests, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Could not save digest to history: {e}")
            
    def get_context_for_llm(self) -> str:
        """Get formatted context from previous digests for LLM consumption."""
        previous_digests = self.load_previous_digests()
        if not previous_digests:
            return ""
            
        context_parts = ["Here are the previous digests for context (avoid repeating similar news):"]
        for digest in previous_digests:
            context_parts.append(f"\n--- Digest from {digest['date']} ---")
            context_parts.append(digest['content'])
            
        context_parts.append("\n--- End of previous digests ---\n")
        return "\n".join(context_parts)

    def generate_markdown_digest(
        self,
        collection_summaries: Dict[str, str],
        articles_by_collection: Dict[str, List[Article]],
        skipped_sources: List[str],
        date: datetime,
        fetch_reports: Optional[Dict[str, dict]] = None,
        collection_errors: Optional[Dict[str, str]] = None,
    ) -> str:
        """Formats the digest with a top-level overview and detailed summaries."""
        title = f"# Daily Digest - {date.strftime('%Y-%m-%d')}"

        # Build the General Overview from individual collection summaries
        overview_parts = ["## General Overview"]
        for collection_name, summary in collection_summaries.items():
            overview_parts.append(f"\n### {collection_name}\n")
            overview_parts.append(summary)
        overview_section = "\n".join(overview_parts)

        # Generate collection errors section (for collections that failed early)
        collection_errors_section = ""
        if collection_errors:
            collection_errors_section = "\n## ⚠️ Collection Processing Errors\n\n"
            collection_errors_section += "*The following collections encountered errors and could not be fully processed. Please check your configuration:*\n\n"
            for collection_name, error_msg in collection_errors.items():
                collection_errors_section += f"- **{collection_name}**: {error_msg}\n"

        # Generate feed report section
        feed_report_section = ""
        if fetch_reports:
            all_successful = []
            all_failed = []
            total_articles = 0
            total_feeds = 0

            for collection_name, report in fetch_reports.items():
                # Defensive checks: handle missing or empty keys gracefully
                successful_feeds = report.get("successful", [])
                failed_feeds = report.get("failed", [])
                
                all_successful.extend(
                    [(s, collection_name) for s in successful_feeds]
                )
                all_failed.extend([(f, collection_name) for f in failed_feeds])
                total_articles += sum(
                    s.get("articles_fetched", 0) for s in successful_feeds
                )
                total_feeds += report.get("total_feeds", 0)

            success_rate = (
                len(all_successful) / total_feeds if total_feeds > 0 else 0
            )

            feed_report_section = f"\n## Feed Processing Report\n\n"
            feed_report_section += f"**Summary**: {len(all_successful)}/{total_feeds} feeds successful ({success_rate:.1%}) • {total_articles} articles fetched\n\n"

            if all_successful:
                feed_report_section += "### ✅ Successful Feeds\n\n"
                for feed, collection in all_successful:
                    feed_report_section += f"- **{feed['name']}** ({collection}): {feed['articles_fetched']} articles\n  `{feed['url']}`\n\n"

            if all_failed:
                feed_report_section += "### ❌ Failed Feeds\n\n"
                feed_report_section += (
                    "*Consider removing these feeds from your collections:*\n\n"
                )
                for feed, collection in all_failed:
                    feed_report_section += f"- **{feed['name']}** ({collection}): {feed['error']}\n  `{feed['url']}`\n\n"

        skipped_sources_section = ""
        if skipped_sources:
            skipped_sources_section = "\n## Skipped Sources\n\nThe following sources were skipped due to a high number of consecutive content extraction errors:\n\n"
            for source in skipped_sources:
                skipped_sources_section += f"- {source}\n"

        detailed_sections = ["## Detailed Summaries"]
        for collection_name, articles in articles_by_collection.items():
            # Filter out articles that might have failed summarization
            valid_articles = [
                a
                for a in articles
                if a.summary and not a.summary.startswith("[Error:")
            ]
            if not valid_articles:
                continue

            detailed_sections.append(f"\n### Collection: {collection_name}\n")
            for article in valid_articles:
                detailed_sections.append(f"#### {article.title}\n")
                detailed_sections.append(f"{article.summary}\n")

        # Assemble the final document
        final_document_parts = [title, overview_section]
        if collection_errors_section:
            final_document_parts.extend(["---", collection_errors_section])
        if feed_report_section:
            final_document_parts.extend(["---", feed_report_section])
        if skipped_sources_section:
            final_document_parts.extend(["---", skipped_sources_section])
        final_document_parts.extend(["---"] + detailed_sections)

        return "\n\n".join(final_document_parts)

    def send_via_email(self, subject: str, body: str, recipient_email: str):
        if (
            not self.output_settings.smtp_server
            or not self.output_settings.smtp_port
            or not self.output_settings.smtp_username_env
            or not self.output_settings.smtp_password_env
        ):
            print("Error: SMTP settings are incomplete. Cannot send email.")
            return

        try:
            smtp_username = get_secret(
                self.output_settings.smtp_username_env, "SMTP Username"
            )
            smtp_password = get_secret(
                self.output_settings.smtp_password_env, "SMTP Password"
            )

            # Convert the Markdown body to HTML
            html_body = markdown2.markdown(body)

            # Create message with HTML content
            msg = MIMEMultipart()
            msg["From"] = smtp_username
            msg["To"] = recipient_email
            msg["Subject"] = subject

            # Attach only the HTML part
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL(
                self.output_settings.smtp_server, self.output_settings.smtp_port
            ) as server:
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            print(f"Email digest sent successfully to {recipient_email}")
        except Exception as e:
            print(f"Error sending email digest: {e}")

    def create_github_release(
        self, tag_name: str, release_name: str, body: str, repo_slug: str
    ):
        if not self.output_settings.github_token_env:
            print(
                "Error: GitHub token environment variable not configured. Cannot create GitHub release."
            )
            return

        try:
            github_token = get_secret(
                self.output_settings.github_token_env, "GitHub Token"
            )
            headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
            }
            data = {
                "tag_name": tag_name,
                "name": release_name,
                "body": body,
                "draft": False,
                "prerelease": False,
            }

            # repo_slug should be in format 'owner/repo'
            api_url = f"https://api.github.com/repos/{repo_slug}/releases"

            response = requests.post(api_url, headers=headers, json=data)
            response.raise_for_status()  # Raise an exception for HTTP errors
            print(
                f"GitHub release '{release_name}' created successfully at {response.json()['html_url']}"
            )
        except requests.exceptions.RequestException as e:
            print(f"Error creating GitHub release: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while creating GitHub release: {e}")
