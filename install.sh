#!/bin/bash
# Install on the Raspberry Pi. Run as root.
set -e
[ -f /etc/rns-repo ] && . /etc/rns-repo
REPO="${1:-${RNS_GW_REPO:-https://github.com/swizzyswizzy/reticulum-wikipedia.git}}"
DEST=/opt/reticulum-wikipedia
HOME_RNS=/var/lib/rns
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true

if [ "$(id -u)" -ne 0 ]; then
  echo "Run: sudo bash install.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
timedatectl set-ntp true >/dev/null 2>&1 || true
systemctl start systemd-timesyncd >/dev/null 2>&1 || true
HDR=$(wget -qSO- --timeout=8 http://1.1.1.1 2>&1 | awk 'BEGIN{IGNORECASE=1} /^  Date:/{ $1=""; print substr($0,2); exit}')
if [ -n "$HDR" ]; then
  date -u -s "$HDR" >/dev/null 2>&1 || date -s "$HDR" >/dev/null 2>&1 || true
fi
apt-get update -qq || apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update -qq || true
apt-get install -y -qq --fix-missing git openssh-client python3 openssl ca-certificates vim avahi-daemon || true
command -v git >/dev/null || { echo "git missing"; exit 1; }

REPO=$(echo "$REPO" | sed -E 's#git@github.com:([^/]+)/([^ ]+)#https://github.com/\1/\2#')
printf '%s\n' "RNS_GW_REPO=$REPO" > /etc/rns-repo
echo "git $REPO"

clone_repo() {
  local dest="$1"
  echo "clone $REPO -> $dest"
  rm -rf "$dest"
  git clone --depth 1 "$REPO" "$dest" && return 0
  git clone --depth 1 -b main "$REPO" "$dest" && return 0
  git clone --depth 1 -b master "$REPO" "$dest" && return 0
  echo "git clone failed for $REPO"
  return 1
}

SRC="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SRC/app.py" ]; then
  mkdir -p "$DEST"
  if [ "$SRC" != "$DEST" ]; then
    cp -a "$SRC"/. "$DEST/"
  fi
  if [ -d "$DEST/.git" ]; then
    git -C "$DEST" remote set-url origin "$REPO" || true
  fi
  chmod +x "$DEST/start.sh" 2>/dev/null || true
elif [ -d "$DEST/.git" ]; then
  git -C "$DEST" remote set-url origin "$REPO" || true
  git -C "$DEST" pull --ff-only || git -C "$DEST" fetch --depth 1 origin && git -C "$DEST" reset --hard FETCH_HEAD || true
else
  clone_repo "$DEST"
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
  apt-get install -y -qq python3-pip || true
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  python3 -m ensurepip --upgrade --break-system-packages || true
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  wget -q -O /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
  python3 /tmp/get-pip.py --break-system-packages
fi
if [ -f "$DEST/requirements.txt" ]; then
  python3 -m pip install --break-system-packages -q -r "$DEST/requirements.txt" || true
else
  python3 -m pip install --break-system-packages -q rns lxmf || true
fi

id rns >/dev/null 2>&1 || useradd --system --home "$HOME_RNS" --create-home --shell /usr/sbin/nologin rns
mkdir -p "$HOME_RNS" "$HOME_RNS/.reticulum" "$HOME_RNS/.reticulum-gateway"
chown -R rns:rns "$HOME_RNS"

UNIT_DIR="$DEST/systemd"
if [ ! -d "$UNIT_DIR" ]; then
  UNIT_DIR="$DEST/firstboot/systemd"
fi
if [ -f "$UNIT_DIR/reticulum.avahi.service" ]; then
  mkdir -p /etc/avahi/services
  cp "$UNIT_DIR/reticulum.avahi.service" /etc/avahi/services/reticulum.service
  systemctl enable --now avahi-daemon 2>/dev/null || true
  systemctl restart avahi-daemon 2>/dev/null || true
  echo "mDNS: _reticulum._tcp :4242"
fi
if [ -f "$UNIT_DIR/reticulum-wikipedia.service" ]; then
  cp "$UNIT_DIR/reticulum-wikipedia.service" /etc/systemd/system/reticulum-wikipedia.service
  ln -sfn reticulum-wikipedia.service /etc/systemd/system/reticulum.service
