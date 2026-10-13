#!/usr/bin/env python3
"""
US Trends & Pop-Culture Digest Bot
Щоденний кураторський дайджест трендів американського інтернету, мемів, блогерів, музики
та поп-культури. Джерела англомовні (Google Trends geo=US, Google News US, музичні й
культурні медіа), дайджест у Telegram пишеться українською.
"""

import os
import re
import sys
import json
import html
import xml.etree.ElementTree as ET
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse


# ── Налаштування ─────────────────────────────────────────────────────────────

HOURS_BACK = int(os.environ.get("HOURS_BACK", "28"))

# Квоти на групу джерел: стільки записів з кожної групи йде до моделі (разом ~14).
QUOTAS = {
    "trends": 4,    # Google Trends US
    "music": 3,     # Billboard, Pitchfork, Stereogum, нові релізи
    "video": 2,     # музичні відео й трейлери, YouTube-шоу/подкасти
    "social": 3,    # TikTok-тренди, драми інфлюенсерів, меми
    "culture": 2,   # Variety, Vulture, The Verge, Know Your Meme
}

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

SENT_FILE = Path(__file__).with_name("sent.json")
SENT_DAYS = 14

# СТОП-СЛОВА: фільтруємо війну, трагедії, кримінал, катастрофи та політику США,
# щоб дайджест лишався легким і не тригерив безпековий фільтр Google (SAFETY).
# Три списки, регістр не важливий, крім STOP_ABBREVIATIONS.

# 1) Початок слова: (?<!\w)стем — "shooting", "shootings", але не "photoshoot".
STOP_PREFIXES = [
    # насильство, кримінал
    "shooting", "shootout", "gunman", "gunfire", "massacre", "murder", "homicide", "stabb",
    "kidnap", "hostage", "terror", "bomb", "explosion", "missile", "airstrike", "genocide",
    "assault", "rape", "arrest", "indict", "convict", "overdose", "suicide", "victim",
    "casualt", "fatal", "plane crash", "car crash",
    # трагедії й катастрофи
    "tragedy", "tragic", "funeral", "obituar", "passed away", "wildfire", "hurricane",
    "earthquake", "tornado", "flood", "disaster",
    # політика США та геополітика
    "trump", "biden", "kamala", "white house", "congress", "senat", "supreme court",
    "election", "democrat", "republican", "tariff", "shutdown", "impeach", "immigra",
    "deportation", "ukrain", "russia", "putin", "gaza", "israel", "hamas", "iran",
    "north korea",
]

# 2) Короткі слова лише цілком: "war" не повинно ловити "warm", "warner", "Warhol".
STOP_WHOLE_WORDS = [
    "war", "wars", "warfare", "dead", "dies", "died", "death", "deaths",
    "kill", "kills", "killed", "killing",
]

# 3) Абревіатури лише великими літерами і лише цілком ("ICE" ≠ "Ice Spice", "price").
STOP_ABBREVIATIONS = ["RIP", "ICE", "GOP", "DUI", "FBI", "DOJ", "ATF", "IDF", "NATO"]

# Вирази, де стоп-слово не означає біди: вирізаємо їх перед перевіркою.
STOP_EXCEPTIONS = [
    r"star\s+wars", r"clone\s+wars", r"war\s+of\s+the\s+worlds", r"war\s+machine",
    r"war\s+games", r"world\s+war\s+z", r"(?:price|bidding|streaming|console|cola)\s+wars?",
    r"walking\s+dead", r"grateful\s+dead", r"dead\s+by\s+daylight", r"dead\s+space",
    r"dead\s+island", r"dead\s+rising", r"dead\s+cells", r"dead\s+poets\s+society",
    r"death\s+stranding", r"death\s+note", r"death\s+becomes\s+her", r"death\s+on\s+the\s+nile",
    r"kill\s+tony", r"kill\s+bill", r"killing\s+eve", r"the\s+killers",
    r"kill(?:ed|ing|s)?\s+it",
    r"shooting\s+(?:a|the|for|his|her|their|new)\s+(?:movie|film|video|scene|series|show|commercial)",
    r"shooting\s+star", r"top\s+gun",
]

