"""Recent headlines for a ticker from Yahoo Finance's public RSS feed."""
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


def fetch_news(ticker, count=10):
    """Newest-first list of {title, source, url, published (epoch seconds or None), ticker}. [] on any failure."""
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(ticker)}&region=US&lang=en-US"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        articles = []
        for item in root.findall('.//item')[:count]:
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            pub = item.findtext('pubDate', '')
            source = item.findtext('source', 'Yahoo Finance')
            pub_ts = None
            if pub:
                try:
                    pub_ts = int(parsedate_to_datetime(pub).timestamp())
                except Exception:
                    pass
            if title:
                articles.append({'title': title, 'source': source, 'url': link, 'published': pub_ts, 'ticker': ticker})
        return articles
    except Exception:
        return []
