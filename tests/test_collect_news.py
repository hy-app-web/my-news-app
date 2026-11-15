import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collect_news as c

# Synthetic fixtures are used only in tests, never in the delivered news JSON.
RSS = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>Test &amp; example</title><link>https://example.com/a?utm_source=x&amp;page=2#top</link><dc:date>2026-01-01T09:00:00+09:00</dc:date><description>DO NOT COPY</description></item></rdf:RDF>'''

class CollectorTests(unittest.TestCase):
    def test_rdf_minimal_fields(self):
        item = c.parse_feed(RSS, 'AI')[0]
        self.assertEqual(item['url'], 'https://example.com/a?page=2')
        self.assertEqual(item['publishedAt'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(item['source'], 'example.com')
        self.assertEqual(set(item), {'id','title','url','source','publishedAt','topics'})
        self.assertNotIn('DO NOT COPY', json.dumps(item))

    def test_rss2_and_atom(self):
        for xml in [b'<rss><channel><item><title>T</title><link>https://example.com/</link><pubDate>Thu, 01 Jan 2026 09:00:00 +0900</pubDate></item></channel></rss>', b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link href="https://example.com/"/><published>2026-01-01T00:00:00Z</published></entry></feed>']:
            self.assertEqual(len(c.parse_feed(xml, 'AI')), 1)

    def test_dedup_merges_topics_and_sorts(self):
        a = c.parse_feed(RSS, 'AI')[0]
        b = {**a, 'topics': ['ビジネス']}
        older = {**a, 'id': 'older', 'url': 'https://example.com/old', 'publishedAt': '2025-01-01T00:00:00+00:00'}
        merged = c.merge_articles([older, a, b])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['topics'], ['AI', 'ビジネス'])

    def test_rejects_unsafe_and_invalid_data(self):
        for xml in [b'<!DOCTYPE rss [<!ENTITY e "oops">]><rss/>', b'<html/>', b'<rss><item><title>T</title><link>javascript:alert(1)</link></item></rss>', b'x'*(c.MAX_BYTES+1)]:
            with self.assertRaises(ValueError): c.parse_feed(xml, 'AI')
        for url in ['javascript:alert(1)', 'https://user:pass@example.com', '//example.com']:
            self.assertIsNone(c.canonical_url(url))
        self.assertIsNone(c.parse_date('bad'))
        self.assertIsNone(c.parse_date('2026-01-01'))
        self.assertIsNone(c.parse_date('2999-01-01T00:00:00Z'))

    def test_empty_valid_feed_is_success(self):
        self.assertEqual(c.parse_feed(b'<rss><channel/></rss>', 'AI'), [])

    def test_failure_preserves_previous_json_and_partial_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            feeds = c.FEEDS[:2]
            def ok(feed): return c.parse_feed(RSS, feed['topic'])
            with patch.object(c, 'FEEDS', feeds):
                c.collect(root, ok, '2026-01-02T00:00:00+00:00')
                previous = (root/'docs/data/news.json').read_bytes()
                def fail(feed): raise TimeoutError()
                c.collect(root, fail, '2026-01-03T00:00:00+00:00')
                self.assertEqual((root/'docs/data/news.json').read_bytes(), previous)
                c.collect(root, fail, '2026-01-03T00:10:00+00:00')
                self.assertEqual(json.loads((root/'docs/data/status.json').read_text())['feeds'][0]['state'], 'error')
                def partial(feed):
                    if feed['id'] == feeds[0]['id']: return []
                    raise TimeoutError()
                c.collect(root, partial, '2026-01-04T00:00:00+00:00')
                saved = json.loads((root/'docs/data/news.json').read_text())
                self.assertEqual(saved['articles'][0]['topics'], ['テクノロジー'])
                self.assertEqual(json.loads((root/'docs/data/status.json').read_text())['feeds'][1]['state'], 'error')

    def test_throttle_repeated_manual_runs(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(c, 'FEEDS', c.FEEDS[:1]):
            calls=[]
            def fetch(feed): calls.append(feed); return []
            root=Path(temp)
            c.collect(root, fetch, '2026-01-02T00:00:00+00:00')
            c.collect(root, fetch, '2026-01-02T00:10:00+00:00')
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads((root/'docs/data/status.json').read_text())['feeds'][0]['state'], 'cached')

    def test_first_failure_does_not_invent_news(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(c, 'FEEDS', c.FEEDS[:1]):
            root=Path(temp)
            def fail(feed): raise TimeoutError()
            c.collect(root, fail, '2026-01-02T00:00:00+00:00')
            self.assertFalse((root/'docs/data/news.json').exists())
            self.assertTrue((root/'docs/data/status.json').exists())

class SiteTests(unittest.TestCase):
    def test_manifest_and_assets(self):
        root=Path(__file__).resolve().parents[1]/'docs'
        manifest=json.loads((root/'manifest.webmanifest').read_text())
        self.assertEqual(manifest['start_url'], '/my-news-app/')
        self.assertEqual(manifest['scope'], '/my-news-app/')
        for icon in manifest['icons']:
            self.assertTrue((root/icon['src']).exists())
        self.assertTrue((root/'assets/apple-touch-icon.png').read_bytes().startswith(b'\x89PNG'))

if __name__ == '__main__': unittest.main()
