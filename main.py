#!/usr/bin/env python3
"""
Chikuya DX AI Scanner
Scans RSS feeds for AI news relevant to food manufacturing and Google Workspace/AppSheet.
Version: 2.0 (Email optional)
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import List, Dict
import feedparser
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# RSS Feeds to scan
RSS_FEEDS = [
    "https://workspaceupdates.googleblog.com/feeds/posts/default",
    "https://cloudblog.withgoogle.com/products/ai-machine-learning/rss/",
    "https://news.mit.edu/rss/topic/artificial-intelligence"
]

# Initialize Gemini AI
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY environment variable is not set. Please add it as a GitHub Secret.")

try:
    genai.configure(api_key=GEMINI_API_KEY)
    # Try different model names in order of preference
    model = None
    model_names = ['gemini-1.5-flash-latest', 'gemini-1.5-pro', 'gemini-pro', 'gemini-1.5-flash']
    
    for model_name in model_names:
        try:
            model = genai.GenerativeModel(model_name)
            print(f"✅ Using model: {model_name}")
            break
        except Exception as e:
            if model_name == model_names[-1]:  # Last model, raise the error
                raise
            continue
    
    if model is None:
        raise ValueError("Could not initialize any Gemini model. Please check your API key and model availability.")
except Exception as e:
    error_msg = str(e)
    if "API key" in error_msg or "API_KEY" in error_msg:
        raise ValueError(
            "❌ Invalid Gemini API key. Please:\n"
            "1. Get a new API key from: https://aistudio.google.com/app/apikey\n"
            "2. Update the GEMINI_API_KEY secret in GitHub repository settings"
        ) from e
    else:
        raise

# Email configuration (optional - if not set, results will only be logged)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_USER")  # Send to self by default

EMAIL_ENABLED = bool(EMAIL_USER and EMAIL_PASSWORD)
if not EMAIL_ENABLED:
    print("⚠️  Email credentials not set. Results will be logged only (check GitHub Actions logs).")
    print("✅ Running with email disabled - this is expected if EMAIL_USER/EMAIL_PASSWORD secrets are not set.")


def parse_date(date_string: str) -> datetime:
    """Parse various date formats from RSS feeds."""
    try:
        # Try parsing as struct_time (feedparser format)
        if hasattr(date_string, 'tm_year'):
            return datetime(*date_string[:6])
        # Try parsing as string
        if isinstance(date_string, str):
            # Try common formats
            for fmt in ['%a, %d %b %Y %H:%M:%S %Z', '%a, %d %b %Y %H:%M:%S %z', 
                       '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ']:
                try:
                    return datetime.strptime(date_string, fmt)
                except ValueError:
                    continue
        # Fallback: use feedparser's parsed date
        parsed = feedparser._parse_date(date_string)
        if parsed:
            return datetime(*parsed[:6])
    except Exception as e:
        print(f"Error parsing date: {date_string}, error: {e}")
    return None


def is_recent_article(entry: Dict, hours: int = 24) -> bool:
    """Check if article was published within the last N hours."""
    article_date = None
    
    if 'published_parsed' in entry and entry.published_parsed:
        try:
            # feedparser provides struct_time, convert to datetime
            article_date = datetime(*entry.published_parsed[:6])
        except (TypeError, ValueError):
            pass
    elif 'published' in entry:
        article_date = parse_date(entry.published)
    
    if not article_date:
        return False
    
    # Remove timezone info for comparison (assume UTC if not specified)
    if article_date.tzinfo is not None:
        article_date = article_date.replace(tzinfo=None)
    
    now = datetime.utcnow()  # Use UTC for consistency
    time_diff = now - article_date
    
    # Check if within the time window
    is_recent = time_diff <= timedelta(hours=hours) and time_diff >= timedelta(hours=0)
    
    return is_recent


def get_article_content(entry: Dict) -> tuple:
    """Extract title and snippet from RSS entry."""
    title = entry.get('title', 'No Title')
    
    # Try to get description/summary
    snippet = entry.get('summary', '') or entry.get('description', '')
    
    # If no snippet, try to get content
    if not snippet and 'content' in entry:
        if isinstance(entry.content, list) and len(entry.content) > 0:
            snippet = entry.content[0].get('value', '')
    
    # Clean up HTML tags if present
    if snippet:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(snippet, 'html.parser')
        snippet = soup.get_text()[:500]  # Limit to 500 chars
    
    return title, snippet


def rate_and_summarize(title: str, snippet: str) -> tuple:
    """Use Gemini AI to rate relevance and generate summary if relevant."""
    system_prompt = """You are a DX Manager at a Japanese food manufacturing company. 
Rate this news item on a scale of 1-10 for relevance to factory automation, AppSheet development, or operational efficiency. 
If relevance is > 7, write a one-sentence summary in Japanese and English.

