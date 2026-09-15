"""Download a Wikipedia title dump into wiki_data/."""

from __future__ import annotations

import gzip
import json
import os
import urllib.request

UA = "reticulum-wikipedia/0.1 (title prefetch)"
DUMP = "https://dumps.wikimedia.org/{lang}wiki/latest/{lang}wiki-latest-all-titles-in-ns0.gz"


def dump_url(lang: str) -> str:
    lang = (lang or "en").strip().lower()
    return DUMP.format(lang=lang)


def write_config(folder: str, lang: str, fetch: str, extra=None) -> str:
    os.makedirs(folder, exist_ok=True)
    cfg = {"lang": lang, "fetch": fetch, "project": "reticulum-wikipedia"}
    if extra:
        cfg.update(extra)
    path = os.path.join(folder, "config.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def prefetch_titles(lang: str, dest_dir: str, progress=None) -> dict:
    """Download all-titles-in-ns0 for lang into dest_dir.

    Writes titles.txt (plain, one title per line) and config.json.
    progress(done, total, message) is optional.
    """
    lang = (lang or "en").strip().lower() or "en"
    os.makedirs(dest_dir, exist_ok=True)
    url = dump_url(lang)
    gz_path = os.path.join(dest_dir, f"{lang}wiki-latest-all-titles-in-ns0.gz")
    titles_path = os.path.join(dest_dir, "titles.txt")

    def say(done, total, msg):
        if progress:
            progress(done, total, msg)

    say(0, 0, "GET " + url)
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
                say(got, total, f"download {got} / {total or '?'}")
        os.replace(tmp, gz_path)

    say(0, 0, "unpack titles")
    n = 0
    tmp_txt = titles_path + ".part"
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as src:
        with open(tmp_txt, "w", encoding="utf-8") as out:
            for line in src:
                title = line.strip().replace("_", " ")
                if not title or title.startswith("#"):
                    continue
                out.write(title + "\n")
                n += 1
                if n % 50000 == 0:
                    say(n, 0, f"index {n} titles")
    os.replace(tmp_txt, titles_path)
    write_config(
        dest_dir,
        lang,
        "prefetch",
        {"titles": n, "dump": os.path.basename(gz_path)},
    )
    say(n, n, f"ready {n} titles")
    return {"lang": lang, "titles": n, "path": dest_dir}
