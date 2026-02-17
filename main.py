#!/usr/bin/env python3
"""
Chikuya DX AI Scanner
Scans RSS feeds for AI news relevant to food manufacturing and Google Workspace/AppSheet.
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
    raise ValueError("GEMINI_API_KEY environment variable is not set")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Email configuration (optional - if not set, results will only be logged)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_USER")  # Send to self by default

EMAIL_ENABLED = bool(EMAIL_USER and EMAIL_PASSWORD)
if not EMAIL_ENABLED:
    print("⚠️  Email credentials not set. Results will be logged only (check GitHub Actions logs).")


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
    if 'published_parsed' in entry and entry.published_parsed:
        article_date = datetime(*entry.published_parsed[:6])
    elif 'published' in entry:
        article_date = parse_date(entry.published)
    else:
        return False
    
    if not article_date:
        return False
    
    time_diff = datetime.now() - article_date.replace(tzinfo=None)
    return time_diff <= timedelta(hours=hours)


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
        print(f"Error calling Gemini API: {e}")
        return 0, ""


def scan_feeds() -> List[Dict]:
    """Scan all RSS feeds and return relevant articles."""
    relevant_articles = []
    
    for feed_url in RSS_FEEDS:
        print(f"Scanning feed: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries:
                if is_recent_article(entry, hours=24):
                    title, snippet = get_article_content(entry)
                    link = entry.get('link', '')
                    
                    print(f"  Processing: {title[:50]}...")
                    relevance, summary = rate_and_summarize(title, snippet)
                    
                    if relevance > 7:
                        relevant_articles.append({
                            'title': title,
                            'link': link,
                            'relevance': relevance,
                            'summary': summary,
                            'source': feed_url
                        })
                        print(f"    ✓ Relevant (score: {relevance})")
        except Exception as e:
            print(f"Error processing feed {feed_url}: {e}")
    
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
    subject = "🤖 Chikuya DX: Today's AI Updates"
    
    html_body = f"""
    <html>
    <head></head>
    <body>
        <h2>Chikuya DX AI Scanner - Daily Report</h2>
        <p>Found {len(articles)} relevant AI news items from the last 24 hours:</p>
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

