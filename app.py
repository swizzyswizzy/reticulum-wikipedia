#!/usr/bin/env python3
"""Lounge host: load services/*/main.py and serve them."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("LOUNGE_PORT", "80"))
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
SERVICES_DIR = os.path.join(ROOT, "services")
HOME = os.path.expanduser("~/.rns-lounge")
STATE_FILE = os.path.join(HOME, "state.json")
IDENT = os.path.join(HOME, "identity")
LOG_FILE = os.path.join(HOME, "lounge.log")
log_lock = threading.Lock()

# Web can use slashes. Micron treats \ as escape, so NomadNet
# pages use the brick tile from the same asciiart.eu gallery.
BANNER_SMALL = r""" / \/ \/ \/ \/ \/ \/ \
 \ /\ /\ /\ /\ /\ /\ /
 :: lounge ::"""

BANNER_MICRON = (
    "_|___|___|___|___|___|___|___|\n"
    "___|___|___|___|___|___|___|__\n"
    "_|___|___|___|___|___|___|___|"
)


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg)
    print("[lounge] " + str(msg), flush=True)
    try:
        os.makedirs(HOME, exist_ok=True)
        with log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


class App:
    def __init__(self):
        self.state = {"alias": socket.gethostname() or "anon"}
        self.identity = None
        self.rns = None
        self.dests = {}
        self._announce_hooks = []
        self.services = []
        os.makedirs(HOME, exist_ok=True)
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                self.state.update(json.load(fh) or {})
        except Exception:
            pass

    def save(self):
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)

    def esc(self, s):
        return (
            str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def shell(self, title, body):
        links = ['<a class="btn btn-nav" href="/">home</a>']
        links.extend(
            f'<a class="btn btn-nav" href="{self.esc(s["path"])}">{self.esc(s["id"])}</a>'
            for s in self.services
        )
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{self.esc(title)}</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/htmx.min.js" defer></script>
</head>
<body>
<header>
<pre class="brand">{BANNER_SMALL}</pre>
<nav>{"".join(links)}</nav>
</header>
{body}
</body>
</html>"""

    def card(self, href, title, desc=""):
        t = self.esc(title)
        d = self.esc(desc)
        return (
            f'<a class="card" href="{self.esc(href)}">'
            f'<span class="card-title">{t}</span>'
            f'<span class="card-desc">{d}</span>'
            f'<span class="btn btn-card">open</span>'
            f"</a>"
        )

    def destination(self, aspect):
        if aspect in self.dests:
            return self.dests[aspect]
        if self.identity is None:
            return None
        import RNS
        dest = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            "lounge",
            aspect,
        )
        try:
            dest.announce(app_data=(self.state.get("alias") or "lounge").encode("utf-8"))
        except Exception:
            pass
        self.dests[aspect] = dest
        return dest

    def on_announce(self, fn):
        self._announce_hooks.append(fn)


app = App()


def load_services():
    app.services = []
    if not os.path.isdir(SERVICES_DIR):
        return
    for name in sorted(os.listdir(SERVICES_DIR)):
        folder = os.path.join(SERVICES_DIR, name)
        main_py = os.path.join(folder, "main.py")
        if not os.path.isdir(folder) or not os.path.isfile(main_py):
            continue
        spec = importlib.util.spec_from_file_location(f"svc_{name}", main_py)
        if spec is None or spec.loader is None:
            print(f"[lounge] skip {name}: bad module")
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            print(f"[lounge] skip {name}: {exc}")
            continue
        svc = {
            "id": name,
            "title": getattr(mod, "title", name),
            "description": getattr(mod, "description", ""),
            "path": getattr(mod, "path", "/" + name),
            "mod": mod,
        }
        app.services.append(svc)
        print(f"[lounge] service {svc['title']} → {svc['path']}")


def setup_services():
    for svc in app.services:
        fn = getattr(svc["mod"], "setup", None)
        if callable(fn):
            try:
                fn(app)
            except Exception as exc:
                print(f"[lounge] setup {svc['id']}: {exc}")


def wifi_ifaces():
    names = []
    base = "/sys/class/net"
    if not os.path.isdir(base):
        return names
    for name in sorted(os.listdir(base)):
        if name == "lo":
            continue
        if os.path.isdir(os.path.join(base, name, "wireless")):
            names.append(name)
    return names