STOP_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(w) for w in STOP_PREFIXES) + ")"
    r"|(?<!\w)(?:" + "|".join(re.escape(w) for w in STOP_WHOLE_WORDS) + r")(?!\w)",
    re.IGNORECASE,
)
STOP_ABBR_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(w) for w in STOP_ABBREVIATIONS) + r")(?!\w)"
)
STOP_EXCEPTIONS_RE = re.compile("|".join(STOP_EXCEPTIONS), re.IGNORECASE)

# БЛОКЛИСТ ДОМЕНІВ: записи з посиланням або джерелом на цих доменах не доходять до моделі.
BLOCKED_TLDS = (".ru", ".su", ".рф", ".xn--p1ai", ".by")
BLOCKED_DOMAINS = [
    "ria.ru", "tass.ru", "rt.com", "lenta.ru", "kp.ru", "mk.ru", "gazeta.ru", "rbc.ru",
    "iz.ru", "vk.com", "ok.ru", "dzen.ru", "mail.ru", "yandex.ru", "sputniknews.com",
]


def gnews(query):
    """Google News RSS (США): лапки й дужки в запиті, лише за останню добу, англ. видача."""
    q = quote_plus(f"{query} when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


# Джерела. group — ключ із QUOTAS; kind "trends" читає Google Trends через xml.etree.
# unverified — URL не вдалося перевірити з середовища розробки (лог першого запуску покаже статус).
FEEDS = [
    # Пошукові тренди Google США разом із заголовками новин
    {"name": "Google Trends (US)", "group": "trends", "kind": "trends",
     "url": "https://trends.google.com/trending/rss?geo=US"},

    # Музика
    {"name": "Billboard", "group": "music",
     "url": "https://www.billboard.com/feed/", "unverified": True},
    {"name": "Pitchfork", "group": "music",
     "url": "https://pitchfork.com/feed/feed-news/rss", "unverified": True},
    {"name": "Stereogum", "group": "music",
     "url": "https://www.stereogum.com/feed/", "unverified": True},
    {"name": "Google News: new music releases", "group": "music",
     "url": gnews('("new single" OR "new album" OR "new song" OR "drops new" OR "surprise album") '
                  '("music" OR "singer" OR "rapper" OR "band")')},

    # Відео
    {"name": "Google News: music videos & trailers", "group": "video",
     "url": gnews('("music video" OR "official trailer" OR "new trailer" OR "teaser trailer" OR "visualizer")')},
    {"name": "Google News: YouTube shows & podcasts", "group": "video",
     "url": gnews('("YouTube" OR "YouTuber") ("new video" OR "podcast" OR "interview" OR "documentary" OR "new series")')},

    # Соцмережі та блогери
    {"name": "Google News: TikTok trends", "group": "social",
     "url": gnews('("TikTok") ("trend" OR "challenge" OR "goes viral" OR "viral video")')},
    {"name": "Google News: influencer drama", "group": "social",
     "url": gnews('("influencer" OR "YouTuber" OR "streamer" OR "content creator") '
                  '("drama" OR "backlash" OR "feud" OR "called out" OR "apologizes")')},
    {"name": "Google News: memes", "group": "social",
     "url": gnews('("meme" OR "memes") ("viral" OR "internet" OR "social media" OR "users")')},

    # Культурні медіа (RSS-адреси не перевірені: типові шляхи)
    {"name": "Variety", "group": "culture",
     "url": "https://variety.com/feed/", "unverified": True},
    {"name": "Vulture", "group": "culture",
     "url": "https://www.vulture.com/rss/index.xml", "unverified": True},
    {"name": "The Verge", "group": "culture",
     "url": "https://www.theverge.com/rss/index.xml", "unverified": True},
    {"name": "Know Your Meme", "group": "culture",
     "url": "https://knowyourmeme.com/newsfeed.rss", "unverified": True},
]

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36 USTrendsBot/1.0"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

UA_MONTHS = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]


