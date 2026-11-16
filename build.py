#!/usr/bin/env python3
"""Builds a one-page tech & finance morning feed from RSS feeds plus a price strip.
No third-party packages needed. Run: python build.py  ->  writes site/index.html
"""
import html, json, os, re, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# ----------------------------- SETTINGS (edit me) -----------------------------
TITLE = "Morning Feed"
TZ = ZoneInfo("Europe/London")
MAX_AGE_HOURS = 48       # ignore stories older than this
MAX_PER_SOURCE = 6       # stops one source dominating a section
MAX_PER_SECTION = 30
BLURB_CHARS = 220

# (name, category "tech" or "fin", feed URL). Add, remove or swap freely.
SOURCES = [
    ("The Verge",        "tech", "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch",       "tech", "https://techcrunch.com/feed/"),
    ("Ars Technica",     "tech", "https://feeds.arstechnica.com/arstechnica/index"),
    ("BBC Technology",   "tech", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Silicon UK",       "tech", "https://www.silicon.co.uk/feed"),
    ("Hacker News",      "tech", "https://hnrss.org/frontpage?points=300"),
    ("BBC Business",     "fin",  "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNBC",             "fin",  "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Guardian Business","fin",  "https://www.theguardian.com/uk/business/rss"),
    ("MarketWatch",      "fin",  "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance",    "fin",  "https://finance.yahoo.com/news/rss"),
    ("Bloomberg",        "fin",  "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg Tech",   "tech", "https://feeds.bloomberg.com/technology/news.rss"),
    ("Financial Times",  "fin",  "https://www.ft.com/?format=rss"),
    ("Business Insider", "fin",  "https://www.businessinsider.com/rss"),
]

# (label, Yahoo Finance symbol, prefix, decimals)
TILES = [
    ("Dow Jones",   "^DJI",     "",  2),
    ("S&P 500",     "^GSPC",    "",  2),
    ("Nasdaq",      "^IXIC",    "",  2),
    ("FTSE 100",    "^FTSE",    "",  0),
    ("Gold",        "GC=F",     "$", 0),
    ("Brent crude", "BZ=F",     "$", 1),
    ("Bitcoin",     "BTC-USD",  "$", 0),
    ("GBP/USD",     "GBPUSD=X", "",  3),
]
# ------------------------------------------------------------------------------

UA = {"User-Agent": "Mozilla/5.0 (compatible; MorningFeedBot/1.0)"}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def local(tag):
    return tag.split("}")[-1]


def clean(text, limit=BLURB_CHARS):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + " […]"
    return text


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def parse_feed(data, source, cat):
    root = ET.fromstring(data)
    items = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        f = {"title": "", "link": "", "desc": "", "date": None}
        for ch in el:
            n = local(ch.tag)
            if n == "title":
                f["title"] = (ch.text or "").strip()
            elif n == "link":
                href = ch.get("href")
                if href:
                    if ch.get("rel", "alternate") == "alternate" or not f["link"]:
                        f["link"] = href
                elif ch.text and ch.text.strip():
                    f["link"] = ch.text.strip()
            elif n in ("description", "summary") and not f["desc"]:
                f["desc"] = ch.text or ""
            elif n == "encoded" and not f["desc"]:
                f["desc"] = ch.text or ""
            elif n in ("pubDate", "published", "updated", "date") and not f["date"]:
                f["date"] = parse_date(ch.text)
        if f["title"] and f["link"] and f["date"]:
            items.append({"source": source, "cat": cat, "title": clean(f["title"], 300),
                          "link": f["link"], "blurb": clean(f["desc"]), "date": f["date"]})
    return items


def words(t):
    return set(re.findall(r"[a-z0-9]+", t.lower()))


def is_dup(item, kept):
    w = words(item["title"])
    for k in kept:
        kw = words(k["title"])
        if w and kw and len(w & kw) / len(w | kw) >= 0.7:
            return True
    return False


def collect():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    all_items, failed = [], []
    for name, cat, url in SOURCES:
        try:
            got = parse_feed(fetch(url), name, cat)
            got = [i for i in got if cutoff <= i["date"] <= now + timedelta(hours=1)]
            got.sort(key=lambda i: i["date"], reverse=True)
            all_items += got[:MAX_PER_SOURCE]
            print(f"ok   {name}: {len(got)} recent", file=sys.stderr)
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {e}", file=sys.stderr)
    out = {"tech": [], "fin": []}
    for i in sorted(all_items, key=lambda i: i["date"], reverse=True):
        sec = out[i["cat"]]
        if len(sec) < MAX_PER_SECTION and not is_dup(i, sec):
            sec.append(i)
    return out, failed


def prices():
    tiles = []
    for label, sym, pre, dec in TILES:
        v = chg = None
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=5d&interval=1d"
            res = json.loads(fetch(url))["chart"]["result"][0]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            v = res["meta"].get("regularMarketPrice") or closes[-1]
            prev = closes[-2] if len(closes) >= 2 else res["meta"].get("chartPreviousClose")
            chg = (v - prev) / prev * 100 if prev else None
        except Exception as e:
            print(f"FAIL price {label}: {e}", file=sys.stderr)
        tiles.append((label, v, chg, pre, dec))
    return tiles


def tile_html(label, v, chg, pre, dec):
    if v is None:
        val, c, cls = "n/a", "", ""
    else:
        val = f"{pre}{v:,.{dec}f}"
        if chg is None:
            c, cls = "", ""
        else:
            sign = "+" if chg >= 0 else "\u2212"
            c = f"{sign}{abs(chg):.1f}%"
            cls = "up" if chg >= 0 else "down"
    return (f'<div class="tile"><b>{html.escape(label)}</b><div class="v">{html.escape(val)}</div>'
            f'<div class="c {cls}">{c}</div></div>')


def when(d, now):
    d = d.astimezone(TZ)
    if d.date() == now.date():
        return d.strftime("%H:%M")
    if d.date() == (now - timedelta(days=1)).date():
        return "Yesterday " + d.strftime("%H:%M")
    return d.strftime("%a %H:%M")


def item_html(i, now):
    pill = "Tech" if i["cat"] == "tech" else "Finance"
    blurb = f'<span class="d">{html.escape(i["blurb"])}</span>' if i["blurb"] else ""
    return (f'<div class="item"><a href="{html.escape(i["link"], quote=True)}" rel="noopener">'
            f'<div class="meta"><span class="pill {i["cat"]}">{pill}</span><span>{html.escape(i["source"])}</span>'
            f'<span>· {when(i["date"], now)}</span></div>'
            f'<span class="t">{html.escape(i["title"])}</span>{blurb}</a></div>')


CSS = """:root{--bg:#faf8f4;--card:#fff;--ink:#1b1b1f;--mute:#6b6b73;--line:#e6e2d9;--tech:#7a3cff;--fin:#0a8a5a;--up:#0a8a5a;--down:#c62d2d;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root{--bg:#111114;--card:#1b1b20;--ink:#f1f0ec;--mute:#9a9aa3;--line:#2c2c33;--tech:#b497ff;--fin:#4fd1a0;--up:#4fd1a0;--down:#ff7b7b;color-scheme:dark}}
html{scroll-padding-top:env(safe-area-inset-top,0px)}*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:20px 16px 48px}
.date{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
h1{font-size:30px;line-height:1.15;margin:4px 0 6px;font-family:Georgia,"Times New Roman",serif}
.sub{color:var(--mute);font-size:14px;margin:0 0 16px}
h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;margin:22px 0 10px;color:var(--mute)}
.mk{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.tile b{display:block;font-size:12px;color:var(--mute);font-weight:600}
.tile .v{font-size:18px;font-weight:700;margin:2px 0;font-variant-numeric:tabular-nums}
.tile .c{font-size:13px;font-weight:600;min-height:1.2em}.up{color:var(--up)}.down{color:var(--down)}
.tabs{display:flex;gap:8px;position:sticky;top:env(safe-area-inset-top,0px);background:var(--bg);padding:8px 0;z-index:2;margin-top:12px}
.tabs button{font:inherit;font-size:14px;font-weight:600;padding:6px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
.tabs button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.item{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:8px}
.item a{color:inherit;text-decoration:none;display:block}.item a:hover .t{text-decoration:underline}
.meta{font-size:12px;color:var(--mute);display:flex;gap:6px;align-items:center;margin-bottom:3px;flex-wrap:wrap}
.pill{font-weight:700;letter-spacing:.05em;text-transform:uppercase;font-size:11px}.pill.tech{color:var(--tech)}.pill.fin{color:var(--fin)}
.t{font-weight:650;display:block}.d{color:var(--mute);font-size:14.5px;display:block;margin-top:2px}
.note{font-size:12px;color:var(--mute);margin-top:28px;border-top:1px solid var(--line);padding-top:14px}
[hidden]{display:none!important}
@media (max-width:560px){.mk{grid-template-columns:repeat(2,1fr)}h1{font-size:26px}}"""

JS = """(function(){var b=document.querySelectorAll('.tabs button'),s=document.querySelectorAll('section[data-s]');
b.forEach(function(x){x.addEventListener('click',function(){var f=x.getAttribute('data-f');
b.forEach(function(y){y.setAttribute('aria-pressed',y===x?'true':'false')});
s.forEach(function(z){z.hidden=!(f==='all'||z.getAttribute('data-s')===f)})})})})();"""


def render(items, tiles, failed):
    now = datetime.now(TZ)
    sec = lambda key, name: (f'<section data-s="{key}"><h2>{name}</h2>' +
                             "".join(item_html(i, now) for i in items[key]) +
                             ("" if items[key] else '<p class="sub">No recent stories.</p>') + "</section>")
    foot = "Prices from Yahoo Finance (unofficial, may lag). Headlines link to the original publishers."
    if failed:
        foot += " Unavailable this run: " + ", ".join(failed) + "."
    return f"""<!DOCTYPE html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(TITLE)}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%93%A1%3C/text%3E%3C/svg%3E">
<style>{CSS}</style></head><body><div class="wrap">
<div class="date">{now.strftime('%A %-d %B %Y')} · Updated {now.strftime('%H:%M')}</div>
<h1>{html.escape(TITLE)}</h1><p class="sub">Tech &amp; finance headlines from your chosen sources.</p>
<h2 style="margin-top:6px">Markets</h2><div class="mk">{''.join(tile_html(*t) for t in tiles)}</div>
<div class="tabs" role="group" aria-label="Filter"><button data-f="all" aria-pressed="true">All</button><button data-f="tech" aria-pressed="false">Tech</button><button data-f="fin" aria-pressed="false">Finance</button></div>
{sec('fin', 'Finance')}{sec('tech', 'Tech')}
<p class="note">{html.escape(foot)}</p></div><script>{JS}</script></body></html>"""


def main():
    items, failed = collect()
    tiles = prices()
    os.makedirs("site", exist_ok=True)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(render(items, tiles, failed))
    n = sum(len(v) for v in items.values())
    print(f"Wrote site/index.html with {n} stories", file=sys.stderr)
    if n == 0:
        sys.exit("No stories found, so not publishing an empty page.")


if __name__ == "__main__":
    main()