def wait_wifi(seconds=45):
    t0 = time.time()
    while time.time() - t0 < seconds:
        names = wifi_ifaces()
        if names:
            return names
        log("waiting for wifi iface")
        time.sleep(2)
    return wifi_ifaces()


def ensure_rns_config():
    cfg = os.path.expanduser("~/.reticulum/config")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    wifi = wait_wifi()
    if wifi:
        devices = ", ".join(wifi)
        ignore = "eth0, end0, enp1s0, usb0, lo"
        extra = f"    devices = {devices}\n    ignored_devices = {ignore}\n"
        log("wifi ifaces: " + devices)
    else:
        extra = "    ignored_devices = eth0, end0, enp1s0, usb0, lo\n"
        log("WARNING: no wifi iface in /sys/class/net/*/wireless")
    text = (
        "[reticulum]\n"
        "  enable_transport = Yes\n"
        "  share_instance = No\n"
        "\n"
        "[log]\n"
        "  loglevel = 5\n"
        "\n"
        "[interfaces]\n"
        "  [[WiFi]]\n"
        "    type = AutoInterface\n"
        "    enabled = yes\n"
        "    group_id = reticulum\n"
        + extra +
        "  [[LAN UDP]]\n"
        "    type = UDPInterface\n"
        "    enabled = yes\n"
        "    listen_ip = 0.0.0.0\n"
        "    listen_port = 4242\n"
        "    forward_ip = 255.255.255.255\n"
        "    forward_port = 4242\n"
        "  [[TCP Server]]\n"
        "    type = TCPServerInterface\n"
        "    enabled = yes\n"
        "    listen_ip = 0.0.0.0\n"
        "    listen_port = 4242\n"
    )
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(text)
    log("wrote " + cfg)


def dump_ifaces():
    try:
        import RNS
        items = list(getattr(RNS.Transport, "interfaces", []) or [])
        if not items:
            log("RNS interfaces: (none)")
            return
        for iface in items:
            log("RNS iface " + str(iface))
    except Exception as exc:
        log("RNS iface list fail " + str(exc))


def do_announce():
    name = (app.state.get("alias") or socket.gethostname() or "lounge").encode("utf-8")
    if not app.dests:
        log("announce skipped: no destinations")
        return
    for key, dest in list(app.dests.items()):
        if key == "chat":
            continue
        try:
            dest.announce(app_data=name)
            log("announce ok " + key + " " + dest.hash.hex() + " name=" + name.decode("utf-8", "replace"))
        except Exception as exc:
            log("announce FAIL " + key + " " + str(exc))


def announce_forever():
    time.sleep(8)
    dump_ifaces()
    do_announce()
    while True:
        time.sleep(30)
        dump_ifaces()
        do_announce()