def redact(value):
    """Прибирає значення секретів із тексту (помилок, відповідей) перед виводом у лог."""
    text = str(value)
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "***")
    return text


# ── Обробка дат та фільтрація ────────────────────────────────────────────────

def parse_date(entry):
    """Парсить дати форматів RFC 2822 та ISO 8601."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass

    for attr in ("published", "updated"):
        s = getattr(entry, attr, None)
        if not s or not isinstance(s, str):
            continue
        try:
            return parsedate_to_datetime(s).astimezone(timezone.utc)
        except Exception:
            pass
        try:
            cleaned = re.sub(r"\.\d+", "", s)
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except Exception:
            pass

    return None


def clean_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_depressing(text):
    """Відсіює важкі, небезпечні та політичні інфоприводи."""
    text = STOP_EXCEPTIONS_RE.sub(" ", text)
    return bool(STOP_RE.search(text) or STOP_ABBR_RE.search(text))


def _host(url):
    try:
        return (urlparse(url or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def host_blocked(host):
    return bool(host) and (
        host.endswith(BLOCKED_TLDS)
        or any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)
    )


def domain_verdict(link, source_url=""):
    """
    Перевірка доменів запису: "blocked" (домен у блоклисті), "nosource" (посилання Google News
    без домену реального джерела, перевірити неможливо) або None (можна).
    Для Google News перевіряємо домен із <source url>, а не news.google.com.
    """
    link_host, source_host = _host(link), _host(source_url)
    if host_blocked(link_host) or host_blocked(source_host):
        return "blocked"
    if link_host == "news.google.com" and not source_host:
        return "nosource"
    return None


def norm_title(title):
    return re.sub(r"[^\w\s]", "", title.lower())


# ── Історія надісланого (sent.json) ──────────────────────────────────────────

def load_sent():
    """Повертає {ключ запису: дата YYYY-MM-DD} за останні SENT_DAYS днів."""
    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[WARN] Не вдалося прочитати {SENT_FILE.name}: {e}")
        return {}

    oldest = (datetime.now(timezone.utc) - timedelta(days=SENT_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in data.items() if isinstance(v, str) and v >= oldest}


def save_sent(sent, entries):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for e in entries:
        sent[e["key"]] = today
    SENT_FILE.write_text(
        json.dumps(dict(sorted(sent.items())), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"[OK] {SENT_FILE.name}: збережено {len(sent)} записів (+{len(entries)} нових)")


# ── Парсинг фідів ────────────────────────────────────────────────────────────

def parse_rss(content):
    """Звичайний RSS/Atom через feedparser → сирі записи."""
    feed = feedparser.parse(content)
    raw = []
    for entry in feed.entries:
        title = clean_html(getattr(entry, "title", ""))
        summary = clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        link = getattr(entry, "link", "").strip()
        raw.append({
            "title": title,
            "summary": summary[:350],
            "link": link,
            "source_url": (entry.get("source") or {}).get("href", ""),
            "key": link or f"title:{norm_title(title)}",
            "dt": parse_date(entry),
            "min_len": 5,
        })
    return raw


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_traffic(value):
    """'200+' / '2,000+' / '500K+' / '1M+' → число для сортування."""
    m = re.match(r"\s*([\d.,]+)\s*([KkMm])?", value or "")
    if not m:
        return 0
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(num * {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1))


def parse_trends(content, st):
    """
    Google Trends RSS: у кожному <item> запит + ht:approx_traffic + кілька ht:news_item
    (title/url/source). feedparser віддає лише один news_item, тому читаємо XML напряму;
    теги порівнюємо без урахування namespace.
    Новини зі стоп-словами викидаємо; якщо новини були, але всі відсіяні — тренд теж відсіюється.
    Сам запит (title) теж перевіряємо на стоп-слова.
    """
    root = ET.fromstring(content)
    raw = []
    for item in root.iter("item"):
        data = {"news": []}
        for child in item:
            name = _local(child.tag)
            if name == "title":
                data["title"] = clean_html(child.text)
            elif name == "pubDate":
                data["pub"] = (child.text or "").strip()
            elif name == "approx_traffic":
                data["traffic"] = (child.text or "").strip()
            elif name == "news_item":
                news = {_local(n.tag): clean_html(n.text) for n in child}
                if news.get("news_item_title"):
                    data["news"].append(news)

        title = data.get("title", "")
        if not title:
            continue

        if is_depressing(title):
            st["stop"] += 1
            st["parsed"] += 1
            continue

        all_news = data["news"]
        allowed = [n for n in all_news if not domain_verdict(n.get("news_item_url", ""))]
        if all_news and not allowed:
            st["blocked"] += 1
            st["parsed"] += 1
            continue
        news = [n for n in allowed if not is_depressing(n["news_item_title"])]
        if all_news and not news:
            st["stop"] += 1
            st["parsed"] += 1
            continue

        traffic = data.get("traffic", "")
        parts = [f"Searches: {traffic}." if traffic else ""]
        if news:
            parts.append("News headlines: " + "; ".join(
                f"\"{n['news_item_title']}\"" + (f" ({n['news_item_source']})" if n.get("news_item_source") else "")
                for n in news[:3]
            ))
        link = next((n.get("news_item_url", "") for n in news if n.get("news_item_url")), "")

        try:
            dt = parsedate_to_datetime(data["pub"]).astimezone(timezone.utc)
        except Exception:
            dt = None

        raw.append({
            "title": title,
            "summary": " ".join(p for p in parts if p),
            "link": link,
            "key": f"trend:{norm_title(title)}",
            "dt": dt,
            "min_len": 2,
            "traffic": parse_traffic(traffic),
        })

    raw.sort(key=lambda r: r["traffic"], reverse=True)
    return raw


def filter_items(raw, source, group, ctx, st):
    """Застосовує фільтри: дата, довжина, блоклист доменів, стоп-слова, уже надіслане, дублі."""
    kept = []
    for it in raw:
        st["parsed"] += 1
        if it["dt"] and it["dt"] < ctx["cutoff"]:
            st["old"] += 1
            continue
        if len(it["title"]) < it["min_len"]:
            continue
        verdict = domain_verdict(it["link"], it.get("source_url", ""))
        if verdict:
            st["blocked" if verdict == "blocked" else "nosource"] += 1
            continue
        if is_depressing(it["title"]) or is_depressing(it["summary"]):
            st["stop"] += 1
            continue
        if it["key"] in ctx["sent"]:
            st["sent"] += 1
            continue
        nt = norm_title(it["title"])
        if nt in ctx["seen_titles"]:
            st["dup"] += 1
            continue
        ctx["seen_titles"].add(nt)
        kept.append({
            "source": source,
            "group": group,
            "title": it["title"],
            "summary": it["summary"],
            "link": it["link"],
            "key": it["key"],
            "date": it["dt"].strftime("%Y-%m-%d %H:%M") if it["dt"] else "Recently",
        })
    return kept


def select_by_quota(per_group):
    """По кожній групі беремо квоту, чергуючи фіди всередині групи."""
    selected = []
    for group, quota in QUOTAS.items():
        queues = [list(q) for q in per_group.get(group, []) if q]
        taken = 0
        while queues and taken < quota:
            for q in list(queues):
                if taken >= quota:
                    break
                selected.append(q.pop(0))
                taken += 1
                if not q:
                    queues.remove(q)
    return selected


def fetch_entries(sent):
    """Збирає та фільтрує інфоприводи з усіх джерел. Повертає (записи, скільки всього розпарсено)."""
    ctx = {
        "cutoff": datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK),
        "sent": sent,
        "seen_titles": set(),
    }
    per_group = {g: [] for g in QUOTAS}
    total_parsed = 0

    for cfg in FEEDS:
        name = cfg["name"]
        st = {"status": "-", "parsed": 0, "stop": 0, "blocked": 0, "nosource": 0, "old": 0, "sent": 0, "dup": 0}
        kept = []
        error = ""
        try:
            resp = requests.get(cfg["url"], headers=HTTP_HEADERS, timeout=20)
            st["status"] = resp.status_code
            if resp.status_code == 200:
                if cfg.get("kind") == "trends":
                    raw = parse_trends(resp.content, st)
                else:
                    raw = parse_rss(resp.content)
                kept = filter_items(raw, name, cfg["group"], ctx, st)
        except Exception as e:
            error = f" | ПОМИЛКА: {redact(e)}"

        total_parsed += st["parsed"]
        per_group[cfg["group"]].append(kept)
        mark = " (URL не перевірений)" if cfg.get("unverified") and st["status"] != 200 else ""
        print(
            f"[FEED] {name}: HTTP {st['status']} | записів: {st['parsed']} | "
            f"відсіяно стоп-словами: {st['stop']} | відкинуто блоклистом: {st['blocked']} | "
            f"без домену джерела: {st['nosource']} | застарілих: {st['old']} | "
            f"уже надсилали: {st['sent']} | дублів: {st['dup']} | лишилось: {len(kept)}{mark}{error}"
        )

    return select_by_quota(per_group), total_parsed


# ── Генерація через Google Gemini API ────────────────────────────────────────

def today_ua():
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
    except Exception:
        now = datetime.now(timezone.utc)
    return f"{now.day} {UA_MONTHS[now.month - 1]}"


def build_prompt(entries):
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. [{e['source']}] {e['title']}")
        if e["summary"]:
            lines.append(f"   Details: {e['summary']}")
        if e["link"]:
            lines.append(f"   Link: {e['link']}")
        lines.append("")

    raw_news = "\n".join(lines) if entries else "Сьогодні тихо - напиши коротко, що в американській мережі спокійно."

    return f"""Ти — турботливий, дотепний хлопець із гарним почуттям гумору, який готує щоденний мінідайджест для своєї коханої дівчини в Telegram.
