"""Wikipedia over the lounge HTTP UI and NomadNet Micron."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from trie import TitleTrie

title = "Wikipedia"
description = "Search and read Wikipedia over HTTP and Reticulum."
path = "/wiki"
is_home = True
micron_paths = [
    "/page/index.mu",
    "/page/index",
    "/page/wiki.mu",
    "/page/wiki",
    "/page/wikipedia.mu",
    "/page/wikipedia",
]

PAGE = os.path.join(HERE, "page.mu")
TITLES = os.path.join(HERE, "titles.txt")
UA = "reticulum-wikipedia/0.1 (Reticulum Wikipedia node)"
SUGGEST_LIMIT = 18
MICRON_LIMIT = 40
CACHE_TTL = 7 * 24 * 3600
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

ctx = None
trie = TitleTrie()
cache_dir = ""
wiki_data = ""
wiki_lang = "en"
wiki_fetch = "on_pi"
wiki_scope = "top10000"
lock = threading.Lock()
_opener = urllib.request.build_opener()
_opener.addheaders = [("User-Agent", UA), ("Accept", "application/json")]


def _s(value, default=""):
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value)


def wiki_host(lang=None):
    code = _s(lang or wiki_lang or "en").strip().lower()
    if not code or code in ("*", "all"):
        code = "en"
    return f"https://{code}.wikipedia.org"


def api_url():
    return wiki_host() + "/w/api.php"


def rest_url():
    return wiki_host() + "/api/rest_v1/page"


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _wiki_dirs():
    home = os.path.expanduser("~/.rns-lounge")
    return [
        os.environ.get("WIKI_DATA") or "",
        "/var/lib/rns/wiki_data",
        os.path.join(ROOT, "wiki_data"),
        os.path.join(home, "wiki"),
        os.path.join(HERE, "wiki_data"),
    ]


def _read_config():
    lang = (os.environ.get("WIKI_LANG") or "").strip().lower()
    fetch = (os.environ.get("WIKI_FETCH") or "").strip().lower()
    scope = (os.environ.get("WIKI_SCOPE") or "").strip().lower()
    folder = ""
    for d in _wiki_dirs():
        if not d:
            continue
        cfg = os.path.join(d, "config.json")
        if os.path.isfile(cfg):
            data = _load_json(cfg)
            folder = d
            lang = lang or str(data.get("lang") or "")
            fetch = fetch or str(data.get("fetch") or "")
            scope = scope or str(data.get("scope") or "")
            break
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "titles.txt")):
            folder = d
            break
    if lang in ("*", "all"):
        lang = "*"
    else:
        lang = (lang or "en").strip().lower() or "en"
    return lang, fetch or "on_pi", scope or "top10000", folder


def _title_files():
    files = []
    if wiki_data:
        files.append(os.path.join(wiki_data, "titles.txt"))
    files.append(os.path.join(os.path.expanduser("~/.rns-lounge"), "wiki", "titles.txt"))
    files.append(TITLES)
    seen = set()
    out = []
    for path in files:
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        out.append(path)
    return out


def _load_titles():
    n = 0
    for path in _title_files():
        try:
            with open(path, encoding="utf-8") as fh:
                n += trie.load_lines(fh)
            print(f"[wiki] titles {path}")
        except Exception as exc:
            print(f"[wiki] titles skip {path}: {exc}")
    if cache_dir and os.path.isdir(cache_dir):
        for name in os.listdir(cache_dir):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(cache_dir, name), encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("title"):
                    trie.insert(data["title"])
            except Exception:
                pass
    return n


def _fetch_index_on_pi():
    dest = wiki_data or os.path.join(os.path.expanduser("~/.rns-lounge"), "wiki")
    os.makedirs(dest, exist_ok=True)
    titles = os.path.join(dest, "titles.txt")
    if os.path.isfile(titles) and os.path.getsize(titles) > 1024:
        return
    print(f"[wiki] on-pi fetch {wiki_lang} -> {dest}")
    try:
        import importlib.util

        prefetch_py = os.path.join(ROOT, "wizard", "prefetch.py")
        if not os.path.isfile(prefetch_py):
            print("[wiki] prefetch.py missing")
            return
        spec = importlib.util.spec_from_file_location("wiki_prefetch", prefetch_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if wiki_scope == "live":
            return
        mod.prefetch_titles(wiki_lang if wiki_lang != "*" else "en", dest, scope=wiki_scope)
        _load_titles()
        print(f"[wiki] indexed {trie.size} titles after on-pi fetch")
    except Exception as exc:
        print("[wiki] on-pi fetch failed:", exc)


def setup(app):
    global ctx, cache_dir, wiki_data, wiki_lang, wiki_fetch, wiki_scope
    ctx = app
    home = os.path.expanduser("~/.rns-lounge")
    wiki_lang, wiki_fetch, wiki_scope, wiki_data = _read_config()
    if not wiki_data:
        wiki_data = os.path.join(home, "wiki")
    cache_dir = os.path.join(wiki_data, "pages") if str(wiki_data).endswith("wiki_data") else os.path.join(home, "wiki", "pages")
    os.makedirs(cache_dir, exist_ok=True)
    n = _load_titles()
    print(f"[wiki] lang={wiki_lang} scope={wiki_scope} fetch={wiki_fetch} data={wiki_data}")
    print(f"[wiki] indexed {trie.size} titles ({n} from files)")
    if wiki_fetch == "on_pi" and wiki_scope != "live":
        threading.Thread(target=_fetch_index_on_pi, daemon=True).start()


def esc(s):
    if ctx is not None:
        return ctx.esc(s)
    return html.escape(str(s or ""), quote=True)


def _safe_name(name):
    raw = urllib.parse.unquote(str(name or "")).replace("_", " ").strip()
    raw = re.sub(r"[^\w\s\-\(\)\'\.\,\:]", "", raw, flags=re.UNICODE)
    return " ".join(raw.split())[:180]


def _cache_path(name):
    key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:80] or "page"
    return os.path.join(cache_dir or "/tmp", key + ".json")


def _http_json(url, timeout=12):
    try:
        with _opener.open(url, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _cached(name):
    path = _cache_path(name)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if time.time() - float(data.get("fetched") or 0) < CACHE_TTL:
            return data
        return data
    except Exception:
        return None


def _store(data):
    if not data or not data.get("title"):
        return
    path = _cache_path(data["title"])
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
        trie.insert(data["title"])
    except Exception:
        pass


class _Plain(HTMLParser):
    skip = {
        "script", "style", "table", "sup", "noscript",
        "img", "figure", "picture", "audio", "video", "source",
        "track", "map", "area", "canvas", "svg", "figcaption",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip = 0
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.skip:
            self._skip += 1
            return
        if self._skip:
            return
        attrs = dict(attrs)
        if tag in ("p", "div", "tr"):
            self.out.append("\n")
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n## ")
        elif tag in ("li", "br"):
            self.out.append("\n• " if tag == "li" else "\n")
        elif tag == "a":
            href = attrs.get("href") or ""
            if href.startswith("/wiki/") and ":" not in href.split("/wiki/", 1)[-1]:
                self._href = urllib.parse.unquote(href.split("/wiki/", 1)[-1]).replace("_", " ")
            else:
                self._href = ""

    def handle_endtag(self, tag):
        if tag in self.skip and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "a":
            self._href = ""
        if tag in ("p", "h1", "h2", "h3", "h4"):
            self.out.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        if self._href:
            self.out.append(f"[[{text}:{self._href}]]")
        else:
            self.out.append(text)


def html_to_text(raw):
    p = _Plain()
    try:
        p.feed(raw or "")
        p.close()
    except Exception:
        return ""
    text = "".join(p.out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def fetch_article(name):
    name = _safe_name(name)
    if not name:
        return None
    hit = _cached(name)
    if hit and hit.get("text") and hit.get("fmt") == 2:
        return hit
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "redirects": 1,
            "format": "json",
            "titles": name,
        }
    )
    data = _http_json(api_url() + "?" + q)
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    page = next(iter(pages.values()), {}) if isinstance(pages, dict) else {}
    if page.get("missing") is not None and not page.get("extract"):
        page = {}
    display = _s(page.get("title") or name)
    raw_extract = _s(page.get("extract")).strip()
    if "<" in raw_extract:
        body = html_to_text(raw_extract)
    else:
        body = raw_extract
    summary = ""
    rest = _http_json(rest_url() + "/summary/" + urllib.parse.quote(name.replace(" ", "_")))
    if rest:
        summary = _s(rest.get("extract")).strip()
        display = _s(rest.get("title") or display)
        if not body:
            body = summary
    if not body and not summary:
        if hit:
            return hit
        return None
    host = (wiki_lang if wiki_lang not in ("*", "all") else "en") + ".wikipedia.org"
    article = {
        "title": display,
        "summary": summary,
        "text": body or summary,
        "fmt": 2,
        "fetched": time.time(),
        "source": host,
    }
    _store(article)
    return article


def _opensearch(query, limit):
    q = urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "namespace": 0,
            "format": "json",
        }
    )
    data = _http_json(api_url() + "?" + q, timeout=6)
    if not isinstance(data, list) or len(data) < 2:
        return []
    out = []
    for name in data[1]:
        if name and name not in out:
            out.append(name)
    return out


def suggest(query, limit=SUGGEST_LIMIT):
    query = _s(query).strip()
    if not query:
        return []
    with lock:
        hits = trie.complete(query, limit)[:limit]
    live = wiki_scope in ("live", "all_languages")
    if live or len(hits) < min(6, limit):
        for name in _opensearch(query, limit):
            if name not in hits:
                hits.append(name)
                trie.insert(name)
            if len(hits) >= limit:
                break
    return hits[:limit]


def _shell(page_title, body, landing=False):
    klass = "wiki-landing" if landing else "wiki-page"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(page_title)}</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/htmx.min.js" defer></script>
</head>
<body class="{klass}">
{body}
</body>
</html>"""