Format your response as:
RELEVANCE: [number 1-10]
SUMMARY: [if relevance > 7, provide one sentence in Japanese and English, otherwise "N/A"]"""

    prompt = f"{system_prompt}\n\nTitle: {title}\n\nSnippet: {snippet}"
    
    try:
        response = model.generate_content(prompt)
        response_text = response.text
        
        # Parse response
        relevance = 0
        summary = ""
        
        for line in response_text.split('\n'):
            if line.startswith('RELEVANCE:'):
                try:
                    relevance = int(line.split(':')[1].strip())
                except:
                    pass
            elif line.startswith('SUMMARY:'):
                summary = line.split(':', 1)[1].strip() if ':' in line else ""
        
        return relevance, summary
    except Exception as e:
        error_msg = str(e)
        if "API key" in error_msg or "API_KEY" in error_msg:
            print(f"    ❌ API Key Error: Please check your GEMINI_API_KEY secret in GitHub settings")
        else:
            print(f"    ⚠️  Error calling Gemini API: {error_msg[:100]}")
        return 0, ""


def scan_feeds() -> List[Dict]:
    """Scan all RSS feeds and return relevant articles."""
    relevant_articles = []
    all_processed = []
    
    for feed_url in RSS_FEEDS:
        print(f"Scanning feed: {feed_url}")
        feed_count = 0
        try:
            feed = feedparser.parse(feed_url)
            total_entries = len(feed.entries) if feed.entries else 0
            print(f"  Found {total_entries} total entries in feed")
            
            # Show most recent article date for debugging
            if total_entries > 0:
                most_recent = None
                for entry in feed.entries[:3]:  # Check first 3 entries
                    if 'published_parsed' in entry and entry.published_parsed:
                        article_date = datetime(*entry.published_parsed[:6])
                        if not most_recent or article_date > most_recent:
                            most_recent = article_date
                
                if most_recent:
                    hours_ago = (datetime.now() - most_recent.replace(tzinfo=None)).total_seconds() / 3600
                    print(f"  Most recent article: {most_recent.strftime('%Y-%m-%d %H:%M:%S')} ({hours_ago:.1f} hours ago)")
            
            for entry in feed.entries:
                if is_recent_article(entry, hours=168):  # 7 days (168 hours)
                    feed_count += 1
                    title, snippet = get_article_content(entry)
                    link = entry.get('link', '')
                    
                    print(f"  Processing: {title[:50]}...")
                    relevance, summary = rate_and_summarize(title, snippet)
                    
                    article_info = {
                        'title': title,
                        'link': link,
                        'relevance': relevance,
                        'summary': summary,
                        'source': feed_url
                    }
                    all_processed.append(article_info)
                    
                    if relevance > 7:
                        relevant_articles.append(article_info)
                        print(f"    ✓ Relevant (score: {relevance}/10)")
                    else:
                        print(f"    - Not relevant (score: {relevance}/10)")
            
            if feed_count == 0:
                print(f"  ⚠️  No articles found from the last 7 days")
            else:
                print(f"  ✓ Processed {feed_count} article(s) from last 7 days")
        except Exception as e:
            print(f"  ❌ Error processing feed {feed_url}: {e}")
            import traceback
            traceback.print_exc()
    
    # Summary
    print(f"\n📊 Summary: Processed {len(all_processed)} total articles, {len(relevant_articles)} relevant (score > 7)")
    
    return relevant_articles


def send_email_report(articles: List[Dict]):
    """Send email report with relevant articles."""
    if not EMAIL_ENABLED:
        print("Email not configured. Skipping email send.")
        return
    
    if not articles:
        print("No relevant articles found. Skipping email.")
        return
    
    # Create email content
    subject = "🤖 Chikuya DX: Weekly AI Updates"
    
    html_body = f"""
    <html>
    <head></head>
    <body>
        <h2>Chikuya DX AI Scanner - Weekly Report</h2>
        <p>Found {len(articles)} relevant AI news items from the last 7 days:</p>
        <ul>
    """
    
    for article in articles:
        html_body += f"""
        <li>
            <strong><a href="{article['link']}">{article['title']}</a></strong><br>
            <em>Relevance Score: {article['relevance']}/10</em><br>
            {article['summary']}<br>
            <small>Source: {article['source']}</small>
        </li>
        <br>
        """
    
    html_body += """
        </ul>
        <p>---</p>
        <p><small>This is an automated report from Chikuya DX AI Scanner</small></p>
    </body>
    </html>
    """
    
    # Create message
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    
    # Attach HTML body
    msg.attach(MIMEText(html_body, 'html'))
    
    # Send email
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Email sent successfully to {EMAIL_TO}")
    except Exception as e:
        print(f"Error sending email: {e}")
        raise


def main():
    """Main execution function."""
    print("=" * 60)
    print("Chikuya DX AI Scanner - Starting scan...")
    print("=" * 60)
    
    # Scan feeds
    relevant_articles = scan_feeds()
    
    print(f"\nFound {len(relevant_articles)} relevant articles")
    
    # Send email if there are relevant articles
    if relevant_articles:
        # Print results to console/logs
        print("\n" + "=" * 60)
        print("📊 RELEVANT ARTICLES FOUND:")
        print("=" * 60)
        for i, article in enumerate(relevant_articles, 1):
            print(f"\n{i}. {article['title']}")
            print(f"   Relevance: {article['relevance']}/10")
            print(f"   Summary: {article['summary']}")
            print(f"   Link: {article['link']}")
            print(f"   Source: {article['source']}")
        
        # Try to send email (if configured)
        send_email_report(relevant_articles)
    else:
        print("No relevant articles to report.")
    
    print("\nScan complete!")


if __name__ == "__main__":
    main()