Твоя мета — підняти їй настрій, розважити, розповісти найсоковитіші плітки, меми та тренди з американського інтернету й поп-культури США.

МОВА:
— Записи нижче англійською. Пиши пост УКРАЇНСЬКОЮ.
— Імена, назви треків, альбомів, фільмів, шоу, брендів, платформ і хештеги лишай латиницею, як в оригіналі. Не перекладай їх і не транслітеруй.
— Американські реалії коротко пояснюй, якщо без цього незрозуміло (наприклад, що таке реаліті-шоу чи Super Bowl).

СТИЛЬ ТА ІНТОНАЦІЯ:
— Живий, теплий, легкий тон із доречною самоіронією. Звертайся до неї особисто, ніби ви п'єте каву і ти розповідаєш: «Уяви, що сьогодні несеться в американському інтернеті».
— СУВОРИЙ ЗАХИСТ: ЖОДНИХ сухих новин, пресрелізів, політики чи депресивних тем. Тільки поп-культура, стосунки, життєві ситуації, меми та драми блогерів.

ПРАВДИВІСТЬ:
— Пиши тільки те, що є в записах. Не згадуй TikTok, Threads чи X, якщо в записі про це не сказано. Рубрику без матеріалу пропусти.
— Нічого не вигадуй: імена, цитати, цифри й подробиці бери лише із записів.