def _search_form(query="", autofocus=True):
    focus = " autofocus" if autofocus else ""
    q = esc(query)
    return f"""
<form class="wiki-search" action="/wiki" method="get">
  <input type="search" name="q" value="{q}" placeholder="Search Wikipedia"
         aria-label="Search Wikipedia"{focus}
         hx-get="/wiki/suggest" hx-trigger="keyup changed delay:80ms, search"
         hx-target="#suggest" hx-include="this" autocomplete="off">
  <button class="btn wiki-go" type="submit">Search</button>
</form>
<div id="suggest" class="wiki-suggest"></div>
"""


def page_landing(query=""):
    return _shell(
        "WIKIPEDIA",
        f"""
<main class="wiki-hero">
  <h1 class="wiki-mark">WIKIPEDIA</h1>
  <p class="wiki-sub">The Free Encyclopedia · over Reticulum</p>
  {_search_form(query, True)}
</main>
<footer class="wiki-foot">Articles: English Wikipedia, CC BY-SA. Prototype index: {trie.size} titles.</footer>
""",
        landing=True,
    )


def page_results(query):
    hits = suggest(query, 30)
    if len(hits) == 1 and hits[0].casefold() == query.casefold():
        article = fetch_article(hits[0])
        if article:
            return page_article(article)
    items = []
    for name in hits:
        items.append(
            f'<li><a href="/wiki/view?title={urllib.parse.quote(name)}">{esc(name)}</a></li>'
        )
    body = "".join(items) or "<li class='muted'>No matching titles in the index.</li>"
    return _shell(
        f"{query} · WIKIPEDIA",
        f"""
<header class="wiki-top">
  <a class="wiki-mark-sm" href="/">WIKIPEDIA</a>
  {_search_form(query, False)}
</header>
<main class="wiki-results">
  <p class="muted">{len(hits)} matches for “{esc(query)}”</p>
  <ul class="wiki-list">{body}</ul>
</main>
""",
    )


