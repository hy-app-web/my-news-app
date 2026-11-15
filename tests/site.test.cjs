const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('docs/index.html', 'utf8');
const script = fs.readFileSync('docs/assets/app.js', 'utf8');
// All fake articles stay in test fixtures; no generated test data is written to docs.
function article(title, url, topics, date) {
  return { id: url, title, url, topics, source: 'example.com', publishedAt: date };
}
const fixtures = [
  article('<img src=x onerror=alert(1)> Test AI', 'https://example.com/a', ['AI'], '2026-01-02T01:00:00Z'),
  article('Test meal', 'https://example.com/b', ['グルメ'], '2026-01-01T01:00:00Z'),
  article('Duplicate', 'https://example.com/a', ['ビジネス'], '2026-01-02T01:00:00Z')
];
async function open({data = {updatedAt: '2026-01-02T01:00:00Z', articles: fixtures}, status = {feeds: []}, saved, storageBlocked = false, failure = false} = {}) {
  const dom = new JSDOM(html, {url:'https://hy-app-web.github.io/my-news-app/', runScripts:'outside-only'});
  const {window} = dom;
  const requests = [];
  if (saved !== undefined) window.localStorage.setItem('my-news-app:topics:v1', JSON.stringify(saved));
  if (storageBlocked) Object.defineProperty(window, 'localStorage', {get(){throw new Error('blocked');}});
  window.fetch = async url => {
    requests.push(url.href);
    if (failure) throw new Error('offline');
    return {ok:true, json:async()=>url.pathname.endsWith('status.json') ? status : data};
  };
  window.eval(script);
  await new Promise(resolve=>setImmediate(resolve));
  return {window, document:window.document, requests, close:()=>window.close()};
}
function button(doc, name){return [...doc.querySelectorAll('button')].find(b=>b.textContent===name);}

test('subpath, descending order, dedup, merged topics, safe text, external links', async()=>{
  const app=await open(); const d=app.document;
  assert.equal(app.requests[0], 'https://hy-app-web.github.io/my-news-app/data/news.json');
  assert.equal(d.querySelectorAll('.article').length,2);
  assert.match(d.querySelector('.article h3').textContent,/Test AI/);
  assert.equal(d.querySelector('.article img'),null);
  assert.deepEqual([...d.querySelectorAll('.article:first-child .tag')].map(t=>t.textContent),['AI','ビジネス']);
  assert.equal(d.querySelector('.article-link').target,'_blank');
  assert.match(d.querySelector('.article-link').rel,/noopener/);
  assert.match(d.querySelector('time').textContent,/10:00/);
  app.close();
});

test('multiple topics use OR, selection persists, reset and no-match guidance',async()=>{
  const app=await open(); const d=app.document;
  button(d,'AI').click(); assert.equal(d.querySelectorAll('.article').length,1);
  button(d,'グルメ').click(); assert.equal(d.querySelectorAll('.article').length,2);
  assert.equal(app.window.localStorage.getItem('my-news-app:topics:v1'),'["AI","グルメ"]');
  const restored=await open({saved:JSON.parse(app.window.localStorage.getItem('my-news-app:topics:v1'))});
  assert.equal(button(restored.document,'AI').getAttribute('aria-pressed'),'true');
  assert.equal(button(restored.document,'グルメ').getAttribute('aria-pressed'),'true');
  restored.close();
  button(d,'すべて表示').click(); assert.equal(button(d,'AI').getAttribute('aria-pressed'),'false');
  button(d,'沖縄').click(); assert.equal(d.querySelectorAll('.article').length,0);
  assert.equal(d.getElementById('empty').hidden,false);
  assert.match(d.getElementById('empty-title').textContent,/このテーマ/);
  app.close();
});

test('blocked or malformed storage does not break filtering',async()=>{
  for(const options of [{storageBlocked:true},{saved:{invalid:true}},{saved:['unknown','AI']}]){
    const app=await open(options); const d=app.document;
    assert.equal(d.querySelectorAll('.chip').length,10);
    button(d,'グルメ').click(); assert.ok(d.querySelectorAll('.article').length>0);
    app.close();
  }
});

test('initial empty data is explicit, no fictional news',async()=>{
  const app=await open({data:{updatedAt:null,articles:[]}}); const d=app.document;
  assert.match(d.getElementById('empty-title').textContent,/まだ届いて/);
  assert.match(d.getElementById('updated').textContent,/まだ収集/);
  assert.equal(d.querySelectorAll('.article').length,0);
  assert.equal(d.querySelector('button[id*=notification]'),null);
  app.close();
});

test('network failure shows retry and can recover',async()=>{
  const app=await open({failure:true}); const d=app.document;
  assert.equal(d.getElementById('retry').hidden,false);
  assert.match(d.getElementById('empty-title').textContent,/読み込めません/);
  app.window.fetch=async()=>({ok:true,json:async()=>({updatedAt:null,articles:[],feeds:[]})});
  d.getElementById('retry').click(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.getElementById('retry').hidden,true);
  assert.match(d.getElementById('empty-title').textContent,/まだ届いて/);
  app.close();
});

test('RSS failures and old data are visible',async()=>{
  const app=await open({status:{feeds:[{topic:'沖縄',state:'error'}]}}); const d=app.document;
  assert.equal(d.getElementById('notice').hidden,false);
  assert.match(d.getElementById('notice').textContent,/沖縄/);
  assert.match(d.getElementById('notice').textContent,/2日以上/);
  app.close();
});

test('invalid URLs or invalid JSON data fail closed',async()=>{
  for(const data of [{updatedAt:null,articles:[{...fixtures[0],url:'javascript:alert(1)'}]}, {articles:'invalid'}]){
    const app=await open({data});
    assert.equal(app.document.querySelectorAll('.article-link').length,0);
    assert.equal(app.document.getElementById('retry').hidden,false);
    app.close();
  }
});
