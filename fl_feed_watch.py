#!/usr/bin/env python3
"""fl_feed_watch — монитор новых заказов FL.ru → уведомления в Telegram.

Периодически опрашивает публичную ленту заказов FL.ru, фильтрует по
ключевым словам и шлёт уведомление ботом в указанный чат/канал.
Дедупликация — sqlite (seen.db рядом со скриптом).

Конфиг — переменные окружения или config.json рядом:
  TG_TOKEN   токен бота (BotFather)
  CHAT_ID    id чата/канала/группы куда слать (бот должен иметь право писать)
  KEYWORDS   regex-фильтр по заголовку+описанию (по умолчанию '.*')
  FEED_URLS  список URL лент через запятую (по умолчанию главная лента проектов)
  INTERVAL   пауза между опросами, сек (по умолчанию 300)
  ONCE=1     один прогон и выход (для cron)
"""
import html as htmllib
import json, os, re, sqlite3, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

DEFAULT_FEEDS = [
    'https://www.fl.ru/projects/?kind=5',   # все проекты
    'https://www.fl.ru/projects/?kind=2',   # с бюджетом
]

CARD_RE = re.compile(
    r'id="prj_name_(\d+)"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.{0,6000}?)(?=id="prj_name_|<footer|b-pager)',
    re.S)
PRICE_RE = re.compile(r"b-post__grid_price.{0,400}?<span[^>]*>(.*?)</span>", re.S)
TXT_RE = re.compile(r'b-post__txt[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r'<[^>]+>')


def load_cfg():
    cfg = {}
    p = os.path.join(HERE, 'config.json')
    if os.path.exists(p):
        cfg = json.load(open(p, encoding='utf-8'))
    env = {k: os.environ.get(k) for k in
           ('TG_TOKEN', 'CHAT_ID', 'KEYWORDS', 'FEED_URLS', 'INTERVAL') if os.environ.get(k)}
    cfg.update(env)
    cfg.setdefault('KEYWORDS', '.*')
    cfg.setdefault('INTERVAL', 300)
    cfg.setdefault('FEED_URLS', ','.join(DEFAULT_FEEDS))
    cfg['feeds'] = [u.strip() for u in cfg['FEED_URLS'].split(',') if u.strip()]
    cfg['kw'] = re.compile(cfg['KEYWORDS'], re.I)
    if not cfg.get('TG_TOKEN') or not cfg.get('CHAT_ID'):
        sys.exit('нужны TG_TOKEN и CHAT_ID (env или config.json)')
    return cfg


def clean(html):
    txt = TAG_RE.sub(' ', html)
    return re.sub(r'\s+', ' ', htmllib.unescape(txt)).strip()


def fetch_orders(url):
    """Разобрать страницу ленты → [{id,title,price,desc,url}]"""
    h = requests.get(url, headers=UA, timeout=40).text
    out = []
    for m in CARD_RE.finditer(h):
        pid, href, title, tail = m.groups()
        pm = PRICE_RE.search(tail)
        tm = TXT_RE.search(tail)
        out.append({
            'id': pid,
            'title': clean(title),
            'price': clean(pm.group(1)) if pm else '',
            'desc': clean(tm.group(1))[:220] if tm else '',
            'url': 'https://www.fl.ru' + href if href.startswith('/') else href,
        })
    return out


def tg_send(tok, chat, text):
    for attempt in range(3):
        try:
            r = requests.post(
                f'https://api.telegram.org/bot{tok}/sendMessage',
                json={'chat_id': chat, 'text': text[:3900],
                      'disable_web_page_preview': True},
                timeout=30)
            if r.status_code == 200 and r.json().get('ok'):
                return True
            if r.status_code == 429:  # flood limit
                time.sleep(int(r.json().get('parameters', {}).get('retry_after', 5)) + 1)
                continue
            print('tg:', r.text[:160])
            return False
        except Exception as e:
            print('tg err:', e)
            time.sleep(3)
    return False


def run(cfg):
    db = sqlite3.connect(os.path.join(HERE, 'seen.db'))
    db.execute('CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY)')
    new = 0
    for feed in cfg['feeds']:
        try:
            orders = fetch_orders(feed)
        except Exception as e:
            print(f'feed {feed}: {e}')
            continue
        for o in orders:
            if db.execute('SELECT 1 FROM seen WHERE id=?', (o['id'],)).fetchone():
                continue
            db.execute('INSERT INTO seen VALUES(?)', (o['id'],))
            if not cfg['kw'].search(o['title'] + ' ' + o['desc']):
                continue
            price = f" · {o['price']}" if o['price'] else ''
            ok = tg_send(cfg['TG_TOKEN'], cfg['CHAT_ID'],
                         f"Новый заказ FL{price}\n\n{o['title']}\n{o['desc']}\n\n{o['url']}")
            print(('sent  ' if ok else 'FAIL  ') + o['id'] + ' ' + o['title'][:60])
            new += 1
            time.sleep(1)
    db.commit()
    db.close()
    print(f'done, новых сигналов: {new}')


def main():
    cfg = load_cfg()
    if os.environ.get('ONCE'):
        run(cfg)
        return
    print(f'fl_feed_watch: опрос каждые {cfg["INTERVAL"]}с, фильтр /{cfg["KEYWORDS"]}/i')
    while True:
        run(cfg)
        time.sleep(int(cfg['INTERVAL']))


if __name__ == '__main__':
    main()
