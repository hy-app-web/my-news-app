"""Public RSS -> minimal JSON. Python standard library only; never fetch article pages."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TOPICS = ['AI', 'テクノロジー', 'ビジネス', '経済', 'バズ', 'グルメ', 'イベント', '東京', '沖縄', 'ハンドメイド']
FEEDS = [{'id': str(i), 'topic': t, 'url': ('https://b.hatena.ne.jp/hotentry.rss' if t == 'バズ' else 'https://b.hatena.ne.jp/search/tag?' + urlencode({'q': t, 'mode': 'rss', 'sort': 'recent'}))} for i, t in enumerate(TOPICS)]
MAX_BYTES = 2 * 1024 * 1024

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)

def canonical_url(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
            return None
        # Keep functional query parameters; discard common tracking parameters only.
        query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid'))
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or '/', urlencode(query), ''))
    except ValueError:
        return None

def parse_date(value):
    try:
        date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        try:
            date = parsedate_to_datetime(value.strip())
        except (ValueError, TypeError):
            return None
    if date.tzinfo is None:
        return None
    if date > datetime.now(timezone.utc) + timedelta(hours=1):
        return None
    return date.astimezone(timezone.utc).isoformat(timespec='seconds')

def local(tag):
    return tag.rsplit('}', 1)[-1]

def parse_feed(body, topic):
    if len(body) > MAX_BYTES or re.search(br'<!\s*(DOCTYPE|ENTITY)', body, re.I):
        raise ValueError('oversized or unsafe XML')
    root = ET.fromstring(body)
    if local(root.tag) not in ('RDF', 'rss', 'feed'):
        raise ValueError('not an RSS/Atom feed')
    articles = []
    for item in root.iter():
        if local(item.tag) not in ('item', 'entry'):
            continue
        title, url, published = '', None, None
        for node in item:
            name = local(node.tag)
            if name == 'title':
                title = ' '.join(''.join(node.itertext()).split())[:400]
            elif name == 'link' and (not node.attrib.get('rel') or node.attrib.get('rel') == 'alternate'):
                url = canonical_url(node.attrib.get('href') or node.text or '')
            elif name in ('date', 'pubDate', 'published', 'updated') and not published:
                published = parse_date(node.text or '')
        if not title or not url or not published:
            continue
        articles.append({'id': hashlib.sha256(url.encode()).hexdigest()[:20], 'title': title, 'url': url,
                         'source': urlsplit(url).hostname, 'publishedAt': published, 'topics': [topic]})
    if any(local(n.tag) in ('item', 'entry') for n in root.iter()) and not articles:
        raise ValueError('feed contains no valid dated articles')
    return articles[:80]

def fetch_feed(feed):
    request = Request(feed['url'], headers={'User-Agent': 'MyNewsApp/1.0 (+https://github.com/hy-app-web/my-news-app)', 'Accept': 'application/rss+xml, application/rdf+xml, application/atom+xml, application/xml'})
    with urlopen(request, timeout=25) as response:
        return parse_feed(response.read(MAX_BYTES + 1), feed['topic'])

def merge_articles(articles):
    merged = {}
    for article in articles:
        url = canonical_url(article['url'])
        if not url:
            continue
        if url not in merged:
            merged[url] = {**article, 'url': url, 'topics': list(article['topics'])}
        else:
            current = merged[url]
            current['topics'] = sorted(set(current['topics'] + article['topics']), key=TOPICS.index)
            if article['publishedAt'] > current['publishedAt']:
                current['publishedAt'] = article['publishedAt']
    return sorted(merged.values(), key=lambda a: (a['publishedAt'], a['id']), reverse=True)[:600]

def collect(root=ROOT, fetch=fetch_feed, now=None):
    now = now or stamp()
    data = root / 'docs/data'
    cache_path = root / 'collector-cache.json'
    cache = read_json(cache_path, {})
    results, success = [], 0
    for feed in FEEDS:
        prior = cache.get(feed['id'], {})
        # Also protect the provider from repeated manual executions within 30 minutes.
        last = prior.get('lastAttemptAt')
        if last and datetime.fromisoformat(now) - datetime.fromisoformat(last) < timedelta(minutes=30):
            results.append({'topic': feed['topic'], 'state': 'error' if prior.get('failed') else 'cached', 'lastSuccessAt': prior.get('lastSuccessAt')})
            continue
        try:
            articles = fetch(feed)
            cache[feed['id']] = {'articles': articles, 'lastSuccessAt': now, 'lastAttemptAt': now}
            success += 1
            results.append({'topic': feed['topic'], 'state': 'ok', 'lastSuccessAt': now})
        except Exception as error:
            cache[feed['id']] = {**prior, 'lastAttemptAt': now, 'failed': True}
            results.append({'topic': feed['topic'], 'state': 'error', 'lastSuccessAt': prior.get('lastSuccessAt')})
            print(f"RSS failed: {feed['topic']} ({type(error).__name__})", file=sys.stderr)
    # With zero successes news.json remains byte-for-byte unchanged.
    if success:
        combined = merge_articles([a for entry in cache.values() for a in entry.get('articles', [])])
        atomic_json(data / 'news.json', {'updatedAt': now, 'articles': combined})
    atomic_json(cache_path, cache)
    atomic_json(data / 'status.json', {'attemptedAt': now, 'feeds': results})
    return results

if __name__ == '__main__':
    collect()