Сьогоднішня дата: {today_ua()}

Ось зібрані інфоприводи та теми за останню добу:
{raw_news}

ЗАВДАННЯ:
1. Обери 4–5 найцікавіших або найкумедніших подій із записів.
2. Якщо в записах є скандал блогерів чи гаряча дискусія в соцмережах — подай це смачно й дотепно.
3. Оформи це як естетичний ранковий пост для Telegram.

РУБРИКИ (кожну бери лише з реального запису; немає запису — пропускай рубрику):
💅 <b>Головна інтрига або срач дня</b> (скандал чи гаряча дискусія — тільки з реального запису)
🐸 <b>Мем / прикол дня</b> (тільки з реального запису про мем, жарт чи кумедну ситуацію)
🍿 <b>Життєва дичина з мережі</b> (кумедна суперечка про стосунки, побут, побачення чи роботу — якщо така є в записах)
🎧 <b>Що зацінити разом</b> (новий трек, кліп, трейлер, шоу чи відео — з записів)
✨ <b>Питання для нас на вечір</b> (єдина рубрика, яку можна придумати самому: одна цікава або кумедна тема чи питання для вас двох, це не факт, а привід поговорити)

ФОРМАТУВАННЯ ДЛЯ TELEGRAM (HTML):
— Заголовок: ☕️ <b>ЩО ТАМ НЕСЕТЬСЯ В АМЕРИКАНСЬКОМУ ІНТЕРНЕТІ</b> | [сьогоднішня дата, вказана вище]
— Кожен пункт: виразний жирний заголовок через <b>...</b>, далі 1–3 змістовні речення без води.
— Обов'язково додавай посилання на джерело з поля «Link»: <a href="URL">Глянути</a> або <a href="URL">Детальніше</a>.
— Між рубриками став чіткий роздільник: ———
— Використовуй тільки безпечні HTML-теги: <b>, <i>, <a href="...">.

