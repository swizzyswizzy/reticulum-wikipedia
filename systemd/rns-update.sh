#!/bin/bash
set +e
DEST=/opt/reticulum-wikipedia
LOG=/var/lib/rns/update.log
mkdir -p /var/lib/rns
echo "[$(date '+%Y-%m-%d %H:%M:%S')] start" >> "$LOG"

[ -f /etc/rns-repo ] && . /etc/rns-repo
REPO="${RNS_GW_REPO:-git@github.com:swizzyswizzy/reticulum-wikipedia.git}"
REPO=$(echo "$REPO" | sed -E 's#https://github.com/([^/]+)/([^/.]+)(\.git)?/?$#git@github.com:\1/\2.git#')
printf '%s\n' "RNS_GW_REPO=$REPO" > /etc/rns-repo

export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true
export SSH_AUTH_SOCK=
export GIT_CONFIG_NOSYSTEM=1
git config --global --unset-all credential.helper 2>/dev/null
git config --system --unset-all credential.helper 2>/dev/null
git config --global --unset-all url.https://github.com/.insteadof 2>/dev/null
git config --global --unset-all url.https://github.com/.insteadOf 2>/dev/null
git config --global url.git@github.com:.insteadOf https://github.com/

mkdir -p /root/.ssh
chmod 700 /root/.ssh
KEY=/root/.ssh/github
if [ ! -s "$KEY" ]; then
  for src in /boot/firmware/rns_deploy_key /boot/rns_deploy_key /root/.ssh/id_ed25519 /root/.ssh/id_rsa; do
    [ -s "$src" ] && grep -q "PRIVATE KEY" "$src" || continue
    tr -d '\r' < "$src" > "$KEY"
    chmod 600 "$KEY"
    break
  done
fi
ssh-keyscan -t ed25519,rsa github.com > /root/.ssh/known_hosts 2>/dev/null || true
printf '%s\n' 'Host github.com' '  User git' '  IdentityFile /root/.ssh/github' '  IdentitiesOnly yes' > /root/.ssh/config
chmod 600 /root/.ssh/config /root/.ssh/github 2>/dev/null || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] $REPO" >> "$LOG"

if [ ! -d "$DEST/.git" ]; then
  rm -rf "$DEST"
  git clone --depth 1 "$REPO" "$DEST" >>"$LOG" 2>&1 || git clone --depth 1 -b main "$REPO" "$DEST" >>"$LOG" 2>&1
fi

cd "$DEST" || { echo "missing $DEST" >> "$LOG"; exit 1; }
git remote set-url origin "$REPO"
echo "origin $(git remote get-url origin)" | tee -a "$LOG"

old=$(git rev-parse HEAD 2>/dev/null)
git -c "url.git@github.com:.insteadOf=https://github.com/" fetch --depth 1 origin >>"$LOG" 2>&1
git reset --hard FETCH_HEAD >>"$LOG" 2>&1 || git reset --hard origin/HEAD >>"$LOG" 2>&1
new=$(git rev-parse HEAD 2>/dev/null)

if [ -z "$new" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: git failed" >> "$LOG"
  exit 1
fi
if [ "$old" = "$new" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] no change ($old)" >> "$LOG"
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] $old -> $new" >> "$LOG"
date "+%Y-%m-%d %H:%M:%S" > /var/lib/rns/updated_at
[ -f "$DEST/requirements.txt" ] && python3 -m pip install --break-system-packages -q -r "$DEST/requirements.txt" >>"$LOG" 2>&1
if [ -f "$DEST/systemd/reticulum-wikipedia.service" ]; then
  cp "$DEST/systemd/reticulum-wikipedia.service" /etc/systemd/system/
fi
if [ -f "$DEST/systemd/reticulum-gateway.service" ]; then
  cp "$DEST/systemd/reticulum-gateway.service" /etc/systemd/system/
fi
if [ -f "$DEST/systemd/reticulum-wikipedia-update.timer" ]; then
  cp "$DEST/systemd/reticulum-wikipedia-update.service" /etc/systemd/system/
  cp "$DEST/systemd/reticulum-wikipedia-update.timer" /etc/systemd/system/
fi
if [ -f "$DEST/systemd/reticulum-gateway-update.timer" ]; then
  cp "$DEST/systemd/reticulum-gateway-update.service" /etc/systemd/system/
  cp "$DEST/systemd/reticulum-gateway-update.timer" /etc/systemd/system/
fi
install -m 755 "$DEST/systemd/rns-update.sh" /usr/local/sbin/rns-update.sh
systemctl daemon-reload
systemctl enable --now reticulum-wikipedia-update.timer >>"$LOG" 2>&1 || systemctl enable --now reticulum-gateway-update.timer >>"$LOG" 2>&1
systemctl restart reticulum-wikipedia >>"$LOG" 2>&1 || systemctl restart reticulum-gateway >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] gateway restarted" >> "$LOG"