def start_rns():
    try:
        import RNS
    except Exception as exc:
        log("RNS missing (" + str(exc) + ")")
        setup_services()
        return
    try:
        RNS.loglevel = 5
        ensure_rns_config()
        app.rns = RNS.Reticulum()
        if os.path.isfile(IDENT):
            app.identity = RNS.Identity.from_file(IDENT)
            log("identity loaded " + IDENT)
        else:
            app.identity = RNS.Identity()
            app.identity.to_file(IDENT)
            log("identity created " + IDENT)
        log("identity hash " + app.identity.hash.hex())
        app.destination("dir")
        log("dest lounge.dir " + app.dests["dir"].hash.hex())
        nn = RNS.Destination(
            app.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            "nomadnetwork",
            "node",
        )
        name = (app.state.get("alias") or socket.gethostname() or "lounge").encode("utf-8")
        try:
            nn.set_default_app_data(name)
        except Exception as exc:
            log("set_default_app_data " + str(exc))
        app.dests["nomadnetwork"] = nn
        log("dest nomadnetwork.node " + nn.hash.hex())

        def serve_page(path, data, request_id, link_id, remote_identity, requested_at):
            return page_micron(path, data, remote_identity).encode("utf-8")

        def on_link(link):
            log("link up " + str(link))

        try:
            nn.set_link_established_callback(on_link)
        except Exception as exc:
            log("link callback " + str(exc))
        paths = ["/page/index.mu", "/page/index"]
        for svc in app.services:
            sid = svc["id"]
            paths.extend(["/page/" + sid + ".mu", "/page/" + sid])
            extra = getattr(svc["mod"], "micron_paths", None) or []
            for p in extra:
                p = str(p)
                if p not in paths:
                    paths.append(p)
        for path in paths:
            nn.register_request_handler(
                path,
                response_generator=serve_page,
                allow=RNS.Destination.ALLOW_ALL,
            )
            log("handler " + path)
        dump_ifaces()

        class Handler:
            aspect_filter = None

            def received_announce(self, destination_hash, announced_identity, app_data, *args, **kwargs):
                preview = app_data[:40] if app_data else b""
                log("heard announce " + bytes(destination_hash).hex() + " data=" + repr(preview))
                for fn in list(app._announce_hooks):
                    try:
                        fn(app_data, destination_hash)
                    except TypeError:
                        fn(app_data)
                    except Exception as exc:
                        log("announce hook " + str(exc))

        RNS.Transport.register_announce_handler(Handler())
        threading.Thread(target=announce_forever, daemon=True).start()
        log("RNS up, announce loop started")
    except Exception as exc:
        log("RNS off (" + str(exc) + ")")
    setup_services()


def dispatch(method, path, query=None, form=None):
    for svc in app.services:
        prefix = svc["path"].rstrip("/") or "/"
        if path != prefix and not path.startswith(prefix + "/"):
            continue
        fn = getattr(svc["mod"], method, None)
        if not callable(fn):
            continue
        try:
            if method == "get":
                out = fn(path, query or {})
            else:
                out = fn(path, form or {})
        except Exception as exc:
            print(f"[lounge] {svc['id']} {method} {exc}")
            return 500, "text/plain; charset=utf-8", str(exc)
        if out is None:
            continue
        if isinstance(out, tuple):
            return out
        return 200, "text/html; charset=utf-8", out
    return None


def fields_from(data):
    out = {}
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        name = str(key)
        if name.startswith("field_"):
            name = name[6:]
        elif name.startswith("var_"):
            name = name[4:]
        if isinstance(val, (list, tuple)):
            val = val[0] if val else ""
        elif isinstance(val, bytes):
            val = val.decode("utf-8", "replace")
        if val is None or isinstance(val, bool):
            out[name] = ""
        else:
            out[name] = str(val)
    return out


def page_slug(path):
    name = str(path or "").rstrip("/").split("/")[-1]
    if name.endswith(".mu"):
        name = name[:-3]
    return name


def page_micron(path, data, remote_identity):
    host = socket.gethostname()
    peer = remote_identity.hash.hex() if remote_identity else "?"
    fields = fields_from(data)
    log("page " + str(path) + " from " + peer + " fields=" + str(list(fields.keys())) + " q=" + repr(fields.get("q") or fields.get("search") or ""))
    raw = (path or "").rstrip("/")
    if raw in ("/page/index.mu", "/page/index", "/page"):
        return micron_index(host, fields)
    slug = page_slug(raw)
    for svc in app.services:
        names = {svc["id"]}
        for extra in getattr(svc["mod"], "micron_paths", None) or []:
            names.add(page_slug(extra))
        if slug not in names and not raw.startswith("/page/" + svc["id"]):
            continue
        fn = getattr(svc["mod"], "micron", None)
        if callable(fn):
            try:
                try:
                    return fn(raw, fields, remote_identity)
                except TypeError:
                    return fn(raw, fields)
            except Exception as exc:
                log("micron " + svc["id"] + " " + str(exc))
                log(traceback.format_exc())
                return f"> {host}\n\nService error: {exc}\n"
        return (
            f"> {svc['title']}\n"
            f"-\n"
            f"{svc['description']}\n"
            f"\n`B5a2`F000`[  home  `:/page/index.mu]`f`b\n"
        )
    return (
        f"> {host}\n\nNo page at {path}\n"
        f"`B5a2`F000`[  home  `:/page/index.mu]`f`b\n"
    )