Поверни ВИКЛЮЧНО готовий текст посту для Telegram."""


SHORTEN_NOTE = (
    "\n\nВАЖЛИВО: попередня відповідь не вмістилась у ліміт і обрізалась. "
    "Зроби пост помітно коротшим: 3–4 рубрики, по 1–2 речення, обов'язково закінчи пост повністю."
)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def gemini_headers(api_key):
    """Ключ іде в заголовку, а не в URL: так він не потрапляє в тексти помилок і логи."""
    return {"Content-Type": "application/json", "x-goog-api-key": api_key}


def get_available_models(api_key):
    """Автоматично отримує актуальний список робочих моделей від Google."""
    try:
        r = requests.get(f"{GEMINI_BASE}/models", headers=gemini_headers(api_key), timeout=12)
        if r.status_code == 200:
            data = r.json()
            models = [
                m["name"].replace("models/", "")
                for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
                and "-tts" not in m.get("name", "")
                and "embedding" not in m.get("name", "")
                and "image" not in m.get("name", "")
            ]
            # Пріоритет: flash-lite (безкоштовний тариф), далі flash, потім решта; pro — в кінець
            lite = [m for m in models if "flash-lite" in m]
            flash = [m for m in models if "flash" in m and m not in lite]
            pro = [m for m in models if "pro" in m and m not in lite and m not in flash]
            others = [m for m in models if m not in lite and m not in flash and m not in pro]
            return lite + flash + others + pro
    except Exception as e:
        print(f"[WARN] Не вдалося отримати список моделей: {redact(e)}")

    return ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]


def repair_truncated(text):
    """Обрізає по останньому ——— (або відкидає недописаний тег) і закриває відкриті <b>/<i>/<a>."""
    if "———" in text:
        text = text.rsplit("———", 1)[0]
    else:
        text = re.sub(r"<[^>]*$", "", text)
    text = text.rstrip()

    stack = []
    for m in re.finditer(r"<(/?)(b|i|a)\b[^>]*>", text):
        if m.group(1):
            if m.group(2) in stack:
                while stack and stack.pop() != m.group(2):
                    pass
        else:
            stack.append(m.group(2))
    return text + "".join(f"</{t}>" for t in reversed(stack))


def _generate(url, payload, api_key):
    """Один запит до Gemini. Повертає (text або None, finishReason)."""
    headers = gemini_headers(api_key)
    resp = requests.post(url, headers=headers, json=payload, timeout=45)

    if resp.status_code == 400 and "thinkingConfig" in resp.text:
        # Fallback без thinkingConfig для моделей, які його не знають
        config = {k: v for k, v in payload["generationConfig"].items() if k != "thinkingConfig"}
        resp = requests.post(url, headers=headers, json={**payload, "generationConfig": config}, timeout=45)

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    candidates = resp.json().get("candidates", [])
    if not candidates:
        return None, "NO_CANDIDATES"

    first = candidates[0]
    finish_reason = first.get("finishReason", "")
    parts = first.get("content", {}).get("parts", [])
    # Витягуємо тексти, ігноруючи технічні думки моделі
    text_parts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought", False)]
    if not text_parts:
        text_parts = [p.get("text", "") for p in parts if "text" in p]
    return "".join(text_parts).strip() or None, finish_reason


def call_gemini_api(prompt, api_key, preferred_model):
    """
    Звертається до Gemini API з ослабленими безпековими фільтрами (щоб не тригерити помилковий SAFETY блокінг)
    та надійно витягує згенерований текст навіть за наявності думок (thought tokens).
    """
    available = get_available_models(api_key)
    print(f"[INFO] Доступні моделі Google: {available[:5]}")

    models_to_try = []
    if preferred_model:
        models_to_try.append(preferred_model)
    for m in available:
        if m not in models_to_try:
            models_to_try.append(m)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.75,
            "maxOutputTokens": 4000,
            "thinkingConfig": {"thinkingBudget": 0}
        },
        # Ослаблюємо безпековий фільтр, щоб не блокувати звичайні життєві інфоприводи
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}
        ]
    }

    for model_name in models_to_try:
        url = f"{GEMINI_BASE}/models/{model_name}:generateContent"
        print(f"[INFO] Звертаємося до моделі: {model_name}...")
        try:
            text, finish_reason = _generate(url, payload, api_key)

            if finish_reason == "SAFETY":
                print(f"[WARN] Модель {model_name} спрацювала на SAFETY фільтр, переходимо далі...")
                continue

            if finish_reason == "MAX_TOKENS":
                print(f"[WARN] Модель {model_name}: відповідь обрізана (MAX_TOKENS), просимо скоротити...")
                shorter = {**payload, "contents": [{"parts": [{"text": prompt + SHORTEN_NOTE}]}]}
                text2, finish2 = _generate(url, shorter, api_key)
                if text2:
                    text = text2 if finish2 != "MAX_TOKENS" else repair_truncated(text2)
                elif text:
                    text = repair_truncated(text)

            if text:
                print(f"[OK] Успішно згенеровано моделлю {model_name}!")
                return text

            print(f"[WARN] Модель {model_name} не дала тексту ({finish_reason}), пробуємо наступну...")
        except Exception as e:
            print(f"[WARN] Модель {model_name} не відповіла ({redact(e)}), пробуємо наступну...")

    return None


def generate_digest(entries):
    """Повертає (текст, записи, що фактично потрапили в пост)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Помилка: змінна оточення GEMINI_API_KEY не знайдена в Secrets!")

    prompt = build_prompt(entries)
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    text = call_gemini_api(prompt, api_key, model)
    if text:
        lowered = text.lower()
        used = [
            e for e in entries
            if (e["link"] and (e["link"] in text or html.escape(e["link"]) in text))
            or e["title"].lower() in lowered
        ]
        # Якщо збігів не знайшли — вважаємо надісланими всі передані моделі
        return text, used or entries

    # Резервний вивід новин, якщо всі моделі тимчасово відмовили
    shown = entries[:5]
    fallback_lines = ["⚠️ <b>Gemini тимчасово зайнятий. Ось головні цікавинки дня:</b>\n"]
    for item in shown:
        link_html = f' (<a href="{html.escape(item["link"])}">Посилання</a>)' if item["link"] else ""
        fallback_lines.append(f"• <b>{html.escape(item['title'])}</b> — {item['source']}{link_html}")
    return "\n".join(fallback_lines), shown


