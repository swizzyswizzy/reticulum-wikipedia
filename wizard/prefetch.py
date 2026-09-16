"""Build a text-only Wikipedia title index into wiki_data/."""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "reticulum-wikipedia/0.1 (title prefetch; text only)"
DUMP = "https://dumps.wikimedia.org/{lang}wiki/latest/{lang}wiki-latest-all-titles-in-ns0.gz"
MAJOR_LANGS = (
    "en", "pl", "de", "fr", "es", "ru", "zh", "ja", "it", "pt",
    "uk", "ar", "nl", "cs", "sv", "fi", "tr", "ko", "vi", "id",
)
TOP_N = 10000


def fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    if i == 0:
        return "%d B" % n
    return "%.1f %s" % (n, units[i])


def fmt_count(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def dump_url(lang: str) -> str:
    lang = (lang or "en").strip().lower()
    return DUMP.format(lang=lang)


def write_config(folder: str, lang: str, fetch: str, extra=None) -> str:
    os.makedirs(folder, exist_ok=True)
    cfg = {
        "lang": lang,
        "fetch": fetch,
        "project": "reticulum-wikipedia",
        "text_only": True,
    }
    if extra:
        cfg.update(extra)
    path = os.path.join(folder, "config.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def _opener():
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", UA), ("Accept", "application/json")]
    return opener


def _json(url, timeout=20):
    try:
        with _opener().open(url, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _clean_title(title):
    title = (title or "").replace("_", " ").strip()
    if not title or title.startswith("#"):
        return ""
    if ":" in title.split(" ")[0] and title.split(":")[0] in (
        "File", "Image", "Category", "Wikipedia", "Help", "Template",
        "Talk", "User", "Portal", "Draft", "Module", "MediaWiki", "Special",
    ):
        return ""
    return title


def _write_titles(path, titles):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        for item in titles:
            fh.write(item + "\n")
    os.replace(tmp, path)


class _Progress:
    def __init__(self, cb):
        self.cb = cb
        self.last = 0.0

    def send(self, done, total, msg, force=False):
        if not self.cb:
            return
        now = time.monotonic()
        if not force and now - self.last < 10:
            self.cb(done, total, "")
            return
        self.last = now
        self.cb(done, total, msg)


def _pageview_top(lang, limit, prog):
    out = []
    seen = set()
    now = datetime.now(timezone.utc)
    for back in range(2, 45):
        if len(out) >= limit:
            break
        day = now - timedelta(days=back)
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            f"{lang}.wikipedia/all-access/{day.year}/{day.month:02d}/{day.day:02d}"
        )
        data = _json(url)
        arts = ((data or {}).get("items") or [{}])[0].get("articles") or []
        for row in arts:
            title = _clean_title(row.get("article") or "")
            if not title or title in seen:
                continue
            seen.add(title)
            out.append(title)
            if len(out) >= limit:
                break
        prog.send(len(out), limit, f"{lang} popular {fmt_count(len(out))}")
        time.sleep(0.05)
    return out


def prefetch_top(lang, dest_dir, prog, limit=TOP_N):
    lang = (lang or "en").strip().lower() or "en"
    prog.send(0, limit, f"top {fmt_count(limit)} titles ({lang})", force=True)
    titles = _pageview_top(lang, limit, prog)
    path = os.path.join(dest_dir, "titles.txt")
    _write_titles(path, titles[:limit])
    return len(titles[:limit])


def prefetch_all_titles(lang, dest_dir, prog):
    lang = (lang or "en").strip().lower() or "en"
    url = dump_url(lang)
    gz_path = os.path.join(dest_dir, f"{lang}wiki-latest-all-titles-in-ns0.gz")
    titles_path = os.path.join(dest_dir, "titles.txt")
    prog.send(0, 0, "GET " + url, force=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as src:
        total = int(src.headers.get("Content-Length") or 0)
        got = 0
        tmp = gz_path + ".part"
        with open(tmp, "wb") as out:
            while True:
                chunk = src.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if total:
                    msg = "download %s / %s" % (fmt_bytes(got), fmt_bytes(total))
                else:
                    msg = "download %s" % fmt_bytes(got)
                prog.send(got, total, msg)
        os.replace(tmp, gz_path)
        prog.send(got, total or got, "downloaded " + fmt_bytes(got), force=True)

    prog.send(0, 0, "unpack titles (text only)", force=True)
    n = 0
    tmp_txt = titles_path + ".part"
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as src:
        with open(tmp_txt, "w", encoding="utf-8") as out:
            for line in src:
                title = _clean_title(line)
                if not title:
                    continue
                out.write(title + "\n")
                n += 1
                if n % 20000 == 0:
                    prog.send(n, 0, "index %s titles" % fmt_count(n))
    os.replace(tmp_txt, titles_path)
    try:
        os.remove(gz_path)
    except OSError:
        pass
    return n


def prefetch_all_languages(dest_dir, prog, per_lang=400):
    prog.send(0, 0, "all languages, popular titles only", force=True)
    seen = set()
    lines = []
    for i, lang in enumerate(MAJOR_LANGS):
        chunk = _pageview_top(lang, per_lang, prog)
        for title in chunk:
            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)
            lines.append(title)
        prog.send(i + 1, len(MAJOR_LANGS), f"{lang} +{len(chunk)}  total {fmt_count(len(lines))}", force=True)
    path = os.path.join(dest_dir, "titles.txt")
    _write_titles(path, lines)
    return len(lines)


def prefetch_titles(lang: str, dest_dir: str, progress=None, scope="all_titles") -> dict:
    lang = (lang or "en").strip().lower() or "en"
    scope = (scope or "all_titles").strip().lower()
    os.makedirs(dest_dir, exist_ok=True)
    prog = _Progress(progress)

    if scope == "live":
        n = 0
    elif scope == "top10000":
        n = prefetch_top(lang, dest_dir, prog)
    elif scope == "all_languages":
        lang = "*"
        n = prefetch_all_languages(dest_dir, prog)
    else:
        n = prefetch_all_titles(lang, dest_dir, prog)

    write_config(
        dest_dir,
        lang,
        "prefetch",
        {"scope": scope, "titles": n, "text_only": True},
    )
    prog.send(n, n, "ready %s titles" % fmt_count(n), force=True)
    return {"lang": lang, "titles": n, "path": dest_dir, "scope": scope}