def _html_body(text):
    chunks = []
    for block in (text or "").split("\n"):
        block = block.rstrip()
        if not block:
            continue
        if block.startswith("## "):
            chunks.append(f"<h2>{_linkify(block[3:])}</h2>")
        elif block.startswith("• "):
            chunks.append(f"<li>{_linkify(block[2:])}</li>")
        else:
            chunks.append(f"<p>{_linkify(block)}</p>")
    html_out = []
    in_list = False
    for part in chunks:
        is_li = part.startswith("<li>")
        if is_li and not in_list:
            html_out.append("<ul>")
            in_list = True
        if not is_li and in_list:
            html_out.append("</ul>")
            in_list = False
        html_out.append(part)
    if in_list:
        html_out.append("</ul>")
    return "".join(html_out)


def _linkify(text):
    def repl(m):
        label, dest = m.group(1), m.group(2)
        href = "/wiki/view?title=" + urllib.parse.quote(dest)
        return f'<a href="{href}">{esc(label)}</a>'

    pieces = []
    last = 0
    for m in re.finditer(r"\[\[([^:\]]+):([^\]]+)\]\]", text):
        pieces.append(esc(text[last:m.start()]))
        pieces.append(repl(m))
        last = m.end()
    pieces.append(esc(text[last:]))
    return "".join(pieces)


