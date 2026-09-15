#!/usr/bin/env python3
"""Dopisuje Wi-Fi i firstboot na kartę nagraną Raspberry Pi Imagerem."""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import subprocess
import sys

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QFileDialog,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import prefetch

REPO_URL = "git@github.com:swizzyswizzy/reticulum-wikipedia.git"
WIZARD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(WIZARD_DIR, ".."))
WIKI_DATA = os.path.join(ROOT_DIR, "wiki_data")
WIFI_SAVE = os.path.join(WIZARD_DIR, "wifi.json")
LANG_FILE = os.path.join(WIZARD_DIR, "languages.json")
PINNED_LANGS = ("en", "pl", "de", "fr", "es", "ru", "zh", "ja", "it", "pt", "uk", "ar", "nl", "cs", "sv")

STYLE = """
QMainWindow, QWidget#root { background: #1b1e1c; color: #d7ddd4; }
QLabel { color: #d7ddd4; font-size: 13px; }
QLabel#title { color: #cfe7c4; font-size: 18px; font-weight: 700; }
QLabel#hint { color: #8b9486; font-size: 12px; }
QLabel#err { color: #e08a7a; font-size: 12px; }
QLabel#rootpw { color: #ff6b5b; font-size: 16px; font-weight: 700; }
QLabel#cred { color: #e8eee4; font-size: 13px; }
QLineEdit, QComboBox {
  background: #111411; color: #e8eee4; border: 1px solid #3a4338;
  padding: 6px 8px; selection-background-color: #3d6b46;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #7dba8a; }
QPushButton {
  background: #2a3329; color: #e8eee4; border: 1px solid #4a5547; padding: 7px 12px;
}
QPushButton:hover { border-color: #7dba8a; }
QPushButton#run {
  background: #3c6b46; border: 1px solid #7dba8a; font-weight: 700; padding: 10px 16px;
}
QTextEdit {
  background: #0e100e; color: #9cff9c; border: 1px solid #3a4338;
  font-family: ui-monospace, Consolas, monospace; font-size: 12px;
}
QFrame#box { border: 1px solid #2c332c; background: #161916; }
QProgressBar { border: 1px solid #3a4338; background: #111411; color: #cfe7c4; text-align: center; height: 16px; }
QProgressBar::chunk { background: #3c6b46; }
QRadioButton { color: #d7ddd4; spacing: 8px; }
"""


def load_languages():
    langs = []
    try:
        with open(LANG_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, list):
            langs = raw
    except Exception:
        langs = [{"code": "en", "name": "English", "local": "English"}]
    by_code = {str(item.get("code") or ""): item for item in langs if item.get("code")}
    ordered = []
    for code in PINNED_LANGS:
        if code in by_code:
            ordered.append(by_code.pop(code))
    ordered.extend(sorted(by_code.values(), key=lambda i: str(i.get("code"))))
    return ordered


def lang_label(item):
    code = item.get("code") or ""
    local = item.get("local") or item.get("name") or code
    name = item.get("name") or local
    if local and name and local != name:
        return f"{local} — {name} ({code})"
    return f"{name} ({code})"


def copy_wiki_data(src, dest, log):
    if not src or not os.path.isdir(src):
        return 0
    os.makedirs(dest, exist_ok=True)
    n = 0
    for name in os.listdir(src):
        if name.startswith("."):
            continue
        a = os.path.join(src, name)
        b = os.path.join(dest, name)
        if os.path.isdir(a):
            continue
        with open(a, "rb") as fh_in, open(b, "wb") as fh_out:
            while True:
                chunk = fh_in.read(1024 * 1024)
                if not chunk:
                    break
                fh_out.write(chunk)
        n += 1
        log("   wiki_data/" + name + " " + str(os.path.getsize(b)) + " B")
    return n


BOOT_LABELS = {"bootfs", "boot", "bootfs64"}