def _mu_url(dest):
    dest = str(dest or "").strip()
    if dest.startswith("/"):
        dest = ":" + dest
    return dest


def _btn(label, dest, fields="", wide=False):
    pad = "        " if wide else "  "
    text = pad + label.strip() + pad
    dest = _mu_url(dest)
    if fields:
        link = f"`[{text}`{dest}`{fields}]"
    else:
        link = f"`[{text}`{dest}]"
    return f"`B5a2`F000`!{link}`!`f`b"


def micron_index(host, fields=None):
    fields = fields or {}
    for svc in app.services:
        if not getattr(svc["mod"], "is_home", False):
            continue
        fn = getattr(svc["mod"], "micron", None)
        if callable(fn):
            try:
                return fn("/page/index.mu", fields)
            except TypeError:
                try:
                    return fn("/page/index.mu", fields, None)
                except TypeError:
                    return fn("/page/index.mu", {})
    alias = app.state.get("alias") or host
    ident = ""
    nn = app.dests.get("nomadnetwork")
    if nn is not None:
        ident = nn.hash.hex()[:12]
    buttons = [
        _btn(svc["title"] or svc["id"], "/page/" + svc["id"] + ".mu", wide=True)
        for svc in app.services
    ]
    if not buttons:
        buttons = ["`F888 no services yet`f"]
    if host and host != alias:
        sub = host + (("  " + ident) if ident else "")
    else:
        sub = ident
    lines = [
        "#!c=0",
        "#!bg=000",
        "#!fg=a6f",
        "`c",
        f"`! {alias} `!",
    ]
    if sub:
        lines.append(f"`F888 {sub}`f")
    lines.extend(
        [
            "",
            "",
            "  ".join(buttons),
            "",
            "",
            "",
            "",
            "",
            "`a`f`b",
        ]
    )
    return "\n".join(lines) + "\n"


def page_home():
    for svc in app.services:
        if not getattr(svc["mod"], "is_home", False):
            continue
        fn = getattr(svc["mod"], "get", None)
        if not callable(fn):
            continue
        try:
            out = fn("/", {})
        except Exception as exc:
            log("home " + svc["id"] + " " + str(exc))
            break
        if isinstance(out, tuple):
            return out[2]
        if out:
            return out
    cards = [app.card(svc["path"], svc["title"], svc["description"]) for svc in app.services]
    body = "".join(cards) or '<p class="muted">no services</p>'
    host = socket.gethostname()
    ident = ""
    nn = app.dests.get("nomadnetwork")
    if nn is not None:
        ident = nn.hash.hex()[:12]
    meta = host
    if ident:
        meta += "  ·  " + ident
    return app.shell(
        "Lounge",
        f"""<main>
<pre class="hero">{BANNER_SMALL}</pre>
<p class="meta">{app.esc(meta)}</p>
<section class="cards">{body}</section>
</main>""",
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[http]", fmt % args)

    def send_bytes(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: (v[0] if v else "") for k, v in parse_qs(parsed.query).items()}
        if path in ("/", "/index.html"):
            self.send_bytes(200, "text/html; charset=utf-8", page_home())
            return
        if path == "/api/log":
            try:
                with open(LOG_FILE, encoding="utf-8") as fh:
                    body = "".join(fh.readlines()[-80:])
            except Exception:
                body = "(no log yet)"
            self.send_bytes(200, "text/plain; charset=utf-8", body)
            return
        name = os.path.basename(path)
        if name in ("style.css", "htmx.min.js"):
            full = os.path.join(WEB, name)
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = "text/css" if name.endswith(".css") else "application/javascript"
            self.send_bytes(200, ctype, body)
            return
        hit = dispatch("get", path, query=query)
        if hit:
            self.send_bytes(*hit)
            return
        self.send_bytes(404, "text/plain", "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", errors="replace") if n else ""
        form = {k: (v[0] if v else "") for k, v in parse_qs(raw).items()}
        hit = dispatch("post", parsed.path, form=form)
        if hit:
            self.send_bytes(*hit)
            return
        self.send_bytes(404, "text/plain", "not found")


def main():
    load_services()
    start_rns()
    http = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log("http://0.0.0.0:" + str(PORT) + "/")
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
