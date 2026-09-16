#!/bin/bash
# Run on the Pi as root:  sudo bash fix-pi.sh
set -e
DEST=/opt/reticulum-wikipedia
HTTPS="https://github.com/swizzyswizzy/reticulum-wikipedia.git"

echo "$HTTPS" 
printf '%s\n' "RNS_GW_REPO=$HTTPS" > /etc/rns-repo
git config --global --unset-all url.git@github.com:.insteadof 2>/dev/null || true
git config --global --unset-all url.git@github.com:.insteadOf 2>/dev/null || true
git config --system --unset-all url.git@github.com:.insteadof 2>/dev/null || true
git config --system --unset-all url.git@github.com:.insteadOf 2>/dev/null || true

if [ -d "$DEST/.git" ]; then
  git -C "$DEST" remote set-url origin "$HTTPS"
  git -C "$DEST" fetch --depth 1 origin || true
  git -C "$DEST" reset --hard FETCH_HEAD || git -C "$DEST" reset --hard origin/main || true
fi

install -m 755 /dev/stdin /usr/local/sbin/rns-update.sh << 'UPD'
#!/bin/bash
set +e
DEST=/opt/reticulum-wikipedia
LOG=/var/lib/rns/update.log
mkdir -p /var/lib/rns
echo "[$(date '+%Y-%m-%d %H:%M:%S')] start" >> "$LOG"
REPO="https://github.com/swizzyswizzy/reticulum-wikipedia.git"
[ -f /etc/rns-repo ] && . /etc/rns-repo
REPO=$(echo "${RNS_GW_REPO:-$REPO}" | sed -E 's#git@github.com:([^/]+)/([^ ]+)#https://github.com/\1/\2#')
printf '%s\n' "RNS_GW_REPO=$REPO" > /etc/rns-repo
echo "[$(date '+%Y-%m-%d %H:%M:%S')] $REPO" >> "$LOG"
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/true
git config --global --unset-all url.git@github.com:.insteadOf 2>/dev/null
cd "$DEST" || exit 1
git remote set-url origin "$REPO"
old=$(git rev-parse HEAD 2>/dev/null)
if git fetch --depth 1 origin >>"$LOG" 2>&1; then
  git reset --hard FETCH_HEAD >>"$LOG" 2>&1
fi
new=$(git rev-parse HEAD 2>/dev/null)
echo "origin $(git remote get-url origin)" >> "$LOG"
if [ -n "$new" ] && [ "$old" != "$new" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $old -> $new" >> "$LOG"
  systemctl restart reticulum-wikipedia >>"$LOG" 2>&1
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] no change ($new)" >> "$LOG"
fi
UPD

python3 - << 'PY'
from pathlib import Path

app = Path("/opt/reticulum-wikipedia/app.py")
t = app.read_text()
t = t.replace("def micron_index(host):", "def micron_index(host, fields=None):")
if "def micron_index(host, fields=None):" in t and "fields = fields or {}" not in t.split("def micron_index", 1)[-1][:180]:
    t = t.replace(
        "def micron_index(host, fields=None):\n",
        "def micron_index(host, fields=None):\n    fields = fields or {}\n",
        1,
    )
t = t.replace("return micron_index(host)", "return micron_index(host, fields)")
t = t.replace('return fn("/page/index.mu", {})', 'return fn("/page/index.mu", fields)')
t = t.replace("return fn('/page/index.mu', {})", "return fn('/page/index.mu', fields)")
app.write_text(t)
print("app.py", "micron_index(host, fields)" in t)

wiki = Path("/opt/reticulum-wikipedia/services/wikipedia/main.py")
w = wiki.read_text()
old = """    q = (fields.get("q") or fields.get("search") or fields.get("title") or "").strip()
    name = _safe_name(fields.get("title") or "")
    if name and not q:"""
new = """    raw = (path or "").rstrip("/")
    slug = raw.rsplit("/", 1)[-1].replace(".mu", "")
    q = (fields.get("q") or fields.get("search") or "").strip()
    name = _safe_name(fields.get("title") or fields.get("t") or "")
    if slug == "wiki":
        name = name or _safe_name(q)
    if name and (slug == "wiki" or not q):"""
if old in w:
    w = w.replace(old, new, 1)
    wiki.write_text(w)
    print("main.py article branch patched")
elif "if slug == \"wiki\":" in w:
    print("main.py already patched")
else:
    print("main.py pattern missing — pull from GitHub after you push")
PY

systemctl restart reticulum-wikipedia
echo "origin: $(git -C $DEST remote get-url origin 2>/dev/null)"
echo "head:   $(git -C $DEST rev-parse --short HEAD 2>/dev/null)"
echo "done"