# ── Відправка в Telegram ─────────────────────────────────────────────────────

def split_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]

    parts = []
    sections = text.split("———")
    current = ""

    for s in sections:
        section = (s.strip() + "\n\n———\n\n") if current else s.strip()
        if len(current) + len(section) <= max_len:
            current += section
        else:
            if current:
                parts.append(current.rstrip("—\n ").strip())
            current = section

    if current:
        parts.append(current.rstrip("—\n ").strip())

    return [p for p in parts if p]


def send_telegram(text, chat_id):
    """Надсилає текст у Telegram. Повертає кількість частин, що дійшли."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    if not token or not chat_id:
        print("[ERROR] Не вказано TELEGRAM_BOT_TOKEN або ID чату!")
        return 0

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    messages = split_message(text)
    delivered = 0

    for idx, msg in enumerate(messages, 1):
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            r = requests.post(url, json=payload, timeout=25)
            if r.ok:
                print(f"[OK] Частина {idx}/{len(messages)} надіслана в Telegram (HTML)")
                delivered += 1
                continue

            print(f"[WARN] Помилка HTML ({r.status_code}), пробуємо чистим текстом...")
            clean_text = re.sub(r"<[^>]+>", "", msg)
            payload["parse_mode"] = None
            payload["text"] = clean_text
            r_plain = requests.post(url, json=payload, timeout=25)

            if r_plain.ok:
                print(f"[OK] Частина {idx}/{len(messages)} надіслана чистим текстом")
                delivered += 1
            else:
                print(f"[ERROR] Помилка Telegram: {r_plain.status_code} {redact(r_plain.text)}")

        except Exception as e:
            print(f"[ERROR] Помилка мережі при надсиланні в Telegram: {redact(e)}")

    return delivered


# ── Головна точка входу ──────────────────────────────────────────────────────

def main():
    now_utc = datetime.now(timezone.utc)
    test_mode = os.environ.get("TEST_MODE", "").strip().lower() == "true"
    print(f"[START] US Trends Run: {now_utc.strftime('%Y-%m-%d %H:%M UTC')} | "
          f"режим: {'ТЕСТОВИЙ (sent.json не оновлюється)' if test_mode else 'БОЙОВИЙ'}")

    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if test_mode and os.environ.get("TELEGRAM_TEST_CHAT_ID"):
        chat_id = os.environ["TELEGRAM_TEST_CHAT_ID"]
    elif test_mode:
        print("[INFO] TELEGRAM_TEST_CHAT_ID не задано: тест піде в TELEGRAM_CHAT_ID.")
    if not chat_id:
        print("[ERROR] Не вказано TELEGRAM_CHAT_ID!")
        sys.exit(1)

    sent = load_sent()
    entries, total_parsed = fetch_entries(sent)
    print(f"[INFO] Зібрано актуальних тем: {len(entries)} (розпарсено з фідів: {total_parsed})")

    if total_parsed == 0:
        print("[ERROR] Жоден фід не віддав записів — перевір лог фідів вище.")
        sys.exit(1)

    digest, used_entries = generate_digest(entries)
    delivered = send_telegram(digest, chat_id)
    if delivered == 0:
        print("[ERROR] Жодна частина дайджесту не дійшла в Telegram!")
        sys.exit(1)

    if test_mode:
        print("[INFO] Тестовий режим: sent.json не оновлюється.")
    elif used_entries:
        save_sent(sent, used_entries)
    print("[DONE] Дайджест згенеровано та надіслано!")


if __name__ == "__main__":
    main()