def _names(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def boot_join(path, name):
    want = name.lower()
    for item in _names(path):
        if item.lower() == want:
            return os.path.join(path, item)
    return os.path.join(path, name)


def is_bootfs(path):
    if not path or not os.path.isdir(path):
        return False
    names = {item.lower() for item in _names(path)}
    return "cmdline.txt" in names and "config.txt" in names


def _add_boot(found, seen, path, label=None):
    path = os.path.abspath(path) if path else ""
    if not path or path in seen or not is_bootfs(path):
        return
    seen.add(path)
    found.append({"label": label or os.path.basename(path) or path, "path": path})


def _mounts():
    rows = []
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                dev, mp, fs = parts[0], parts[1], parts[2]
                mp = mp.replace("\\040", " ").replace("\\011", "\t")
                rows.append((dev, mp, fs))
    except OSError:
        pass
    return rows


def _lsblk():
    try:
        raw = subprocess.check_output(
            ["lsblk", "-J", "-o", "NAME,LABEL,FSTYPE,MOUNTPOINT,SIZE,TYPE"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(raw or "{}")
    except Exception:
        return []
    out = []

    def walk(node, parent=""):
        name = node.get("name") or ""
        dev = "/dev/" + name
        out.append({
            "dev": dev,
            "label": (node.get("label") or "").strip(),
            "fstype": (node.get("fstype") or "").lower(),
            "mp": node.get("mountpoint") or "",
            "size": node.get("size") or "",
            "type": node.get("type") or "",
        })
        for ch in node.get("children") or []:
            walk(ch, name)

    for disk in data.get("blockdevices") or []:
        walk(disk)
    return out


def _udisks_mount(dev):
    try:
        out = subprocess.check_output(
            ["udisksctl", "mount", "-b", dev],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        return ""
    # Mounted /dev/sdb1 at /media/user/bootfs
    m = re.search(r" at (/[^\s.]+)", out)
    if m:
        return m.group(1)
    return ""


def list_bootfs():
    found = []
    seen = set()
    if os.name == "nt":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:/"
            _add_boot(found, seen, root, "bootfs " + letter + ":")
        return found

    for _dev, mp, _fs in _mounts():
        _add_boot(found, seen, mp, os.path.basename(mp) or mp)

    bases = [
        "/media",
        "/run/media",
        "/mnt",
        "/Volumes",
    ]
    uid = os.environ.get("SUDO_UID") or str(os.getuid())
    bases.append("/run/user/" + uid)
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    if user:
        bases.append("/media/" + user)
        bases.append("/run/media/" + user)
    for key in ("XDG_RUNTIME_DIR",):
        if os.environ.get(key):
            bases.append(os.environ[key])
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(base, onerror=lambda _e: None):
                lower = {n.lower() for n in filenames}
                if "cmdline.txt" in lower and "config.txt" in lower:
                    _add_boot(found, seen, dirpath)
                    dirnames.clear()
                    continue
                rel = dirpath[len(base):].count(os.sep) if dirpath.startswith(base) else 9
                if rel >= 4:
                    dirnames.clear()
        except OSError:
            pass

    for part in _lsblk():
        if part["mp"]:
            _add_boot(found, seen, part["mp"], part["label"] or part["mp"])
            continue
        fstype = part["fstype"]
        label = (part["label"] or "").lower()
        looks_boot = label in BOOT_LABELS or fstype in ("vfat", "fat32", "fat16", "msdos", "exfat")
        if not looks_boot or part["type"] not in ("part", ""):
            continue
        if label not in BOOT_LABELS and fstype not in ("vfat", "fat32", "fat16", "msdos"):
            continue
        mp = _udisks_mount(part["dev"])
        if mp:
            tag = (part["label"] or os.path.basename(mp)) + " " + part["dev"]
            _add_boot(found, seen, mp, tag)
    return found


def read_text(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if not data or b"\x00" in data[:80]:
        raise RuntimeError("uszkodzony plik: " + path)
    return data.decode("ascii", errors="replace")


def write_text(path, text):
    raw = text.replace("\r\n", "\n").encode("ascii", errors="replace")
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def patch_cmdline(text: str) -> str:
    line = " ".join(text.replace("\n", " ").split())
    line = re.sub(r"\bsystemd\.run\S*", "", line)
    line = re.sub(r"\bmodules-load=\S*", "", line)
    line = re.sub(r"\s+", " ", line).strip()
    if re.search(r"\brootwait\b", line):
        line = re.sub(r"\brootwait\b", "rootwait modules-load=dwc2,g_ether", line, count=1)
    else:
        line += " modules-load=dwc2,g_ether"
    line += " systemd.run=/boot/firmware/rns-firstboot.sh systemd.run_success_action=none systemd.run_failure_action=none"
    return re.sub(r"\s+", " ", line).strip() + "\n"


def patch_config(text: str) -> str:
    text = re.sub(r"^([ \t]*)otg_mode=", r"\1#otg_mode=", text, flags=re.M)
    if not re.search(r"^dtoverlay=dwc2\b", text, re.M):
        if re.search(r"^\[all\]", text, re.M):
            text = re.sub(r"^\[all\]", "[all]\ndtoverlay=dwc2", text, count=1, flags=re.M)
        else:
            text = text.rstrip() + "\n\n[all]\ndtoverlay=dwc2\n"
    if not text.endswith("\n"):
        text += "\n"
    return text


def known_hosts_path():
    return os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts")


def drop_known_host(name):
    name = (name or "").strip()
    if not name:
        return "empty address"
    try:
        subprocess.check_call(
            ["ssh-keygen", "-R", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return "ssh-keygen -R " + name
    except Exception:
        pass
    path = known_hosts_path()
    if not os.path.isfile(path):
        return "no known_hosts file"
    keep = []
    dropped = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            token = line.split()[0] if line.split() else ""
            if name in token.split(","):
                dropped += 1
                continue
            keep.append(line)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(keep)
    return ("removed " + str(dropped) + " line(s) for " + name) if dropped else ("no entry for " + name)


def make_hostname():
    alphabet = string.ascii_lowercase + string.digits
    return "node-" + "".join(secrets.choice(alphabet) for _ in range(6))


def make_root_pw():
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(14))


def crypt_sha512(password):
    try:
        import crypt
        return crypt.crypt(password, crypt.METHOD_SHA512)
    except Exception:
        pass
    try:
        return subprocess.check_output(["openssl", "passwd", "-6", password], text=True).strip()
    except Exception:
        return ""


def user_data(hostname, root_pw):
    return (
        "#cloud-config\n"
        f"hostname: {hostname}\n"
        f"fqdn: {hostname}\n"
        "manage_etc_hosts: true\n"
        "enable_ssh: true\n"
        "ssh_pwauth: true\n"
        "disable_root: false\n"
        "chpasswd:\n"
        "  expire: false\n"
        "  list: |\n"
        f"    root:{root_pw}\n"
        "    rtclm:reticulum\n"
        "users:\n"
        "  - name: rtclm\n"
        "    gecos: Reticulum\n"
        "    primary_group: users\n"
        "    groups: [adm, dialout, sudo, audio, video, plugdev, netdev, gpio, i2c, spi]\n"
        "    shell: /bin/bash\n"
        "    lock_passwd: false\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    plain_text_passwd: reticulum\n"
    )


def yaml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def wifi_nm(ssid, psk):
    return (
        "[connection]\n"
        "id=rns-wifi\n"
        "uuid=7c9e6679-7425-40de-944b-e07fc1f90ae7\n"
        "type=wifi\n"
        "interface-name=wlan0\n"
        "autoconnect=true\n"
        "autoconnect-priority=100\n"
        "\n"
        "[wifi]\n"
        f"ssid={ssid}\n"
        "mode=infrastructure\n"
        "\n"
        "[wifi-security]\n"
        "key-mgmt=wpa-psk\n"
        f"psk={psk}\n"
        "\n"
        "[ipv4]\n"
        "method=auto\n"
        "\n"
        "[ipv6]\n"
        "method=auto\n"
    )


def network_config(ssid, psk):
    return (
        "network:\n"
        "  version: 2\n"
        "  wifis:\n"
        "    wlan0:\n"
        "      dhcp4: true\n"
        "      regulatory-domain: \"PL\"\n"
        "      access-points:\n"
        f"        {yaml_str(ssid)}:\n"
        f"          password: {yaml_str(psk)}\n"
        "      optional: true\n"
    )


def wpa_conf(ssid, psk):
    s = ssid.replace("\\", "\\\\").replace('"', '\\"')
    p = psk.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "country=PL\n"
        "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
        "update_config=1\n"
        "\n"
        "network={\n"
        f'    ssid="{s}"\n'
        f'    psk="{p}"\n'
        "    key_mgmt=WPA-PSK\n"
        "}\n"
    )


def ssh_repo_url(url):
    url = (url or REPO_URL).strip()
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        return f"git@github.com:{m.group(1)}/{m.group(2)}.git"
    return url


def firstboot_sh(ssid, psk, hostname, root_pw, do_install=True, auto_update=False, repo_url=None, use_ssh_key=False):
    repo = ssh_repo_url(repo_url or REPO_URL).replace("'", "'\\''")
    ssid_q = ssid.replace("'", "'\\''")
    psk_q = psk.replace("'", "'\\''")
    host_q = hostname.replace("'", "'\\''")
    root_q = root_pw.replace("'", "'\\''")
    if do_install:
        install_block = f"""
mkdir -p /usr/local/sbin /etc/systemd/system /var/lib/rns
cat > /usr/local/sbin/rns-install-once.sh << 'INST'
#!/bin/bash
set +e
FLAG=/var/lib/rns/.installed
[ -f "$FLAG" ] && exit 0
mkdir -p /var/lib/rns
exec >> /var/lib/rns/install.log 2>&1
echo "install $(date)"
for i in $(seq 1 90); do
  ping -c1 -W2 1.1.1.1 && break
  ping -c1 -W2 8.8.8.8 && break
  sleep 2
done
timedatectl set-ntp true >/dev/null 2>&1 || true
systemctl start systemd-timesyncd >/dev/null 2>&1 || true
for i in $(seq 1 20); do
  timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qi yes && break
  sleep 2
done
HDR=$(wget -qSO- --timeout=8 http://1.1.1.1 2>&1 | awk 'BEGIN{{IGNORECASE=1}} /^  Date:/{{$1=""; print substr($0,2); exit}}')
if [ -n "$HDR" ]; then
  date -u -s "$HDR" >/dev/null 2>&1 || date -s "$HDR" >/dev/null 2>&1 || true
fi
echo "clock $(date -u -Iseconds)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update -qq || true
apt-get install -y -qq --fix-missing wget ca-certificates git openssh-client python3 openssl vim || true
command -v git >/dev/null || apt-get install -y git || true
REPO='{repo}'
REPO=$(echo "$REPO" | sed -E 's#https://github.com/([^/]+)/([^/.]+)(\\.git)?/?$#git@github.com:\\1/\\2.git#')
mkdir -p /root/.ssh
chmod 700 /root/.ssh
KEY=/root/.ssh/github
for src in /boot/firmware/rns_deploy_key /boot/rns_deploy_key /root/.ssh/github; do
  [ -s "$src" ] || continue
  grep -q "PRIVATE KEY" "$src" || continue
  tr -d '\\r' < "$src" > "$KEY"
  chmod 600 "$KEY"
  echo "ssh key from $src"
  break
done
ssh-keyscan -t ed25519,rsa github.com > /root/.ssh/known_hosts 2>/dev/null || true
printf '%s\\n' 'Host github.com' '  User git' '  IdentityFile /root/.ssh/github' '  IdentitiesOnly yes' > /root/.ssh/config
chmod 600 /root/.ssh/config
printf '%s\\n' "RNS_GW_REPO=$REPO" > /etc/rns-repo
echo "git $REPO"
ssh -T git@github.com 2>&1 | head -3 || true
if git clone --depth 1 "$REPO" /tmp/rns-src || git clone --depth 1 -b main "$REPO" /tmp/rns-src || git clone --depth 1 -b master "$REPO" /tmp/rns-src; then
  RNS_AUTO_UPDATE={1 if auto_update else 0} bash /tmp/rns-src/install.sh "$REPO"
else
  echo "clone failed: $REPO"
  exit 1
fi
touch "$FLAG"
INST
chmod 755 /usr/local/sbin/rns-install-once.sh
cat > /etc/systemd/system/rns-install.service << 'UNIT'
[Unit]
Description=RNS gateway install
After=network-online.target NetworkManager-wait-online.service
Wants=network-online.target
[Service]
Type=oneshot
TimeoutStartSec=0
ExecStart=/usr/local/sbin/rns-install-once.sh
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable rns-install.service
systemctl start rns-install.service || /usr/local/sbin/rns-install-once.sh
echo "instalacja repo zlecona"
"""
    else:
        install_block = 'echo "skipping gateway install"\n'
    return f"""#!/bin/bash
set +e
exec >> /boot/firmware/rns-firstboot.log 2>&1 || exec >> /boot/rns-firstboot.log 2>&1
echo "===== RNS firstboot $(date) ====="
BOOT=/boot/firmware
[ -f "$BOOT/cmdline.txt" ] || BOOT=/boot
rm -f /etc/nologin /run/nologin /var/lib/nologin
systemctl disable --now userconfig.service userconfig-pi.service >/dev/null 2>&1
systemctl stop systemd-user-sessions >/dev/null 2>&1
rm -f /etc/nologin /run/nologin
systemctl start systemd-user-sessions >/dev/null 2>&1
echo '{host_q}' > /etc/hostname
hostname '{host_q}' >/dev/null 2>&1
hostnamectl set-hostname '{host_q}' >/dev/null 2>&1
if ! id rtclm >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo,adm,netdev,gpio,i2c,spi,video,plugdev rtclm
fi
echo 'rtclm:reticulum' | chpasswd
echo 'root:{root_q}' | chpasswd
passwd -u root >/dev/null 2>&1
usermod -s /bin/bash rtclm >/dev/null 2>&1
usermod -s /bin/bash root >/dev/null 2>&1
echo 'rtclm ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/rtclm
chmod 440 /etc/sudoers.d/rtclm
mkdir -p /etc/ssh/sshd_config.d
printf '%s\\n' 'PasswordAuthentication yes' 'PermitRootLogin yes' 'KbdInteractiveAuthentication yes' > /etc/ssh/sshd_config.d/99-rns.conf
systemctl enable ssh >/dev/null 2>&1
systemctl enable sshd >/dev/null 2>&1
systemctl start ssh >/dev/null 2>&1
systemctl start sshd >/dev/null 2>&1
raspi-config nonint do_ssh 0 >/dev/null 2>&1
systemctl reload ssh >/dev/null 2>&1 || systemctl restart ssh >/dev/null 2>&1
echo "ssh/user/hostname ustawione"
rfkill unblock wifi >/dev/null 2>&1
rfkill unblock all >/dev/null 2>&1
mkdir -p /etc/NetworkManager/system-connections
cat > /etc/NetworkManager/system-connections/rns-wifi.nmconnection << 'NMEOF'
{wifi_nm(ssid, psk)}NMEOF
chmod 600 /etc/NetworkManager/system-connections/rns-wifi.nmconnection
chown root:root /etc/NetworkManager/system-connections/rns-wifi.nmconnection
echo "nmconnection zapisany"
systemctl restart NetworkManager >/dev/null 2>&1
sleep 3
nmcli radio wifi on >/dev/null 2>&1
nmcli connection reload >/dev/null 2>&1
nmcli device wifi connect '{ssid_q}' password '{psk_q}' >/dev/null 2>&1
nmcli connection up rns-wifi >/dev/null 2>&1
echo "nmcli exit=$?"
mkdir -p /var/lib/rns/wiki_data
for src in /boot/firmware/wiki_data /boot/wiki_data; do
  [ -d "$src" ] || continue
  cp -a "$src"/. /var/lib/rns/wiki_data/ 2>/dev/null || true
  echo "wiki_data from $src"
done
{install_block}
if [ -f /var/lib/rns/.installed ]; then
  sed -i -E 's/ systemd\\.run[^ ]*//g' "$BOOT/cmdline.txt" 2>/dev/null
  echo "systemd.run removed — gateway already installed"
fi
echo "RNS firstboot done"
"""


HOOK = "bash /boot/firmware/rns-firstboot.sh || bash /boot/rns-firstboot.sh || true\n"


def hook_firstrun(boot, ssid, psk, hostname, root_pw, log, do_install=True, auto_update=False, repo_url=None, use_ssh_key=False):
    path = os.path.join(boot, "firstrun.sh")
    if os.path.isfile(path):
        log("Imager firstrun.sh present — " + str(os.path.getsize(path)) + " B")
        try:
            text = read_text(path)
        except Exception:
            text = open(path, encoding="utf-8", errors="replace").read()
        if "rns-firstboot.sh" in text:
            log("hook already in firstrun.sh")
            return
        if re.search(r"^exit 0", text, re.M):
            text = text.replace("exit 0", HOOK + "exit 0", 1)
            log("hook inserted before exit 0")
        else:
            text = text.rstrip() + "\n" + HOOK
            log("hook appended to firstrun.sh")
        write_text(path, text)
        return
    log("no firstrun.sh — writing our own")
    write_text(path, firstboot_sh(ssid, psk, hostname, root_pw, do_install, auto_update, repo_url, use_ssh_key))


def apply(boot, ssid, psk, hostname, root_pw, log, do_install=True, auto_update=False, repo_url=None, ssh_key=None, wiki_lang="en", fetch_mode="on_pi"):
    def peek(name):
        p = os.path.join(boot, name)
        if os.path.isfile(p):
            log(f"  {name}: {os.path.getsize(p)} B")
        else:
            log(f"  {name}: MISSING")

    log("1. bootfs = " + boot)
    log("2. files on the card:")
    for name in (
        "cmdline.txt", "config.txt", "firstrun.sh", "user-data",
        "network-config", "meta-data", "wpa_supplicant.conf", "ssh",
    ):
        peek(name)

    cmd_path = boot_join(boot, "cmdline.txt")
    old_cmd = read_text(cmd_path)
    log("3. cmdline BEFORE:")
    log("   " + old_cmd.strip())
    if "ds=nocloud" in old_cmd:
        log("   cloud-init (ds=nocloud) detected — Imager 2.x / Trixie")
    if "systemd.run" in old_cmd:
        log("   systemd.run detected (legacy firstrun)")
    new_cmd = patch_cmdline(old_cmd)
    if "cfg80211.ieee80211_regdom=" not in new_cmd:
        new_cmd = new_cmd.rstrip() + " cfg80211.ieee80211_regdom=PL\n"
        log("   adding regdom=PL")
    write_text(cmd_path, new_cmd)
    log("4. cmdline AFTER:")
    log("   " + new_cmd.strip())

    cfg_path = boot_join(boot, "config.txt")
    old_cfg = read_text(cfg_path)
    new_cfg = patch_config(old_cfg)
    write_text(cfg_path, new_cfg)
    log("5. config.txt: dtoverlay=dwc2=" + str("dtoverlay=dwc2" in new_cfg) +
        "  otg_mode commented=" + str(bool(re.search(r"^#otg_mode=", new_cfg, re.M))))

    log("6. writing network-config (cloud-init / netplan for Trixie)")
    write_text(os.path.join(boot, "network-config"), network_config(ssid, psk))
    log("   SSID=" + ssid + "  password=" + str(len(psk)) + " chars")

    log("7. writing wpa_supplicant.conf (older images)")
    write_text(os.path.join(boot, "wpa_supplicant.conf"), wpa_conf(ssid, psk))

    log("8. hostname=" + hostname + "  user=rtclm")
    write_text(os.path.join(boot, "user-data"), user_data(hostname, root_pw))
    if not os.path.isfile(os.path.join(boot, "meta-data")):
        write_text(os.path.join(boot, "meta-data"), "instance-id: " + hostname + "\n")
    hashed = crypt_sha512("reticulum")
    if hashed:
        write_text(os.path.join(boot, "userconf.txt"), "rtclm:" + hashed + "\n")
        write_text(os.path.join(boot, "userconf"), "rtclm:" + hashed + "\n")
        log("   userconf.txt written")
    else:
        log("   no openssl/crypt — user from cloud-init and firstboot")

    use_key = bool(ssh_key and os.path.isfile(ssh_key))
    log("9. writing rns-firstboot.sh")
    write_text(
        os.path.join(boot, "rns-firstboot.sh"),
        firstboot_sh(ssid, psk, hostname, root_pw, do_install, auto_update, repo_url, use_key),
    )
    log("   auto-install repo=" + str(do_install) + "  auto-update=" + str(auto_update))
    log("   repo=" + (repo_url or REPO_URL))

    if use_key:
        dest_key = os.path.join(boot, "rns_deploy_key")
        with open(ssh_key, "rb") as src, open(dest_key, "wb") as dst:
            dst.write(src.read())
        try:
            os.chmod(dest_key, 0o600)
        except Exception:
            pass
        log("   deploy key copied to bootfs (" + str(os.path.getsize(dest_key)) + " B)")
    else:
        leftover = os.path.join(boot, "rns_deploy_key")
        if os.path.isfile(leftover):
            os.remove(leftover)
            log("   old deploy key removed")

    log("10. hook firstrun")
    hook_firstrun(boot, ssid, psk, hostname, root_pw, log, do_install, auto_update, repo_url, use_key)

    write_text(os.path.join(boot, "ssh"), "")
    log("11. ssh file (enable daemon)")

    dest_wiki = os.path.join(boot, "wiki_data")
    os.makedirs(dest_wiki, exist_ok=True)
    prefetch.write_config(dest_wiki, wiki_lang or "en", fetch_mode or "on_pi")
    log("12. wiki lang=" + str(wiki_lang) + "  fetch=" + str(fetch_mode))
    if fetch_mode == "prefetch":
        copied = copy_wiki_data(WIKI_DATA, dest_wiki, log)
        log("    copied " + str(copied) + " file(s) from wiki_data/")
    else:
        leftover_titles = os.path.join(dest_wiki, "titles.txt")
        if os.path.isfile(leftover_titles) and fetch_mode != "prefetch":
            pass

    log("13. verify after write:")
    for name in ("cmdline.txt", "network-config", "user-data", "userconf.txt", "rns-firstboot.sh", "firstrun.sh", "ssh"):
        peek(name)
    log("14. done. SSH: rtclm / reticulum   host: " + hostname)


class PrefetchWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    done = Signal()
    fail = Signal(str)

    def __init__(self, lang, dest):
        super().__init__()
        self.lang = lang
        self.dest = dest

    def run(self):
        try:
            def cb(done, total, msg):
                self.progress.emit(int(done or 0), int(total or 0), str(msg))
            info = prefetch.prefetch_titles(self.lang, self.dest, cb)
            self.log.emit("prefetch " + str(info.get("titles")) + " titles")
            self.done.emit()
        except Exception as exc:
            self.fail.emit(str(exc))


def box():
    f = QFrame()
    f.setObjectName("box")
    f.setLayout(QVBoxLayout())
    f.layout().setContentsMargins(12, 10, 12, 10)
    f.layout().setSpacing(6)
    return f


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("reticulum-wikipedia")
        self.resize(620, 860)
        self.root_pw = ""
        self.hostname = ""
        self.cards = []
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)

        title = QLabel("RETICULUM-WIKIPEDIA  ·  SD card")
        title.setObjectName("title")
        hint = QLabel(
            "Write Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager first "
            "(you may also set Wi-Fi there). Leave the card in the reader and click RUN — "
            "this tool only adds the gateway first-boot files."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(hint)

        b1 = box()
        row = QHBoxLayout()
        row.addWidget(QLabel("Card"))
        self.card = QComboBox()
        row.addWidget(self.card, 1)
        scan = QPushButton("Refresh")
        scan.clicked.connect(self.refresh)
        row.addWidget(scan)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_card)
        row.addWidget(browse)
        b1.layout().addLayout(row)
        self.err_card = QLabel("")
        self.err_card.setObjectName("err")
        b1.layout().addWidget(self.err_card)
        col.addWidget(b1)

        b2 = box()
        b2.layout().addWidget(QLabel("Wi‑Fi (DHCP)"))
        g = QGridLayout()
        self.ssid = QLineEdit()
        self.psk = QLineEdit()
        self.psk.setEchoMode(QLineEdit.Password)
        g.addWidget(QLabel("SSID"), 0, 0)
        g.addWidget(self.ssid, 0, 1)
        g.addWidget(QLabel("Password"), 1, 0)
        g.addWidget(self.psk, 1, 1)
        b2.layout().addLayout(g)
        saved = {}
        try:
            with open(WIFI_SAVE, encoding="utf-8") as fh:
                saved = json.load(fh)
        except Exception:
            saved = {}
        self.ssid.setText(str(saved.get("ssid") or ""))
        self.psk.setText(str(saved.get("psk") or ""))
        self.err_net = QLabel("")
        self.err_net.setObjectName("err")
        b2.layout().addWidget(self.err_net)
        self.do_install = QCheckBox("Clone the repo and start the gateway (HTTP/HTTPS) on first boot")
        self.do_install.setChecked(bool(saved.get("do_install", True)))
        b2.layout().addWidget(self.do_install)
        self.auto_update = QCheckBox("Automatically pull repository updates onto the device")
        self.auto_update.setChecked(bool(saved.get("auto_update", False)))
        b2.layout().addWidget(self.auto_update)
        g2 = QGridLayout()
        self.repo = QLineEdit(str(saved.get("repo") or REPO_URL))
        self.repo.setPlaceholderText("git@github.com:user/repo.git")
        self.ssh_key = QLineEdit(str(saved.get("ssh_key") or ""))
        self.ssh_key.setPlaceholderText("optional private key for a private repo")
        pick = QPushButton("Key…")
        pick.clicked.connect(self.pick_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.ssh_key, 1)
        key_row.addWidget(pick)
        g2.addWidget(QLabel("Repo"), 0, 0)
        g2.addWidget(self.repo, 0, 1)
        g2.addWidget(QLabel("SSH key"), 1, 0)
        g2.addLayout(key_row, 1, 1)
        b2.layout().addLayout(g2)
        hint = QLabel("Private GitHub: add a deploy key to the repo, pick the private key here. HTTPS URL is rewritten to git@.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        b2.layout().addWidget(hint)
        col.addWidget(b2)

        wiki = box()
        wiki.layout().addWidget(QLabel("Wikipedia"))
        self.langs = load_languages()
        self.lang = QComboBox()
        self.lang.setEditable(True)
        self.lang.setInsertPolicy(QComboBox.NoInsert)
        for item in self.langs:
            self.lang.addItem(lang_label(item), item.get("code"))
        want = str(saved.get("wiki_lang") or "en")
        idx = self.lang.findData(want)
        if idx >= 0:
            self.lang.setCurrentIndex(idx)
        wiki.layout().addWidget(self.lang)
        lang_hint = QLabel("Every language Wikipedia publishes. Type to filter the list.")
        lang_hint.setObjectName("hint")
        lang_hint.setWordWrap(True)
        wiki.layout().addWidget(lang_hint)
        self.fetch_prefetch = QRadioButton("Prefetch before installing (wiki_data/ on this computer, then copy to the card)")
        self.fetch_on_pi = QRadioButton("Fetch all data on the Raspberry Pi after install (needs internet on the Pi)")
        fetch_mode = str(saved.get("fetch_mode") or "on_pi")
        self.fetch_on_pi.setChecked(fetch_mode != "prefetch")
        self.fetch_prefetch.setChecked(fetch_mode == "prefetch")
        grp = QButtonGroup(self)
        grp.addButton(self.fetch_prefetch)
        grp.addButton(self.fetch_on_pi)
        wiki.layout().addWidget(self.fetch_prefetch)
        wiki.layout().addWidget(self.fetch_on_pi)
        fetch_hint = QLabel(
            "Prefetch downloads the full title index for that language into wiki_data/ and shows progress. "
            "Article text is still fetched when a page is opened. "
            "On-Pi does the same download after first boot."
        )
        fetch_hint.setObjectName("hint")
        fetch_hint.setWordWrap(True)
        wiki.layout().addWidget(fetch_hint)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        wiki.layout().addWidget(self.bar)
        self.err_wiki = QLabel("")
        self.err_wiki.setObjectName("err")
        wiki.layout().addWidget(self.err_wiki)
        col.addWidget(wiki)

        cred = box()
        self.host_lab = QLabel("host: —")
        self.host_lab.setObjectName("cred")
        self.user_lab = QLabel("ssh: rtclm / reticulum")
        self.user_lab.setObjectName("cred")
        self.pw_lab = QLabel("root password: (after RUN)")
        self.pw_lab.setObjectName("rootpw")
        self.pw_lab.setWordWrap(True)
        copy = QPushButton("Copy root password")
        copy.clicked.connect(self.copy_root)
        cred.layout().addWidget(self.host_lab)
        cred.layout().addWidget(self.user_lab)
        cred.layout().addWidget(self.pw_lab)
        cred.layout().addWidget(copy)
        kh = QHBoxLayout()
        self.kh_host = QLineEdit(str(saved.get("ssh_host") or "192.168.0.153"))
        self.kh_host.setPlaceholderText("IP or node-xxxxx")
        wipe = QPushButton("Remove from known_hosts")
        wipe.clicked.connect(self.wipe_known)
        kh.addWidget(self.kh_host)
        kh.addWidget(wipe)
        cred.layout().addLayout(kh)
        col.addWidget(cred)

        run = QPushButton("RUN")
        run.setObjectName("run")
        run.clicked.connect(self.run)
        col.addWidget(run)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        col.addWidget(self.log)
        for lab in self.findChildren(QLabel):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.refresh()

    def pick_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Private SSH key", os.path.expanduser("~/.ssh"), "Keys (*);;All (*.*)"
        )
        if path:
            self.ssh_key.setText(path)

    def wipe_known(self):
        targets = []
        typed = self.kh_host.text().strip()
        if typed:
            targets.append(typed)
        if self.hostname:
            targets.append(self.hostname)
            targets.append(self.hostname + ".local")
        seen = []
        for t in targets:
            if t not in seen:
                seen.append(t)
        if not seen:
            self.say("enter an IP or click RUN first (so a hostname exists)")
            return
        for t in seen:
            self.say(drop_known_host(t))

    def copy_root(self):
        if not self.root_pw:
            return
        QApplication.clipboard().setText(self.root_pw)
        self.say("root password copied")

    def say(self, text):
        self.log.append(text)
        self.log.moveCursor(QTextCursor.End)

    def browse_card(self):
        start = "/media"
        if not os.path.isdir(start):
            start = "/"
        path = QFileDialog.getExistingDirectory(self, "Boot partition (folder with cmdline.txt)", start)
        if not path:
            return
        if not is_bootfs(path):
            self.err_card.setText("that folder has no cmdline.txt + config.txt")
            self.say("not a bootfs: " + path)
            return
        self.err_card.setText("")
        item = {"label": "manual " + path, "path": path}
        self.cards = [item] + [c for c in self.cards if c["path"] != path]
        self.card.clear()
        for c in self.cards:
            self.card.addItem(c["label"])
        self.card.setCurrentIndex(0)
        self.say("card: " + path)

    def refresh(self):
        self.card.clear()
        self.cards = list_bootfs()
        if not self.cards:
            self.card.addItem("insert a Lite-imaged card and refresh")
            self.say("no bootfs mounted")
            self.say("Linux: open the card in the file manager once, or click Browse and pick the bootfs folder")
            parts = []
            for p in _lsblk():
                if p["fstype"] or p["label"] or p["mp"]:
                    parts.append(
                        (p["dev"] + " " + (p["label"] or "-") + " " + (p["fstype"] or "-") + " " + (p["mp"] or "unmounted"))
                    )
            if parts:
                self.say("disks: " + " | ".join(parts[:12]))
            return
        for c in self.cards:
            self.card.addItem(c["label"] + "  " + c["path"])
        self.say("card: " + self.cards[0]["path"])

    def selected(self):
        i = self.card.currentIndex()
        if i < 0 or i >= len(self.cards):
            return None
        return self.cards[i]

    def current_lang(self):
        code = self.lang.currentData()
        if code:
            return str(code)
        text = self.lang.currentText().strip()
        if text.endswith(")") and "(" in text:
            text = text[text.rfind("(") + 1 : -1]
        return (text or "en").split()[0].strip().lower()

    def fetch_mode(self):
        return "prefetch" if self.fetch_prefetch.isChecked() else "on_pi"

    def save_prefs(self, ssid):
        with open(WIFI_SAVE, "w", encoding="utf-8") as fh:
            json.dump({
                "ssid": ssid,
                "psk": self.psk.text(),
                "ssh_host": self.kh_host.text().strip(),
                "do_install": self.do_install.isChecked(),
                "auto_update": self.auto_update.isChecked(),
                "repo": self.repo.text().strip() or REPO_URL,
                "ssh_key": self.ssh_key.text().strip(),
                "wiki_lang": self.current_lang(),
                "fetch_mode": self.fetch_mode(),
            }, fh)

    def write_card(self, card):
        apply(
            card["path"], self.ssid.text().strip(), self.psk.text(),
            self.hostname, self.root_pw, self.say,
            self.do_install.isChecked(),
            self.auto_update.isChecked(),
            self.repo.text().strip() or REPO_URL,
            self.ssh_key.text().strip(),
            self.current_lang(),
            self.fetch_mode(),
        )
        self.save_prefs(self.ssid.text().strip())

    def run(self):
        self.err_card.setText("")
        self.err_net.setText("")
        self.err_wiki.setText("")
        card = self.selected()
        ssid = self.ssid.text().strip()
        if not card:
            self.err_card.setText("select the card that already has cmdline.txt (after Imager)")
            return
        if not ssid:
            self.err_net.setText("enter the SSID")
            return
        self.hostname = make_hostname()
        self.root_pw = make_root_pw()
        self.host_lab.setText("host: " + self.hostname)
        self.pw_lab.setText("root password: " + self.root_pw)
        self.say("=== RUN " + card["path"] + " ===")
        self.say("wiki " + self.current_lang() + "  " + self.fetch_mode())
        if self.fetch_mode() != "prefetch":
            try:
                prefetch.write_config(WIKI_DATA, self.current_lang(), "on_pi")
                self.write_card(card)
            except Exception as exc:
                self.err_card.setText(str(exc))
                self.say("ERROR: " + str(exc))
            return
        self.bar.setValue(0)
        self.thread = QThread()
        self.worker = PrefetchWorker(self.current_lang(), WIKI_DATA)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_prefetch_progress)
        self.worker.log.connect(self.say)
        self.worker.fail.connect(lambda e: self.on_prefetch_fail(e))
        self.worker.done.connect(lambda: self.on_prefetch_done(card))
        self.worker.done.connect(self.thread.quit)
        self.worker.fail.connect(self.thread.quit)
        self.thread.start()

    def on_prefetch_progress(self, done, total, msg):
        if total:
            self.bar.setValue(max(0, min(100, int(done * 100 / total))))
        self.say(msg)

    def on_prefetch_fail(self, err):
        self.err_wiki.setText(str(err))
        self.say("prefetch ERROR: " + str(err))

    def on_prefetch_done(self, card):
        self.bar.setValue(100)
        try:
            self.write_card(card)
        except Exception as exc:
            self.err_card.setText(str(exc))
            self.say("ERROR: " + str(exc))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    win = App()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