fi
if [ -f "$UNIT_DIR/reticulum-gateway.service" ]; then
  cp "$UNIT_DIR/reticulum-gateway.service" /etc/systemd/system/reticulum-gateway.service
fi
if [ -f "$UNIT_DIR/rns-update.sh" ]; then
  install -m 755 "$UNIT_DIR/rns-update.sh" /usr/local/sbin/rns-update.sh
fi
if [ -f "$UNIT_DIR/reticulum-wikipedia-update.service" ]; then
  cp "$UNIT_DIR/reticulum-wikipedia-update.service" /etc/systemd/system/
  cp "$UNIT_DIR/reticulum-wikipedia-update.timer" /etc/systemd/system/
fi
if [ -f "$UNIT_DIR/reticulum-gateway-update.service" ]; then
  cp "$UNIT_DIR/reticulum-gateway-update.service" /etc/systemd/system/
  cp "$UNIT_DIR/reticulum-gateway-update.timer" /etc/systemd/system/
fi
# wiki_data from the SD boot partition
mkdir -p /var/lib/rns/wiki_data
for src in /boot/firmware/wiki_data /boot/wiki_data "$SRC/wiki_data"; do
  [ -d "$src" ] || continue
  cp -a "$src"/. /var/lib/rns/wiki_data/ 2>/dev/null || true
done
chown -R rns:rns /var/lib/rns/wiki_data 2>/dev/null || true
systemctl daemon-reload
systemctl reset-failed reticulum-wikipedia 2>/dev/null || true
systemctl reset-failed reticulum-gateway 2>/dev/null || true
systemctl enable reticulum-wikipedia 2>/dev/null || systemctl enable reticulum-gateway
if [ "${RNS_AUTO_UPDATE:-1}" = "0" ]; then
  systemctl disable --now reticulum-wikipedia-update.timer 2>/dev/null || true
  systemctl disable --now reticulum-gateway-update.timer 2>/dev/null || true
  echo "auto-update: disabled"
else
  systemctl enable --now reticulum-wikipedia-update.timer 2>/dev/null || systemctl enable --now reticulum-gateway-update.timer
  echo "auto-update: enabled (every 1 min)"
fi
date "+%Y-%m-%d %H:%M:%S" > "$HOME_RNS/updated_at" || true

LOG="$HOME_RNS/install.log"
mkdir -p "$HOME_RNS"
wait_service() {
  deadline=$(( $(date +%s) + 300 ))
  n=0
  while [ "$(date +%s)" -lt "$deadline" ]; do
    n=$((n + 1))
    echo "[$(date -Iseconds)] restart #$n" | tee -a "$LOG"
    systemctl reset-failed reticulum-wikipedia 2>/dev/null || true
    systemctl reset-failed reticulum-gateway 2>/dev/null || true
    systemctl restart reticulum-wikipedia >>"$LOG" 2>&1 || systemctl start reticulum-wikipedia >>"$LOG" 2>&1 || \
      systemctl restart reticulum-gateway >>"$LOG" 2>&1 || systemctl start reticulum-gateway >>"$LOG" 2>&1 || true
    sleep 3
    if systemctl is-active --quiet reticulum-wikipedia || systemctl is-active --quiet reticulum-gateway; then
      echo "[$(date -Iseconds)] service active after $n attempt(s)" | tee -a "$LOG"
      return 0
    fi
    echo "[$(date -Iseconds)] still dead" | tee -a "$LOG"
    sleep 2
  done
  echo "[$(date -Iseconds)] ERROR: reticulum-gateway did not start within 5 minutes" | tee -a "$LOG"
  systemctl --no-pager --full status reticulum-gateway >>"$LOG" 2>&1 || true
  journalctl -u reticulum-gateway -n 80 --no-pager >>"$LOG" 2>&1 || true
  return 1
}

if ! wait_service; then
  echo "Error: service did not start. Log: $LOG"
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Done. Panel: http://${IP:-IP}/  and  https://${IP:-IP}/"
echo "IP written to $HOME_RNS/ip.txt"