def page_article(article):
    name = article["title"]
    summary = article.get("summary") or ""
    lead = f"<p class='wiki-lead'>{esc(summary)}</p>" if summary else ""
    return _shell(
        f"{name} · WIKIPEDIA",
        f"""
<header class="wiki-top">
  <a class="wiki-mark-sm" href="/">WIKIPEDIA</a>
  {_search_form(name, False)}
</header>
<main class="wiki-article">
  <h1>{esc(name)}</h1>
  {lead}
  <article>{_html_body(article.get("text") or "")}</article>
  <p class="wiki-src">Source: {esc(wiki_lang)}.wikipedia.org · {esc(name)} · CC BY-SA 4.0</p>
</main>
""",
    )


def page_missing(name):
    return _shell(
        "Not found · WIKIPEDIA",
        f"""
<header class="wiki-top">
  <a class="wiki-mark-sm" href="/">WIKIPEDIA</a>
  {_search_form(name, False)}
</header>
<main class="wiki-article">
  <h1>{esc(name)}</h1>
  <p>This node has no cached copy and could not reach English Wikipedia.</p>
  <p class="muted">On the mesh, pages appear here after a node with internet has fetched them once.</p>
</main>
""",
    )


def render_suggest(query):
    hits = suggest(query)
    if not hits:
        return ""
    bits = []
    for name in hits:
        href = "/wiki/view?title=" + urllib.parse.quote(name)
        bits.append(f'<a href="{href}">{esc(name)}</a>')
    return "".join(bits)


def get(req_path, query):
    query = query or {}
    path = (req_path or "").rstrip("/") or "/"
    if path == "/wiki/suggest":
        return 200, "text/html; charset=utf-8", render_suggest(query.get("q") or "")
    if path == "/wiki/view":
        name = _safe_name(query.get("title") or query.get("q") or "")
        if not name:
            return page_landing()
        article = fetch_article(name)
        return page_article(article) if article else page_missing(name)
    if path in ("/", "/wiki"):
        q = _s(query.get("q") or query.get("title")).strip()
        if q:
            return page_results(q)
        return page_landing()
    return None


def post(req_path, form):
    return get(req_path, form or {})


