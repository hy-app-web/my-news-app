const TOPICS = ['AI', 'テクノロジー', 'ビジネス', '経済', 'バズ', 'グルメ', 'イベント', '東京', '沖縄', 'ハンドメイド'];
const KEY = 'my-news-app:topics:v1';
const $ = id => document.getElementById(id);
let selected = [], articles = [], digest = null;
try {
  const saved = JSON.parse(localStorage.getItem(KEY) || '[]');
  if (Array.isArray(saved)) selected = [...new Set(saved.filter(t => TOPICS.includes(t)))];
} catch { $('save-note').textContent = 'このブラウザでは選択を保存できません。この画面では絞り込めます。'; }
const dateFormat = new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
function formatDate(value) { return dateFormat.format(new Date(value)); }
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(selected)); }
  catch { $('save-note').textContent = '選択を保存できませんでした。この画面では絞り込めます。'; }
}
function renderTopics() {
  $('topics').replaceChildren(...TOPICS.map(topic => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'chip'; button.textContent = topic;
    button.setAttribute('aria-pressed', String(selected.includes(topic)));
    button.addEventListener('click', () => {
      selected = selected.includes(topic) ? selected.filter(t => t !== topic) : [...selected, topic];
      save();
      for (const chip of $('topics').children) chip.setAttribute('aria-pressed', String(selected.includes(chip.textContent)));
      renderArticles();
    });
    return button;
  }));
}
function showEmpty(title, message, retry = false) {
  $('empty').hidden = false; $('empty-title').textContent = title; $('empty-message').textContent = message; $('retry').hidden = !retry;
}
function renderDigest() {
  const container = $('digest-items'), status = $('digest-status');
  container.replaceChildren();
  const editions = Array.isArray(digest?.editions) ? digest.editions.filter(e =>
    e && typeof e.editionId === 'string' && Number.isFinite(Date.parse(e.generatedAt)) &&
    Array.isArray(e.items) && e.items.length <= 20) : [];
  editions.sort((a, b) => Date.parse(b.generatedAt) - Date.parse(a.generatedAt));
  const edition = editions[0];
  if (!edition) { status.textContent = '要約は準備中です。本文や説明文を確認できた記事だけを掲載します。'; return; }
  const known = new Map(articles.map(a => [a.id, a]));
  const items = edition.items.filter(s => s && typeof s.summary === 'string' &&
    s.summary.trim() && s.summary.length <= 600 && ['本文', '説明文'].includes(s.basis) &&
    known.has(s.id) && s.sourceUrl === known.get(s.id).url);
  const visible = items.filter(s => !selected.length || known.get(s.id).topics.some(t => selected.includes(t)));
  status.textContent = `要約更新 ${formatDate(edition.generatedAt)}（日本時間）・${items.length}件。AIによる要約です。詳しくは元記事をご確認ください。`;
  if (Number.isInteger(edition.skippedCount) && edition.skippedCount > 0)
    status.textContent += ` 本文等を確認できなかった${edition.skippedCount}件は要約していません。`;
  if (Date.now() - Date.parse(edition.generatedAt) > 24 * 60 * 60 * 1000)
    status.textContent += ' 前回の要約を表示しています。更新が遅れている可能性があります。';
  if (!visible.length) {
    const message = document.createElement('p'); message.className = 'date-note';
    message.textContent = items.length ? '選択したテーマの要約は今回ありません。ほかのテーマもお試しください。' : '今回は内容を確認できた記事がなく、要約を掲載していません。下のニュース一覧は引き続き読めます。';
    container.append(message); return;
  }
  for (const summary of visible) {
    const article = known.get(summary.id);
    const card = document.createElement('article'); card.className = 'digest-card';
    const heading = document.createElement('h3'); heading.textContent = article.title;
    const text = document.createElement('p'); text.className = 'digest-summary'; text.textContent = summary.summary;
    const basis = document.createElement('p'); basis.className = 'date-note';
    basis.textContent = `要約の根拠：${summary.basis} ・ ${article.source}`;
    const link = document.createElement('a'); link.textContent = '元の記事を読む ↗';
    link.href = article.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    card.append(heading, text, basis, link); container.append(card);
  }
}
function renderArticles() {
  renderDigest();
  const visible = articles.filter(a => !selected.length || a.topics.some(t => selected.includes(t)));
  $('count').textContent = `${visible.length}件`;
  $('articles').replaceChildren(...visible.map(article => {
    const card = document.createElement('article'); card.className = 'article';
    const tags = document.createElement('div'); tags.className = 'article-tags';
    for (const topic of article.topics) { const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = topic; tags.append(tag); }
    const link = document.createElement('a'); link.className = 'article-link'; link.href = article.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    const heading = document.createElement('h3'); heading.textContent = article.title;
    const arrow = document.createElement('span'); arrow.className = 'arrow'; arrow.textContent = '↗'; arrow.setAttribute('aria-label', '元記事を新しいタブで開く'); heading.append(arrow); link.append(heading);
    const meta = document.createElement('div'); meta.className = 'meta';
    const source = document.createElement('span'); source.className = 'source'; source.textContent = article.source;
    const time = document.createElement('time'); time.dateTime = article.publishedAt; time.textContent = `${formatDate(article.publishedAt)}（日本時間）`;
    meta.append(source, time); card.append(tags, link, meta); return card;
  }));
  $('empty').hidden = visible.length > 0;
  if (!visible.length) showEmpty(articles.length ? 'このテーマの記事はまだありません' : 'ニュースはまだ届いていません', articles.length ? 'ほかのテーマを選ぶか、「すべて表示」をお試しください。' : '初回の収集待ち、または取得できる記事がありません。時間をおいてもう一度ご覧ください。');
}
function validArticle(a) {
  try {
    return a && typeof a.title === 'string' && a.title.trim() && typeof a.source === 'string' && a.source && /^https?:$/.test(new URL(a.url).protocol) && Number.isFinite(Date.parse(a.publishedAt)) && Array.isArray(a.topics) && a.topics.every(t => TOPICS.includes(t));
  } catch { return false; }
}
async function json(path) {
  const response = await fetch(new URL(path, document.baseURI), { cache: 'no-store' });
  if (!response.ok) throw new Error('fetch failed');
  return response.json();
}
async function load() {
  $('loading').hidden = false; $('empty').hidden = true; $('notice').hidden = true;
  try {
    const [data, status, summaries] = await Promise.all([json('./data/news.json'), json('./data/status.json').catch(() => null), json('./data/summaries.json').catch(() => null)]);
    digest = summaries;
    if (!data || !Array.isArray(data.articles) || data.articles.some(a => !validArticle(a)) || (data.updatedAt !== null && !Number.isFinite(Date.parse(data.updatedAt)))) throw new Error('invalid data');
    const unique = new Map();
    for (const a of data.articles) { const prior = unique.get(a.url); if (prior) prior.topics = [...new Set([...prior.topics, ...a.topics])]; else unique.set(a.url, { ...a, topics: [...a.topics] }); }
    articles = [...unique.values()].sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));
    $('updated').textContent = data.updatedAt ? `最終更新 ${formatDate(data.updatedAt)}（日本時間）` : '最終更新：まだ収集されていません';
    const errors = status?.feeds?.filter(f => f.state === 'error') || [];
    let notice = '';
    if (errors.length) notice = `一部のテーマを取得できませんでした（${errors.map(f => f.topic).join('、')}）。取得済みのニュースがある場合は前回のデータを表示しています。`;
    else if (!status) notice = '収集状況を確認できません。ニュースの最終更新日時をご確認ください。';
    if (data.updatedAt && Date.now() - Date.parse(data.updatedAt) > 48 * 60 * 60 * 1000) notice += ' 最終更新から2日以上経過しています。収集が遅れている可能性があります。';
    if (notice) { $('notice').textContent = notice; $('notice').hidden = false; }
    renderArticles();
  } catch {
    $('digest-items').replaceChildren(); $('digest-status').textContent = 'ニュースを読み込めないため、要約も確認できません。';
    $('updated').textContent = '最終更新：確認できません'; $('count').textContent = ''; $('articles').replaceChildren();
    showEmpty('ニュースを読み込めませんでした', '通信状態を確認して、もう一度お試しください。', true);
  } finally { $('loading').hidden = true; }
}
$('reset').addEventListener('click', () => { selected = []; save(); renderTopics(); renderArticles(); });
$('retry').addEventListener('click', load);
renderTopics(); load();
