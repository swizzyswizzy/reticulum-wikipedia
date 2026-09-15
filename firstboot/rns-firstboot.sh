#!/bin/bash
set +e
FLAG=/var/lib/rns/.firstboot-done
[ -f /etc/rns-repo ] && . /etc/rns-repo
REPO="${RNS_GW_REPO:-git@github.com:swizzyswizzy/reticulum-wikipedia.git}"
REPO=$(echo "$REPO" | sed -E 's#https://github.com/([^/]+)/([^/.]+)(\.git)?/?$#git@github.com:\1/\2.git#')
[ -f "$FLAG" ] && exit 0

export DEBIAN_FRONTEND=noninteractive
timedatectl set-ntp true >/dev/null 2>&1 || true
HDR=$(wget -qSO- --timeout=8 http://1.1.1.1 2>&1 | awk 'BEGIN{IGNORECASE=1} /^  Date:/{ $1=""; print substr($0,2); exit}')
[ -n "$HDR" ] && date -u -s "$HDR" >/dev/null 2>&1
apt-get update -qq || apt-get -o Acquire::Check-Valid-Until=false update -qq || true
apt-get install -y -qq --fix-missing wget ca-certificates git openssh-client vim || true

mkdir -p /root/.ssh /var/lib/rns
chmod 700 /root/.ssh
KEY=/root/.ssh/github
for src in /boot/firmware/rns_deploy_key /boot/rns_deploy_key; do
  [ -s "$src" ] || continue
  grep -q "PRIVATE KEY" "$src" || continue
  tr -d '\r' < "$src" > "$KEY"
  chmod 600 "$KEY"
done
ssh-keyscan -t ed25519,rsa github.com > /root/.ssh/known_hosts 2>/dev/null || true
printf '%s\n' 'Host github.com' '  User git' '  IdentityFile /root/.ssh/github' '  IdentitiesOnly yes' > /root/.ssh/config
chmod 600 /root/.ssh/config
printf '%s\n' "RNS_GW_REPO=$REPO" > /etc/rns-repo

if git clone --depth 1 "$REPO" /tmp/rns-src || git clone --depth 1 -b main "$REPO" /tmp/rns-src; then
  bash /tmp/rns-src/install.sh "$REPO"
else
  echo "clone failed: $REPO" >> /var/lib/rns/install.log
  exit 1
fi
touch "$FLAG"
