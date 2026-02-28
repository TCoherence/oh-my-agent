---
name: top-5-daily-news
description: Fetch and display the top 5 daily news headlines from reliable sources (BBC News, Reuters). Use when the user asks about today's news, current events, top headlines, what's happening in the world, or any request to check the latest news.
---

# Top 5 Daily News

Fetch the top 5 headlines from reliable news RSS feeds. No API key required.

## Usage

```bash
python skills/top-5-daily-news/scripts/fetch_news.py
```

To prefer a specific source (BBC or Reuters):

```bash
python skills/top-5-daily-news/scripts/fetch_news.py BBC
python skills/top-5-daily-news/scripts/fetch_news.py Reuters
```

## Sources (tried in order)

1. BBC News — `https://feeds.bbci.co.uk/news/rss.xml`
2. Reuters — `https://feeds.reuters.com/reuters/topNews`
3. AP News (via RSSHub) — fallback

If one source fails, the script automatically tries the next.

## Output

The script prints the top 5 headlines with titles, URLs, and publication dates. Present the results to the user in a clean numbered list, summarizing each headline in plain language if helpful.
