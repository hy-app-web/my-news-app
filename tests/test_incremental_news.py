"""Incremental archive and workflow regression tests; synthetic data is temp-only."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collect_news as c

ROOT = Path(__file__).resolve().parents[1]
NOW = '2026-11-16T10:00:00+00:00'

def item(key, topics=None):
    url = f'https://example.com/{key}'
    return {'id': key, 'title': f'Test {key}', 'url': url, 'source': 'example.com',
            'publishedAt': '2026-01-01T00:00:00+00:00', 'topics': topics or ['AI']}

def seed(root, articles):
    c.atomic_json(root/'docs/data/news.json', {'updatedAt': '2026-01-01T00:00:00+00:00', 'articles': articles})

def news(root):
    return json.loads((root/'docs/data/news.json').read_text())

def many(feed):
    return [item(f"{feed['id']}-{i}", [feed['topic']]) for i in range(50)]

class IncrementalTests(unittest.TestCase):
    def test_twenty_cap_and_ten_topic_balance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c.collect(root, many, NOW, run_id='100')
            articles = news(root)['articles']
            self.assertEqual(len(articles), 20)
            counts = Counter(t for a in articles for t in a['topics'])
            self.assertEqual(counts, dict.fromkeys(c.TOPICS, 2))
            self.assertEqual(json.loads((root/'docs/data/status.json').read_text())['addedCount'], 20)

    def test_archive_beyond_old_six_hundred_cap_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = [item(f'old-{i}') for i in range(650)]
            existing[0]['publishedAt'] = '2025-01-01T00:00:00+00:00'
            seed(root, existing)
            c.collect(root, many, NOW)
            saved = news(root)['articles']
            self.assertEqual(len(saved), 670)
            self.assertTrue(all(a in saved for a in existing))
            self.assertEqual(saved[-1], existing[0])

    def test_shortage_adds_only_available_and_redistributes_unused_slots(self):
        for count in (0, 3, 9, 20, 35):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                def fetch(feed):
                    return [item(str(i), [feed['topic']]) for i in range(count)] if feed['topic']=='AI' else []
                c.collect(root, fetch, NOW)
                self.assertEqual(len(news(root)['articles']), min(count,20))
        candidates=[item(f'ai-{i}') for i in range(40)] + [item(f't-{i}',[t]) for i,t in enumerate(c.TOPICS[1:])]
        chosen=c.select_new_articles([],candidates)
        self.assertEqual(len(chosen),20)
        self.assertEqual({t for a in chosen for t in a['topics']},set(c.TOPICS))

    def test_same_url_tracking_variants_and_cross_topic_overlap(self):
        existing=item('known')
        candidates=[{**existing, 'url': existing['url']+'?utm_source=test#top'},
                    item('shared',['AI']), item('shared',['テクノロジー']), item('shared',['ビジネス'])]
        chosen=c.select_new_articles([existing],candidates)
        self.assertEqual(len(chosen),1)
        self.assertEqual(chosen[0]['topics'],['AI','テクノロジー','ビジネス'])

    def test_next_run_skips_registered_urls_and_fills_next_twenty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            c.collect(root,many,NOW,run_id='100')
            first=news(root)['articles']
            c.collect(root,many,'2026-11-17T10:00:00+00:00',run_id='101')
            saved=news(root)['articles']
            self.assertEqual(len(saved),40)
            self.assertEqual(len({a['url'] for a in saved}),40)
            self.assertTrue(all(a in saved for a in first))

    def test_same_actions_run_retries_are_idempotent_even_after_later_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            c.collect(root,many,NOW,run_id='100')
            c.collect(root,many,'2026-11-17T10:00:00+00:00',run_id='101')
            before=(root/'docs/data/news.json').read_bytes()
            def no_fetch(feed): self.fail('completed run must not fetch again')
            c.collect(root,no_fetch,'2026-11-18T10:00:00+00:00',run_id='100')
            self.assertEqual((root/'docs/data/news.json').read_bytes(),before)

    def test_total_failure_preserves_bytes_and_can_retry_same_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); seed(root,[item('old')])
            before=(root/'docs/data/news.json').read_bytes()
            def fail(feed): raise TimeoutError()
            c.collect(root,fail,NOW,run_id='100')
            self.assertEqual((root/'docs/data/news.json').read_bytes(),before)
            c.collect(root,many,'2026-11-17T10:00:00+00:00',run_id='100')
            self.assertEqual(len(news(root)['articles']),21)

    def test_partial_failure_keeps_old_articles_and_ignores_failed_cache_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); old=item('old'); seed(root,[old])
            c.atomic_json(root/'collector-cache.json',{'0':{'articles':[item('stale-not-registered')]}})
            def fetch(feed):
                if feed['topic']=='AI': raise TimeoutError()
                return [item('new', [feed['topic']])]
            c.collect(root,fetch,NOW)
            saved=news(root)['articles']
            self.assertEqual(len(saved),2)
            self.assertIn(old,saved)
            self.assertNotIn('stale-not-registered',{a['id'] for a in saved})

    def test_simultaneous_same_checkout_runs_read_under_lock(self):
        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=2) as pool:
            root=Path(tmp)
            entered=threading.Event(); release=threading.Event()
            def fetch(feed):
                entered.set()
                if not release.wait(5): raise RuntimeError('test timed out')
                return many(feed)
            first=pool.submit(c.collect,root,fetch,NOW,'100')
            self.assertTrue(entered.wait(5))
            second=pool.submit(c.collect,root,fetch,NOW,'100')
            release.set()
            first.result(10); second.result(10)
            self.assertEqual(len(news(root)['articles']),20)
            self.assertEqual(news(root)['completedRunIds'],['100'])

class WorkflowTests(unittest.TestCase):
    def test_schedule_and_visible_delay_note(self):
        workflow=(ROOT/'.github/workflows/collect-news.yml').read_text()
        cron=re.search(r"cron: '([^']+)'",workflow).group(1).split()
        self.assertEqual(cron[0],'0')
        self.assertEqual(sorted((int(h)+9)%24 for h in cron[1].split(',')),[12,18])
        self.assertEqual(cron[2:],['*','*','*'])
        self.assertIn('workflow_dispatch:',workflow)
        for path in ['README.md','docs/index.html']:
            text=(ROOT/path).read_text()
            self.assertIn('12時・18時',text)
            self.assertIn('遅延',text)

    def test_serialization_fresh_main_and_direct_pages_call(self):
        workflow=(ROOT/'.github/workflows/collect-news.yml').read_text()
        self.assertIn('group: collect-news',workflow)
        self.assertIn('cancel-in-progress: false',workflow)
        self.assertLess(workflow.index('git fetch origin main'),workflow.index('python scripts/collect_news.py'))
        self.assertIn('git merge --ff-only origin/main',workflow)
        self.assertIn('needs: collect',workflow)
        self.assertIn('uses: ./.github/workflows/pages.yml',workflow)
        pages=(ROOT/'.github/workflows/pages.yml').read_text()
        for required in ['workflow_call:', "vars.PAGES_ENABLED == 'true'", 'ref: main', 'path: docs', 'actions/deploy-pages@v4']:
            self.assertIn(required,pages)
        for permission in ['pages: write','id-token: write']:
            self.assertIn(permission,workflow)
            self.assertIn(permission,pages)

    def test_remote_main_change_rejects_save_without_overwriting_remote_data(self):
        workflow=(ROOT/'.github/workflows/collect-news.yml').read_text()
        block=workflow.split('      - name: Save collected data\n        run: |\n',1)[1].split('\n  publish:',1)[0]
        script=textwrap.dedent(block)
        def git(cwd,*args):
            return subprocess.run(['git',*args],cwd=cwd,check=True,text=True,capture_output=True).stdout
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); origin=root/'origin.git'; writer=root/'writer'; runner=root/'runner'
            git(root,'init','--bare','--initial-branch=main',str(origin))
            git(root,'clone',str(origin),str(writer))
            git(writer,'config','user.name','Test'); git(writer,'config','user.email','test@example.com')
            for path in ['docs/data/news.json','docs/data/status.json','collector-cache.json']:
                p=writer/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('{}\n')
            git(writer,'add','.'); git(writer,'commit','-m','seed'); git(writer,'push','origin','HEAD:main')
            git(root,'clone',str(origin),str(runner))
            (runner/'docs/data/news.json').write_text('{"local":20}\n')
            (writer/'docs/data/news.json').write_text('{"remote":"must remain"}\n')
            git(writer,'add','.'); git(writer,'commit','-m','concurrent data'); git(writer,'push','origin','HEAD:main')
            remote_head=git(writer,'rev-parse','HEAD').strip()
            result=subprocess.run(['bash','-e','-c',script],cwd=runner,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('rejected',result.stderr)
            self.assertEqual(git(root,'--git-dir='+str(origin),'rev-parse','main').strip(),remote_head)
            self.assertIn('must remain',git(root,'--git-dir='+str(origin),'show','main:docs/data/news.json'))

if __name__=='__main__': unittest.main()