def _mu_clean(s, n=80):
    out = (
        str(s or "")
        .replace("`", "'")
        .replace("<", " ")
        .replace(">", " ")
        .replace("|", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return out[:n].strip()


_MU_LINK = re.compile(r"\[\[([^:\[\]]+):([^\[\]]+)\]\]")


def _mu_href(title, label=None):
    label = _mu_clean(label or title, 42) or "link"
    key = _mu_clean(title, 60).replace(" ", "_")
    if not key:
        return label
    return f"`F7bf`_`[{label}`:/page/wiki.mu`title={key}]`_`f"


def _mu_heading(line):
    s = _s(line).strip()
    if s.startswith("## "):
        return _mu_clean(s[3:], 48)
    if s.startswith("=") and s.endswith("="):
        return _mu_clean(s.strip("= "), 48)
    if "[[" in s or s.endswith((".", "!", "?", ",", ";")):
        return ""
    words = s.split()
    if 1 <= len(words) <= 8 and 2 <= len(s) <= 48 and s[0].isupper():
        return _mu_clean(s, 48)
    return ""


def _mu_wrap(text, width=88):
    words = [w for w in _s(text).split(" ") if w]
    lines = []
    cur = ""
    for word in words:
        trial = word if not cur else cur + " " + word
        if cur and len(trial) > width and not word.startswith("`"):
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _mu_rich(text):
    text = str(text or "").replace("`", "'")
    chunks = []
    pos = 0
    for match in _MU_LINK.finditer(text):
        before = " ".join(text[pos:match.start()].split())
        if before:
            chunks.append(before)
        chunks.append(_mu_href(match.group(2), match.group(1)))
        pos = match.end()
    tail = " ".join(text[pos:].split())
    if tail:
        chunks.append(tail)
    return _mu_wrap(" ".join(chunks))


def _micron_shell(*body):
    lines = [
        "#!c=0",
        "#!bg=111",
        "#!fg=eee",
        "`c",
        "`F6cf`! WIKIPEDIA `!`f",
        "`F888 The Free Encyclopedia`f",
        "`a",
        "",
    ]
    lines.extend(body)
    lines.append("")
    lines.append("`F555 " + wiki_lang + ".wikipedia.org  CC BY-SA`f")
    lines.append("`B46a`F000`[  search  `:/page/index.mu]`f`b")
    return "\n".join(lines) + "\n"


def _micron_article(name):
    article = fetch_article(name)
    if not article:
        return _micron_shell(
            f"`Ffaa`! {_mu_clean(name, 48)} `!`f",
            "",
            "`F888 That page is not on this node.`f",
        )
    title = _mu_clean(article["title"], 52)
    parts = [
        "`B46a`F000`[ search `:/page/index.mu]`f`b",
        "",
        f"`F4af`! {title} `!`f",
        "",
    ]
    body = _s(article.get("text") or article.get("summary"))
    size = 0
    for raw in body.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        head = _mu_heading(raw)
        if head:
            parts.append("")
            parts.append("`F8cf`! " + head + " `!`f")
            continue
        lines = _mu_rich(raw)
        if not lines:
            continue
        parts.extend(lines)
        parts.append("")
        size += sum(len(x) for x in lines)
        if size > 80000:
            parts.append("`F555 …`f")
            break
    return _micron_shell(*parts)


def micron(path, fields, remote_identity=None):
    fields = fields or {}
    fields = {_s(k): _s(v) for k, v in (fields or {}).items()}
    raw = _s(path).rstrip("/")
    slug = raw.rsplit("/", 1)[-1].replace(".mu", "")
    q = _s(fields.get("q") or fields.get("search")).strip()
    name = _safe_name(fields.get("title") or fields.get("t") or "")
    if slug == "wiki":
        return _micron_article(name or _safe_name(q))
    if name and (not q or name.casefold() == q.casefold()):
        return _micron_article(name)

    hits = suggest(q, MICRON_LIMIT) if q else []
    try:
        with open(PAGE, encoding="utf-8") as fh:
            tmpl = fh.read()
    except Exception:
        tmpl = "`! WIKIPEDIA `!\n{{results}}\n"
    rows = []
    if q and not hits:
        rows.append("`F888 no titles for " + _mu_clean(q, 40) + "`f")
    elif q:
        rows.append("`F555 " + str(len(hits)) + " matches`f")
        rows.append("")
    for name in hits:
        rows.append(_mu_href(name))
    return (
        tmpl.replace("{{q}}", _mu_clean(q, 40))
        .replace("{{results}}", "\n".join(rows) if rows else "`F888 type a title`f")
        .replace("{{count}}", str(trie.size))
    )
