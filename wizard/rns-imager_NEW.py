#!/usr/bin/env python3
"""Raspberry Pi Imager (Linux).

Writes an image with dd, then adds Wi-Fi, SSH and a first-boot user.
Windows is shown disabled. Every failure is printed.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import string
import subprocess
import sys
import time
import traceback
import urllib.request

from PySide6.QtCore import (
    Qt,
    QEasingCurve,
    QProcess,
    QPropertyAnimation,
    QThread,
    Signal,
    QObject,
)
from PySide6.QtGui import QColor, QFont, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

IMAGE_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
WIFI_SAVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wifi.json")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "rpi-imager")
MAX_CARD_BYTES = 256 * 1024 * 1024 * 1024
MIN_IMAGE_BYTES = 200 * 1024 * 1024
JOB_FLAG = "--flash-job"
USER_NAME = "pi"
USER_PASS = "raspberry"
FIRSTBOOT = "firstboot.sh"

STYLE = """
QMainWindow, QWidget#root { background: #07070a; color: #ece8f4; }
QLabel { color: #ece8f4; font-size: 13px; }
QLabel#title { color: #e4d7ff; font-size: 18px; font-weight: 700; }
QLabel#brand { color: #8d7aa8; font-size: 11px; }
QLabel#hint { color: #8d7aa8; font-size: 12px; }
QLabel#err { color: #ff7aa2; font-size: 12px; }
QLabel#warn { color: #e6b84d; font-size: 13px; font-weight: 700; }
QLabel#rootpw { color: #c4a6ff; font-size: 16px; font-weight: 700; }
QLabel#cred { color: #ece8f4; font-size: 13px; }
QLineEdit, QComboBox {
  background: #101014; color: #ece8f4; border: 1px solid #3a2f4d;
  padding: 6px 8px; selection-background-color: #6d3dff;
  border-radius: 4px;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #9b6dff; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
  background: #101014; color: #ece8f4; selection-background-color: #6d3dff;
  border: 1px solid #3a2f4d;
}
QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled {
  color: #5c5470; background: #0c0c10; border-color: #2a2433;
}
QPushButton {
  background: #16161c; color: #ece8f4; border: 1px solid #4a3b66;
  padding: 7px 12px; border-radius: 4px;
}
QPushButton:hover { background: #221c30; border-color: #9b6dff; }
QPushButton:pressed { background: #2a2040; }
QPushButton#run {
  background: #6d3dff; color: #ffffff; border: 1px solid #9b6dff;
  font-weight: 700; padding: 10px 16px;
}
QPushButton#run:hover { background: #8354ff; }
QPushButton#run:pressed { background: #5a2ee0; }
QPushButton#run:disabled { background: #2a2040; color: #7a7090; border-color: #3a2f4d; }
QTextEdit {
  background: #050508; color: #d4c2ff; border: 1px solid #3a2f4d;
  font-family: ui-monospace, Consolas, monospace; font-size: 12px;
  border-radius: 4px;
}
QTextEdit QScrollBar:vertical {
  background: #050508; width: 10px; margin: 0;
}
QTextEdit QScrollBar::handle:vertical {
  background: #4a3b66; min-height: 24px; border-radius: 4px;
}
QTextEdit QScrollBar::add-line:vertical, QTextEdit QScrollBar::sub-line:vertical { height: 0; }
QProgressBar {
  border: 1px solid #3a2f4d; background: #101014; color: #e4d7ff;
  text-align: center; height: 18px; border-radius: 4px;
}
QProgressBar::chunk { background: #7c4dff; border-radius: 3px; }
QFrame#box { border: 1px solid #2a2438; background: #0e0e13; border-radius: 6px; }
QFrame#banner { border: 1px solid #6a4a16; background: #1a1408; }
"""


class Fail(RuntimeError):
    """Visible, expected failure. Message is shown to the user as-is."""


def emit(line: str) -> None:
    sys.stdout.write(line.rstrip() + "\n")
    sys.stdout.flush()


def emit_progress(pct: int) -> None:
    emit("PROGRESS " + str(max(0, min(100, int(pct)))))


def which_or_fail(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise Fail("Required program not found: " + name + ". Install it and try again.")
    return path


def run_cmd(argv, *, timeout=None, check=True):
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise Fail("Program not found: " + argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise Fail("Timed out: " + " ".join(argv)) from exc
    text = (proc.stdout or "").strip()
    if check and proc.returncode != 0:
        raise Fail(
            "Command failed ("
            + str(proc.returncode)
            + "): "
            + " ".join(argv)
            + ("\n" + text if text else "")
        )
    return proc.returncode, text


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def gb(n) -> str:
    try:
        return f"{int(n) / (1024 ** 3):.2f} GB"
    except Exception:
        return "?"


def load_saved() -> dict:
    try:
        with open(WIFI_SAVE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        emit("WARN could not read " + WIFI_SAVE + ": " + str(exc))
        return {}


def save_saved(data: dict) -> None:
    try:
        with open(WIFI_SAVE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    except Exception as exc:
        raise Fail("Could not save settings to " + WIFI_SAVE + ": " + str(exc)) from exc


def is_bootfs(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "cmdline.txt")) and os.path.isfile(
        os.path.join(path, "config.txt")
    )


def read_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise Fail("Cannot read " + path + ": " + str(exc)) from exc
    if not data:
        raise Fail("File is empty: " + path)
    if b"\x00" in data[:80]:
        raise Fail("File looks binary or damaged: " + path)
    return data.decode("ascii", errors="replace")


def write_text(path: str, text: str) -> None:
    raw = text.replace("\r\n", "\n").encode("ascii", errors="replace")
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        with open(path, "rb") as fh:
            written = fh.read()
        if written != raw:
            raise Fail("Write verify failed: " + path)
    except Fail:
        raise
    except OSError as exc:
        raise Fail("Cannot write " + path + ": " + str(exc)) from exc
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def patch_cmdline(text: str) -> str:
    line = " ".join(text.replace("\n", " ").split())
    line = re.sub(r"\bsystemd\.run\S*", "", line)
    line = re.sub(r"\s+", " ", line).strip()
    line += " systemd.run=/boot/firmware/" + FIRSTBOOT
    line += " systemd.run_success_action=none systemd.run_failure_action=none"
    if "cfg80211.ieee80211_regdom=" not in line:
        line += " cfg80211.ieee80211_regdom=PL"
    return re.sub(r"\s+", " ", line).strip() + "\n"


def make_hostname() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "pi-" + "".join(secrets.choice(alphabet) for _ in range(6))


def make_root_pw() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(14))


def crypt_sha512(password: str) -> str:
    try:
        import crypt

        hashed = crypt.crypt(password, crypt.METHOD_SHA512)
        if hashed:
            return hashed
    except Exception:
        pass
    openssl = shutil.which("openssl")
    if not openssl:
        return ""
    try:
        return subprocess.check_output([openssl, "passwd", "-6", password], text=True).strip()
    except Exception as exc:
        emit("WARN openssl passwd failed: " + str(exc))
        return ""


def user_data(hostname: str, root_pw: str) -> str:
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
        f"    {USER_NAME}:{USER_PASS}\n"
        "users:\n"
        f"  - name: {USER_NAME}\n"
        "    gecos: Raspberry Pi\n"
        "    primary_group: users\n"
        "    groups: [adm, dialout, sudo, audio, video, plugdev, netdev, gpio, i2c, spi]\n"
        "    shell: /bin/bash\n"
        "    lock_passwd: false\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        f"    plain_text_passwd: {USER_PASS}\n"
        "runcmd:\n"
        f"  - usermod -s /bin/bash {USER_NAME}\n"
        f"  - passwd -u {USER_NAME}\n"
        f"  - mkdir -p /home/{USER_NAME}\n"
        f"  - chown {USER_NAME}:{USER_NAME} /home/{USER_NAME}\n"
    )


def yaml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def wifi_nm(ssid: str, psk: str) -> str:
    return (
        "[connection]\n"
        "id=rpi-wifi\n"
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


def network_config(ssid: str, psk: str) -> str:
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


def wpa_conf(ssid: str, psk: str) -> str:
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


def firstboot_sh(ssid, psk, hostname, root_pw) -> str:
    ssid_q = ssid.replace("'", "'\\''")
    psk_q = psk.replace("'", "'\\''")
    host_q = hostname.replace("'", "'\\''")
    root_q = root_pw.replace("'", "'\\''")
    user_q = USER_NAME.replace("'", "'\\''")
    pass_q = USER_PASS.replace("'", "'\\''")
    return f"""#!/bin/bash
set +e
exec >> /boot/firmware/firstboot.log 2>&1 || exec >> /boot/firstboot.log 2>&1
echo "===== firstboot $(date) ====="
BOOT=/boot/firmware
[ -f "$BOOT/cmdline.txt" ] || BOOT=/boot
rm -f /etc/nologin /run/nologin /var/lib/nologin
systemctl disable --now userconfig.service userconfig-pi.service >/dev/null 2>&1
systemctl start systemd-user-sessions >/dev/null 2>&1
echo '{host_q}' > /etc/hostname
hostname '{host_q}' >/dev/null 2>&1
hostnamectl set-hostname '{host_q}' >/dev/null 2>&1
if ! id '{user_q}' >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo,adm,netdev,gpio,i2c,spi,video,plugdev '{user_q}'
fi
usermod -s /bin/bash '{user_q}' >/dev/null 2>&1
usermod -aG sudo,adm,netdev,gpio,i2c,spi,video,plugdev '{user_q}' >/dev/null 2>&1
mkdir -p /home/{user_q}
chown '{user_q}':'{user_q}' /home/{user_q} >/dev/null 2>&1
echo '{user_q}:{pass_q}' | chpasswd
echo 'root:{root_q}' | chpasswd
passwd -u '{user_q}' >/dev/null 2>&1
passwd -u root >/dev/null 2>&1
chage -E -1 '{user_q}' >/dev/null 2>&1
echo '{user_q} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/{user_q}
chmod 440 /etc/sudoers.d/{user_q}
sed -i 's#^Banner .*#Banner none#' /etc/ssh/sshd_config >/dev/null 2>&1
rm -f /usr/share/userconf-pi/sshd_banner /etc/ssh/sshd_banner >/dev/null 2>&1
echo "user $(getent passwd '{user_q}')"
mkdir -p /etc/ssh/sshd_config.d
printf '%s\\n' 'PasswordAuthentication yes' 'PermitRootLogin yes' 'KbdInteractiveAuthentication yes' > /etc/ssh/sshd_config.d/99-imager.conf
systemctl enable ssh >/dev/null 2>&1
systemctl enable sshd >/dev/null 2>&1
systemctl start ssh >/dev/null 2>&1
systemctl start sshd >/dev/null 2>&1
raspi-config nonint do_ssh 0 >/dev/null 2>&1
systemctl reload ssh >/dev/null 2>&1 || systemctl restart ssh >/dev/null 2>&1
rfkill unblock wifi >/dev/null 2>&1
rfkill unblock all >/dev/null 2>&1
mkdir -p /etc/NetworkManager/system-connections
cat > /etc/NetworkManager/system-connections/rpi-wifi.nmconnection << 'NMEOF'
{wifi_nm(ssid, psk)}NMEOF
chmod 600 /etc/NetworkManager/system-connections/rpi-wifi.nmconnection
chown root:root /etc/NetworkManager/system-connections/rpi-wifi.nmconnection
systemctl restart NetworkManager >/dev/null 2>&1
sleep 3
nmcli radio wifi on >/dev/null 2>&1
nmcli connection reload >/dev/null 2>&1
nmcli device wifi connect '{ssid_q}' password '{psk_q}' >/dev/null 2>&1
nmcli connection up rpi-wifi >/dev/null 2>&1
echo "nmcli exit=$?"
sed -i -E 's/ systemd\\.run[^ ]*//g' "$BOOT/cmdline.txt" 2>/dev/null
echo "firstboot done"
"""


HOOK = "bash /boot/firmware/" + FIRSTBOOT + " || bash /boot/" + FIRSTBOOT + " || true\n"


def hook_firstrun(boot, ssid, psk, hostname, root_pw, log):
    path = os.path.join(boot, "firstrun.sh")
    if os.path.isfile(path):
        log("firstrun.sh present — " + str(os.path.getsize(path)) + " B")
        try:
            text = read_text(path)
        except Fail:
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError as exc:
                raise Fail("Cannot read firstrun.sh: " + str(exc)) from exc
        if FIRSTBOOT in text:
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
    write_text(path, firstboot_sh(ssid, psk, hostname, root_pw))


def apply(boot, ssid, psk, hostname, root_pw, log):
    if not is_bootfs(boot):
        raise Fail("Not a Raspberry Pi boot partition: " + boot)

    def peek(name):
        p = os.path.join(boot, name)
        if os.path.isfile(p):
            log(f"  {name}: {os.path.getsize(p)} B")
        else:
            log(f"  {name}: MISSING")

    log("bootfs = " + boot)
    cmd_path = os.path.join(boot, "cmdline.txt")
    old_cmd = read_text(cmd_path)
    log("cmdline BEFORE: " + old_cmd.strip())
    if "root=" not in old_cmd:
        raise Fail("cmdline.txt has no root= — refusing to patch a damaged boot file")
    write_text(cmd_path, patch_cmdline(old_cmd))
    check_cmd = read_text(cmd_path)
    if "systemd.run=/boot/firmware/" + FIRSTBOOT not in check_cmd:
        raise Fail("cmdline.txt did not keep systemd.run after write")
    log("cmdline AFTER: " + check_cmd.strip())

    write_text(os.path.join(boot, "network-config"), network_config(ssid, psk))
    write_text(os.path.join(boot, "wpa_supplicant.conf"), wpa_conf(ssid, psk))
    log("Wi-Fi SSID=" + ssid + "  password=" + str(len(psk)) + " chars")

    write_text(os.path.join(boot, "user-data"), user_data(hostname, root_pw))
    if not os.path.isfile(os.path.join(boot, "meta-data")):
        write_text(os.path.join(boot, "meta-data"), "instance-id: " + hostname + "\n")
    hashed = crypt_sha512(USER_PASS)
    if hashed:
        write_text(os.path.join(boot, "userconf.txt"), USER_NAME + ":" + hashed + "\n")
        write_text(os.path.join(boot, "userconf"), USER_NAME + ":" + hashed + "\n")
        log("userconf.txt written")
    else:
        log("WARN no openssl/crypt — user will come from cloud-init and firstboot")

    script = firstboot_sh(ssid, psk, hostname, root_pw)
    write_text(os.path.join(boot, FIRSTBOOT), script)
    if os.path.getsize(os.path.join(boot, FIRSTBOOT)) < 100:
        raise Fail(FIRSTBOOT + " is too small after write")
    hook_firstrun(boot, ssid, psk, hostname, root_pw, log)
    write_text(os.path.join(boot, "ssh"), "")

    required = ("cmdline.txt", "config.txt", "network-config", "user-data", FIRSTBOOT, "firstrun.sh", "ssh")
    missing = [name for name in required if not os.path.isfile(os.path.join(boot, name))]
    log("verify after write:")
    for name in required + ("userconf.txt", "wpa_supplicant.conf"):
        peek(name)
    if missing:
        raise Fail("Missing after write: " + ", ".join(missing))
    log("first-boot files verified")


def list_disks():
    if not is_linux():
        return []
    which_or_fail("lsblk")
    code, raw = run_cmd(
        ["lsblk", "-J", "-b", "-o", "NAME,SIZE,MODEL,TRAN,TYPE,RM,MOUNTPOINT,PKNAME"],
        check=True,
    )
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise Fail("lsblk returned invalid JSON: " + str(exc)) from exc
    disks = []
    for d in data.get("blockdevices") or []:
        if d.get("type") != "disk":
            continue
        size = int(d.get("size") or 0)
        name = d.get("name") or ""
        if not name or size <= 0:
            continue
        mounts = []

        def walk(node):
            if node.get("mountpoint"):
                mounts.append(node["mountpoint"])
            for ch in node.get("children") or []:
                walk(ch)

        walk(d)
        system = any(m in ("/", "/boot", "/boot/efi", "/home", "/usr") for m in mounts)
        item = {
            "id": name,
            "dev": "/dev/" + name,
            "size": size,
            "model": (d.get("model") or name).strip(),
            "tran": (d.get("tran") or "").strip(),
            "mounts": mounts,
            "system": system,
        }
        item["label"] = (
            item["model"]
            + " · "
            + gb(size)
            + (" · " + item["tran"] if item["tran"] else "")
            + " · "
            + item["dev"]
        )
        if system:
            item["label"] += "  (SYSTEM — blocked)"
        elif size > MAX_CARD_BYTES:
            item["label"] += "  (too large — blocked)"
        disks.append(item)
    return disks


def disk_by_dev(dev: str):
    for d in list_disks():
        if d["dev"] == dev:
            return d
    return None


def assert_safe_disk(dev: str, expected_size=None):
    if not dev.startswith("/dev/"):
        raise Fail("Refusing unusual device path: " + dev)
    if not re.match(r"^/dev/(sd[a-z]+|mmcblk[0-9]+|nvme[0-9]+n[0-9]+|vd[a-z]+|loop[0-9]+)$", dev):
        raise Fail("Refusing unexpected device name: " + dev)
    if os.path.islink(dev):
        real = os.path.realpath(dev)
        emit("device " + dev + " -> " + real)
        dev = real
    if not os.path.exists(dev):
        raise Fail("Device disappeared: " + dev)
    info = disk_by_dev(dev)
    if not info:
        raise Fail("Device not found in lsblk: " + dev)
    if info["system"]:
        raise Fail("Refusing to write a disk that holds the running system: " + dev)
    if info["size"] > MAX_CARD_BYTES:
        raise Fail("Disk is larger than 256 GB — this tool is for SD / USB cards: " + dev)
    if expected_size is not None and int(expected_size) != int(info["size"]):
        raise Fail(
            "Disk size changed since you selected it ("
            + gb(expected_size)
            + " -> "
            + gb(info["size"])
            + "). Re-select the card."
        )
    return info


def part_name(disk: str, number: int) -> str:
    if re.search(r"(mmcblk|nvme|loop|nbd)", disk):
        return disk + "p" + str(number)
    return disk + str(number)


def mounted_parts(dev: str):
    out = []
    code, raw = run_cmd(["lsblk", "-ln", "-o", "NAME,MOUNTPOINT", dev], check=False)
    if code != 0:
        raise Fail("lsblk failed for " + dev + ":\n" + raw)
    for line in raw.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1] not in ("", "-"):
            out.append(("/dev/" + parts[0], parts[1]))
    return out


def unmount_device(dev: str, log) -> None:
    mounts = mounted_parts(dev)
    if not mounts:
        log("no mounted partitions on " + dev)
        return
    for part, mp in reversed(mounts):
        log("unmount " + part + " from " + mp)
        code, text = run_cmd(["umount", "-f", part], check=False)
        if code != 0:
            code2, text2 = run_cmd(["umount", "-l", part], check=False)
            if code2 != 0:
                raise Fail(
                    "Cannot unmount " + part + " (" + mp + "). Close file managers.\n" + text + "\n" + text2
                )
    left = mounted_parts(dev)
    if left:
        raise Fail("Still mounted after umount: " + ", ".join(p + " on " + m for p, m in left))
    log("all partitions unmounted")


def reread_partition_table(dev: str, log) -> None:
    for cmd in (["blockdev", "--rereadpt", dev], ["partprobe", dev], ["udevadm", "settle"]):
        if not shutil.which(cmd[0]):
            log("skip " + cmd[0] + " (not installed)")
            continue
        code, text = run_cmd(cmd, check=False)
        if code != 0:
            log("WARN " + " ".join(cmd) + " failed: " + text)
        else:
            log(" ".join(cmd) + " ok")
    time.sleep(1)


def wait_for_boot_part(dev: str, log, timeout=30) -> str:
    part = part_name(dev, 1)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(part):
            log("boot partition appeared: " + part)
            return part
        time.sleep(0.5)
    raise Fail("Partition 1 did not appear after writing " + dev + ". Expected " + part + ".")


def mount_boot(part: str, dest: str, log) -> str:
    os.makedirs(dest, exist_ok=True)
    if os.path.ismount(dest):
        run_cmd(["umount", dest], check=False)
    code, text = run_cmd(["mount", "-t", "vfat", "-o", "rw,utf8", part, dest], check=False)
    if code != 0:
        code, text = run_cmd(["mount", "-o", "rw", part, dest], check=False)
    if code != 0:
        raise Fail("Cannot mount " + part + ":\n" + text)
    if not is_bootfs(dest):
        run_cmd(["umount", dest], check=False)
        raise Fail("Mounted " + part + " but it is not a Raspberry Pi boot partition.")
    log("mounted " + part + " at " + dest)
    return dest


def image_kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".img.xz") or lower.endswith(".xz"):
        return "xz"
    if lower.endswith(".img") or lower.endswith(".iso"):
        return "img"
    raise Fail("Unsupported image file: " + path + ". Use a .img, .img.xz or .zip.")


def assert_image(path: str) -> None:
    if not path:
        raise Fail("No image file selected.")
    if not os.path.isfile(path):
        raise Fail("Image file not found: " + path)
    size = os.path.getsize(path)
    if size < MIN_IMAGE_BYTES:
        raise Fail("Image file is only " + str(size) + " bytes. Too small for Raspberry Pi OS.")


def unzip_to_img(path: str, log) -> str:
    import zipfile

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise Fail("Not a valid zip file: " + path + "\n" + str(exc)) from exc
    imgs = [n for n in zf.namelist() if n.lower().endswith(".img") and not n.endswith("/")]
    if not imgs:
        raise Fail("Zip file contains no .img: " + path)
    name = imgs[0]
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, os.path.basename(name))
    if os.path.isfile(dest) and os.path.getsize(dest) > MIN_IMAGE_BYTES:
        log("using already unpacked " + dest)
        return dest
    log("unpacking " + name + " from zip")
    tmp = dest + ".part"
    try:
        with zf.open(name) as src, open(tmp, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, dest)
    except OSError as exc:
        raise Fail("Cannot unpack zip image: " + str(exc)) from exc
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    log("unpacked " + dest + " (" + gb(os.path.getsize(dest)) + ")")
    return dest


def xz_uncompressed_size(path: str) -> int:
    code, text = run_cmd(["xz", "-l", "--robot", path], check=False)
    if code != 0:
        return 0
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "file" and len(parts) >= 5:
            try:
                return int(parts[4])
            except ValueError:
                return 0
        if parts and parts[0] == "totals" and len(parts) >= 4:
            try:
                return int(parts[3])
            except ValueError:
                return 0
    return 0


def proc_write_bytes(pid: int):
    try:
        with open("/proc/" + str(pid) + "/io", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("write_bytes:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def dd_cmd(dev, image=None, direct=True):
    cmd = ["dd", "of=" + dev, "bs=4M", "iflag=fullblock", "status=progress"]
    if image:
        cmd.insert(1, "if=" + image)
    if direct:
        cmd.append("oflag=direct")
    return cmd


def open_dd(dev, log, image=None, stdin=None, direct=True):
    cmd = dd_cmd(dev, image=image, direct=direct)
    log("command: " + " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdin=stdin if image is None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def flash_with_dd(image: str, dev: str, log) -> None:
    which_or_fail("dd")
    kind = image_kind(image)
    if kind == "zip":
        image = unzip_to_img(image, log)
        kind = "img"
    unmount_device(dev, log)
    expected = 0
    xz = None
    dd = None
    log("writing with oflag=direct so the card is written as we go")
    if kind == "xz":
        which_or_fail("xz")
        expected = xz_uncompressed_size(image)
        log("decompress + dd " + image + " -> " + dev)
        if expected:
            log("uncompressed size " + gb(expected))
        xz = subprocess.Popen(
            ["xz", "-dc", image],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        dd = open_dd(dev, log, stdin=xz.stdout, direct=True)
        if xz.stdout:
            xz.stdout.close()
    else:
        expected = os.path.getsize(image)
        log("image size " + gb(expected))
        dd = open_dd(dev, log, image=image, direct=True)
    time.sleep(0.4)
    if dd.poll() is not None and dd.returncode != 0:
        err = b""
        if dd.stdout:
            err = dd.stdout.read() or b""
        log("oflag=direct failed: " + err.decode("utf-8", "replace").strip())
        log("retrying without oflag=direct")
        if xz is not None:
            xz.kill()
            xz.wait()
            xz = subprocess.Popen(
                ["xz", "-dc", image],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            dd = open_dd(dev, log, stdin=xz.stdout, direct=False)
            if xz.stdout:
                xz.stdout.close()
        else:
            dd = open_dd(dev, log, image=image, direct=False)
    _watch_dd(dd, expected, log)
    if xz is not None:
        xz.wait()
        if xz.returncode != 0:
            raise Fail("xz failed (" + str(xz.returncode) + ")")
    if dd.returncode != 0:
        raise Fail("dd failed (" + str(dd.returncode) + ")")
    if shutil.which("blockdev"):
        code, text = run_cmd(["blockdev", "--flushbufs", dev], check=False)
        if code != 0:
            log("WARN blockdev --flushbufs: " + text)
        else:
            log("card buffers flushed")
    emit_progress(90)


def _watch_dd(dd, expected: int, log) -> None:
    if dd.stdout:
        try:
            os.set_blocking(dd.stdout.fileno(), False)
        except (AttributeError, OSError) as exc:
            log("WARN cannot set non-blocking dd output: " + str(exc))
    last_report = 0.0
    last_logged = -1
    last_bytes = -1
    stall = 0
    announced_flush = False
    buf = ""
    while True:
        if dd.stdout:
            try:
                chunk = dd.stdout.read(4096) or b""
            except BlockingIOError:
                chunk = b""
            if chunk:
                buf += chunk.decode("utf-8", "replace").replace("\r", "\n")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        log(line)
        written = proc_write_bytes(dd.pid) if dd.pid else None
        now = time.time()
        if now - last_report >= 1:
            last_report = now
            total = expected or int(2.5 * 1024 * 1024 * 1024)
            if written is None:
                if last_logged < 0:
                    log("dd still running (waiting for the first bytes)")
                    last_logged = 0
                emit_progress(8)
            else:
                done = written >= total
                pct = 88 if done else 8 + min(79, int(written * 79 / max(total, 1)))
                emit_progress(pct)
                if done and not announced_flush:
                    log("image data is written. Waiting for dd to finish and the card to settle.")
                    announced_flush = True
                elif not done and written - last_logged >= 32 * 1024 * 1024:
                    extra = " / " + gb(expected) if expected else ""
                    log("writing " + gb(written) + extra + " to the card")
                    last_logged = written
                if written == last_bytes:
                    stall += 1
                    if stall == 15 and not done:
                        log("WARN no new bytes for 15 seconds — disk may be slow")
                    if stall > 0 and stall % 15 == 0 and done:
                        log("still waiting for dd to exit… " + str(stall) + "s after last write")
                else:
                    stall = 0
                    last_bytes = written
        if dd.poll() is not None:
            break
        time.sleep(0.25)
    if buf.strip():
        log(buf.strip())
    dd.wait()
    written = proc_write_bytes(dd.pid) if dd.pid else None
    if written:
        log("dd exited after writing about " + gb(written))


def download_image(log, progress=None) -> str:
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, "raspios-lite-arm64.img.xz")
    if os.path.isfile(dest) and os.path.getsize(dest) > MIN_IMAGE_BYTES:
        log("image already in cache: " + dest + " (" + gb(os.path.getsize(dest)) + ")")
        if progress:
            progress(100)
        return dest
    log("starting download of Raspberry Pi OS Lite 64-bit")
    log("URL " + IMAGE_URL)
    log("saving to " + dest)
    tmp = dest + ".part"
    last_log = 0
    try:
        req = urllib.request.Request(IMAGE_URL, headers={"User-Agent": "rpi-imager"})
        with urllib.request.urlopen(req, timeout=60) as src:
            total = int(src.headers.get("Content-Length") or 0)
            if total:
                log("size " + gb(total) + " (" + str(total) + " bytes)")
            else:
                log("size unknown (server did not send Content-Length)")
            got = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = src.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if progress:
                        if total:
                            progress(int(got * 100 / total))
                        else:
                            progress(min(99, int(got * 100 / (800 * 1024 * 1024))))
                    if got - last_log >= 20 * 1024 * 1024 or got == chunk:
                        last_log = got
                        if total:
                            log("downloaded " + gb(got) + " / " + gb(total))
                        else:
                            log("downloaded " + gb(got))
                out.flush()
                os.fsync(out.fileno())
        os.replace(tmp, dest)
    except Fail:
        raise
    except Exception as exc:
        raise Fail("Download failed: " + str(exc)) from exc
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    size = os.path.getsize(dest)
    if size < MIN_IMAGE_BYTES:
        raise Fail("Downloaded file is too small: " + dest + " (" + str(size) + " bytes)")
    log("download complete: " + dest + " (" + gb(size) + ")")
    if progress:
        progress(100)
    return dest


def run_flash_job(job_path: str) -> int:
    try:
        with open(job_path, encoding="utf-8") as fh:
            job = json.load(fh)
    except Exception as exc:
        emit("ERROR cannot read job file: " + str(exc))
        return 2
    if not is_linux():
        emit("ERROR Linux only.")
        return 2
    if not is_root():
        emit("ERROR this step must run as administrator (root).")
        return 2
    try:
        image = job["image"]
        dev = job["dev"]
        size = job.get("size")
        ssid = job["ssid"]
        psk = job.get("psk") or ""
        hostname = job["hostname"]
        root_pw = job["root_pw"]
        emit("JOB image=" + image)
        emit("JOB disk=" + dev)
        assert_image(image)
        info = assert_safe_disk(dev, size)
        emit("disk " + info["label"])
        emit_progress(5)
        flash_with_dd(image, dev, emit)
        emit_progress(90)
        reread_partition_table(dev, emit)
        part = wait_for_boot_part(dev, emit)
        boot = mount_boot(part, "/mnt/rpi-bootfs", emit)
        try:
            apply(boot, ssid, psk, hostname, root_pw, emit)
        finally:
            emit("unmount " + boot)
            code, text = run_cmd(["umount", boot], check=False)
            if code != 0:
                run_cmd(["sync"], check=False)
                time.sleep(1)
                code, text = run_cmd(["umount", "-l", boot], check=False)
                if code != 0:
                    raise Fail("Cannot unmount boot partition:\n" + text)
            run_cmd(["sync"], check=False)
        emit_progress(100)
        emit("DONE card is ready. It is safe to remove.")
        emit("SSH user=" + USER_NAME + " password=" + USER_PASS + " host=" + hostname)
        emit("root password=" + root_pw)
        return 0
    except Fail as exc:
        emit("ERROR " + str(exc))
        return 1
    except Exception:
        emit("ERROR unexpected failure:")
        emit(traceback.format_exc())
        return 1


def pkexec_cmd(job_path: str):
    python = os.path.abspath(sys.executable)
    script = os.path.abspath(__file__)
    job_path = os.path.abspath(job_path)
    inner = [python, script, JOB_FLAG, job_path]
    if is_root():
        return inner, "already root"
    pkexec = shutil.which("pkexec")
    env = shutil.which("env") or "/usr/bin/env"
    if pkexec:
        return [pkexec, env, "PYTHONUNBUFFERED=1", python, script, JOB_FLAG, job_path], "pkexec"
    sudo = shutil.which("sudo")
    if sudo:
        return [sudo, env, "PYTHONUNBUFFERED=1", python, script, JOB_FLAG, job_path], "sudo"
    raise Fail(
        "This program must write the SD card, so it needs administrator rights.\n"
        "Install polkit (pkexec) or run this in a terminal:\n\n"
        "  sudo " + python + " " + script
    )


def box():
    f = QFrame()
    f.setObjectName("box")
    f.setLayout(QVBoxLayout())
    f.layout().setContentsMargins(12, 10, 12, 10)
    f.layout().setSpacing(6)
    return f


class FlashWorker(QObject):
    line = Signal(str)
    progress = Signal(int)
    finished = Signal(int)

    def __init__(self, argv):
        super().__init__()
        self.argv = argv
        self.proc = None

    def start(self):
        self.proc = QProcess()
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        program, *args = self.argv
        self.proc.start(program, args)
        if not self.proc.waitForStarted(8000):
            self.line.emit("ERROR could not start: " + " ".join(self.argv))
            self.line.emit(self.proc.errorString())
            self.finished.emit(1)

    def _read(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for raw in data.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("PROGRESS "):
                try:
                    self.progress.emit(int(line.split()[1]))
                except (IndexError, ValueError):
                    self.line.emit(line)
                continue
            self.line.emit(line)

    def _done(self, code, _status):
        self.finished.emit(int(code))


class DownloadWorker(QObject):
    line = Signal(str)
    progress = Signal(int)
    done = Signal(str)
    fail = Signal(str)

    def run(self):
        try:
            path = download_image(self.line.emit, self.progress.emit)
            self.done.emit(path)
        except Fail as exc:
            self.fail.emit(str(exc))
        except Exception:
            self.fail.emit(traceback.format_exc())


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Raspberry Pi Imager")
        self.resize(900, 560)
        self.root_pw = ""
        self.hostname = ""
        self.disks = []
        self.worker = None
        self.dl_thread = None
        self.dl_worker = None
        self.flashing = False
        self.downloading = False
        self._faded = False
        self._bar_anim = None
        saved = load_saved()

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("Raspberry Pi Imager")
        title.setObjectName("title")
        brand = QLabel("Marek Żytko Software™")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(title, 1)
        head.addWidget(brand, 0)
        hint = QLabel(
            "Linux writes Raspberry Pi OS onto the card with dd, then adds "
            "Wi-Fi, SSH and a first-boot user. The whole card is erased."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        col.addLayout(head)
        col.addWidget(hint)

        self.win_banner = QFrame()
        self.win_banner.setObjectName("banner")
        bl = QVBoxLayout(self.win_banner)
        bl.setContentsMargins(12, 10, 12, 10)
        note = QLabel(
            "Windows version will be available in the future.\n"
            "This tool works on Linux only for now. The form below is disabled."
        )
        note.setObjectName("warn")
        note.setWordWrap(True)
        bl.addWidget(note)
        col.addWidget(self.win_banner)
        self.win_banner.setVisible(not is_linux())

        self.linux_note = QLabel("")
        self.linux_note.setObjectName("hint")
        self.linux_note.setWordWrap(True)
        col.addWidget(self.linux_note)

        b0 = box()
        b0.layout().addWidget(QLabel("Raspberry Pi OS image"))
        img_row = QHBoxLayout()
        self.image = QLineEdit(str(saved.get("image") or ""))
        self.image.setPlaceholderText("path to .img, .img.xz or .zip")
        self.pick_img_btn = QPushButton("Browse…")
        self.pick_img_btn.clicked.connect(self.pick_image)
        self.dl_btn = QPushButton("Download Lite 64-bit")
        self.dl_btn.clicked.connect(self.download)
        img_row.addWidget(self.image, 1)
        img_row.addWidget(self.pick_img_btn)
        img_row.addWidget(self.dl_btn)
        b0.layout().addLayout(img_row)
        self.err_img = QLabel("")
        self.err_img.setObjectName("err")
        b0.layout().addWidget(self.err_img)

        b1 = box()
        row = QHBoxLayout()
        row.addWidget(QLabel("Card"))
        self.card = QComboBox()
        row.addWidget(self.card, 1)
        self.scan_btn = QPushButton("Refresh")
        self.scan_btn.clicked.connect(self.refresh)
        row.addWidget(self.scan_btn)
        b1.layout().addLayout(row)
        self.err_card = QLabel("")
        self.err_card.setObjectName("err")
        b1.layout().addWidget(self.err_card)

        b2 = box()
        b2.layout().addWidget(QLabel("Wi‑Fi (DHCP)"))
        g = QGridLayout()
        self.ssid = QLineEdit(str(saved.get("ssid") or ""))
        self.psk = QLineEdit(str(saved.get("psk") or ""))
        self.psk.setEchoMode(QLineEdit.Password)
        g.addWidget(QLabel("SSID"), 0, 0)
        g.addWidget(self.ssid, 0, 1)
        g.addWidget(QLabel("Password"), 1, 0)
        g.addWidget(self.psk, 1, 1)
        b2.layout().addLayout(g)
        self.err_net = QLabel("")
        self.err_net.setObjectName("err")
        b2.layout().addWidget(self.err_net)

        cred = box()
        self.host_lab = QLabel("host: —")
        self.host_lab.setObjectName("cred")
        self.user_lab = QLabel("ssh: " + USER_NAME + " / " + USER_PASS)
        self.user_lab.setObjectName("cred")
        self.pw_lab = QLabel("root password: (after RUN)")
        self.pw_lab.setObjectName("rootpw")
        self.pw_lab.setWordWrap(True)
        self.copy_btn = QPushButton("Copy root password")
        self.copy_btn.clicked.connect(self.copy_root)
        cred.layout().addWidget(self.host_lab)
        cred.layout().addWidget(self.user_lab)
        cred.layout().addWidget(self.pw_lab)
        cred.layout().addWidget(self.copy_btn)
        kh = QHBoxLayout()
        self.kh_host = QLineEdit(str(saved.get("ssh_host") or ""))
        self.kh_host.setPlaceholderText("IP or hostname")
        self.wipe_btn = QPushButton("Remove from known_hosts")
        self.wipe_btn.clicked.connect(self.wipe_known)
        kh.addWidget(self.kh_host)
        kh.addWidget(self.wipe_btn)
        cred.layout().addLayout(kh)
        cred.layout().addStretch(1)

        mid = QHBoxLayout()
        mid.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(b0)
        left.addWidget(b1)
        left.addWidget(b2)
        left.addStretch(1)
        mid.addLayout(left, 3)
        mid.addWidget(cred, 2)
        col.addLayout(mid)

        self.status_lab = QLabel("Ready.")
        self.status_lab.setObjectName("hint")
        col.addWidget(self.status_lab)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat("%p%")
        self.bar.setTextVisible(True)
        col.addWidget(self.bar)
        self.run_btn = QPushButton("RUN")
        self.run_btn.setObjectName("run")
        self.run_btn.clicked.connect(self.run)
        col.addWidget(self.run_btn)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        col.addWidget(self.log)
        for lab in self.findChildren(QLabel):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)

        self._interactive = [
            self.image, self.pick_img_btn, self.dl_btn, self.card, self.scan_btn,
            self.ssid, self.psk, self.copy_btn, self.kh_host, self.wipe_btn, self.run_btn,
        ]

        if is_linux():
            if is_root():
                self.linux_note.setText("Running as administrator. The card will be written with dd.")
            else:
                self.linux_note.setText(
                    "When you click RUN, Linux will ask for your password. "
                    "That is normal: writing an SD card needs administrator rights."
                )
            self.refresh()
            self._check_tools()
        else:
            self.set_enabled(False)
            self.say("Windows is not supported yet. Use Linux.")
            self.err_card.setText("Windows version will be available in the future.")

    def set_enabled(self, on: bool) -> None:
        for w in self._interactive:
            w.setEnabled(on)

    def showEvent(self, event):
        super().showEvent(event)
        if self._faded:
            return
        self._faded = True
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        self._fade = QPropertyAnimation(effect, b"opacity", self)
        self._fade.setDuration(280)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()

    def set_status(self, text: str, pct=None) -> None:
        self.status_lab.setText(text)
        if pct is None:
            return
        target = max(0, min(100, int(pct)))
        if self._bar_anim is not None:
            self._bar_anim.stop()
        anim = QPropertyAnimation(self.bar, b"value", self)
        anim.setDuration(180)
        anim.setStartValue(self.bar.value())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._bar_anim = anim
        anim.start()

    def say(self, text: str) -> None:
        self.log.append(text)
        self.log.moveCursor(QTextCursor.End)

    def _check_tools(self) -> None:
        missing = [name for name in ("dd", "lsblk", "mount", "umount") if not shutil.which(name)]
        if missing:
            msg = "Missing system tools: " + ", ".join(missing)
            self.err_card.setText(msg)
            self.say("ERROR " + msg)
        if not shutil.which("pkexec") and not is_root():
            self.say(
                "WARN pkexec not found. If RUN cannot ask for a password, open a terminal and run:\n"
                "  sudo " + os.path.abspath(sys.executable) + " " + os.path.abspath(__file__)
            )

    def pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Raspberry Pi OS image",
            self.image.text() or os.path.expanduser("~"),
            "Images (*.img *.img.xz *.xz *.zip);;All (*.*)",
        )
        if path:
            self.image.setText(path)

    def download(self):
        if not is_linux():
            return
        if self.downloading:
            self.say("download already running")
            return
        if self.flashing:
            self.say("wait until the card write finishes")
            return
        self.err_img.setText("")
        self.downloading = True
        self.dl_btn.setEnabled(False)
        self.set_status("Downloading Raspberry Pi OS Lite 64-bit…", 0)
        self.say("download started in the background")
        self.dl_thread = QThread()
        self.dl_worker = DownloadWorker()
        self.dl_worker.moveToThread(self.dl_thread)
        self.dl_thread.started.connect(self.dl_worker.run)
        self.dl_worker.line.connect(self.say)
        self.dl_worker.progress.connect(self._on_download_progress)
        self.dl_worker.done.connect(self._on_download_done)
        self.dl_worker.fail.connect(self._on_download_fail)
        self.dl_worker.done.connect(self.dl_thread.quit)
        self.dl_worker.fail.connect(self.dl_thread.quit)
        self.dl_thread.finished.connect(self.dl_worker.deleteLater)
        self.dl_thread.start()

    def _on_download_progress(self, pct: int):
        self.set_status("Downloading Raspberry Pi OS Lite 64-bit… " + str(pct) + "%", pct)

    def _on_download_done(self, path: str):
        self.downloading = False
        self.dl_btn.setEnabled(is_linux() and not self.flashing)
        self.image.setText(path)
        self.set_status("Download finished.", 100)
        self.say("image ready: " + path)

    def _on_download_fail(self, err: str):
        self.downloading = False
        self.dl_btn.setEnabled(is_linux() and not self.flashing)
        self.set_status("Download failed.", 0)
        self.err_img.setText(err.splitlines()[0][:240])
        self.say("ERROR " + err)

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
            self.say("Enter an IP, or click RUN first so a hostname exists.")
            return
        for t in seen:
            self.say(drop_known_host(t))

    def copy_root(self):
        if not self.root_pw:
            self.say("No root password yet. Click RUN first.")
            return
        QApplication.clipboard().setText(self.root_pw)
        self.say("root password copied")

    def refresh(self):
        if not is_linux():
            self.card.clear()
            self.card.addItem("Windows: unavailable")
            return
        self.card.clear()
        try:
            self.disks = list_disks()
        except Fail as exc:
            self.disks = []
            self.err_card.setText(str(exc))
            self.say("ERROR " + str(exc))
            self.card.addItem("could not list disks")
            return
        except Exception as exc:
            self.disks = []
            self.err_card.setText(str(exc))
            self.say("ERROR listing disks: " + str(exc))
            self.card.addItem("could not list disks")
            return
        usable = [d for d in self.disks if not d["system"] and d["size"] <= MAX_CARD_BYTES]
        blocked = [d for d in self.disks if d not in usable]
        if not usable:
            self.card.addItem("insert an SD card and click Refresh")
            self.say("no writable card found")
            for d in blocked:
                self.say("skipped " + d["label"])
            return
        for d in usable:
            self.card.addItem(d["label"], d["dev"])
        self.say("found " + str(len(usable)) + " writable disk(s)")
        for d in blocked:
            self.say("skipped " + d["label"])

    def selected(self):
        dev = self.card.currentData()
        if not dev:
            return None
        for d in self.disks:
            if d["dev"] == dev:
                return d
        return None

    def run(self):
        if not is_linux():
            self.err_card.setText("Windows version will be available in the future.")
            return
        if self.flashing:
            self.say("already writing")
            return
        if self.downloading:
            self.say("wait until the download finishes")
            self.err_img.setText("Download is still running.")
            return
        self.err_card.setText("")
        self.err_net.setText("")
        self.err_img.setText("")
        disk = self.selected()
        image = self.image.text().strip()
        ssid = self.ssid.text().strip()
        bad = False
        if not image:
            self.err_img.setText("Choose an image file, or download Lite 64-bit.")
            bad = True
        else:
            try:
                assert_image(image)
                image_kind(image)
            except Fail as exc:
                self.err_img.setText(str(exc))
                bad = True
        if not disk:
            self.err_card.setText("Select the SD card. Refresh if it is not listed.")
            bad = True
        else:
            try:
                assert_safe_disk(disk["dev"], disk["size"])
            except Fail as exc:
                self.err_card.setText(str(exc))
                bad = True
        if not ssid:
            self.err_net.setText("Enter the Wi-Fi name (SSID).")
            bad = True
        if bad:
            self.say("stopped: fix the errors above")
            return

        ask = QMessageBox(self)
        ask.setWindowTitle("Erase this card?")
        ask.setIcon(QMessageBox.Warning)
        ask.setText(
            "This will ERASE the whole card and write a new system.\n\n"
            + disk["label"]
            + "\n\nImage:\n"
            + image
        )
        ask.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        ask.setDefaultButton(QMessageBox.No)
        if ask.exec() != QMessageBox.Yes:
            self.say("cancelled")
            return

        self.hostname = make_hostname()
        self.root_pw = make_root_pw()
        self.host_lab.setText("host: " + self.hostname)
        self.pw_lab.setText("root password: " + self.root_pw)
        self.say("=== RUN " + disk["dev"] + " ===")
        self.say("hostname " + self.hostname)

        job = {
            "image": os.path.abspath(image),
            "dev": disk["dev"],
            "size": disk["size"],
            "ssid": ssid,
            "psk": self.psk.text(),
            "hostname": self.hostname,
            "root_pw": self.root_pw,
        }
        os.makedirs(CACHE, exist_ok=True)
        job_path = os.path.join(CACHE, "job.json")
        try:
            with open(job_path, "w", encoding="utf-8") as fh:
                json.dump(job, fh)
                fh.write("\n")
            os.chmod(job_path, 0o600)
        except OSError as exc:
            self.err_card.setText("Cannot write job file: " + str(exc))
            self.say("ERROR " + str(exc))
            return
        try:
            save_saved({
                "ssid": ssid,
                "psk": self.psk.text(),
                "ssh_host": self.kh_host.text().strip(),
                "image": image,
            })
        except Fail as exc:
            self.say("WARN settings not saved: " + str(exc))

        try:
            argv, how = pkexec_cmd(job_path)
        except Fail as exc:
            self.err_card.setText(str(exc))
            self.say("ERROR " + str(exc))
            return
        self.say("privilege method: " + how)
        self.say("starting: " + " ".join(shlex.quote(a) for a in argv))
        if how == "pkexec":
            self.say("a password window should appear now")
        elif how == "sudo":
            self.say("sudo may ask for your password in the terminal that started this program")

        self.flashing = True
        self.set_enabled(False)
        self.set_status("Writing the card…", 1)
        self.worker = FlashWorker(argv)
        self.worker.line.connect(self._on_line)
        self.worker.progress.connect(self._on_flash_progress)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    def _on_flash_progress(self, pct: int):
        self.set_status("Writing the card… " + str(pct) + "%", pct)

    def _on_line(self, line: str):
        self.say(line)
        if line.startswith("ERROR"):
            self.err_card.setText(line[6:].strip()[:240])

    def _on_done(self, code: int):
        self.flashing = False
        if is_linux():
            self.set_enabled(True)
        if code == 0:
            self.set_status("Card is ready.", 100)
            self.say("finished. Eject the card, put it in the Pi, wait 2–3 minutes.")
        else:
            self.set_status("Write failed.", self.bar.value())
            self.say("failed with exit code " + str(code))
            if not self.err_card.text():
                self.err_card.setText("Write failed. Read the log. Nothing was assumed to work.")


def drop_known_host(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "No address given."
    try:
        subprocess.check_call(
            ["ssh-keygen", "-R", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return "Removed known_hosts entry for " + name
    except FileNotFoundError:
        pass
    except Exception as exc:
        emit("WARN ssh-keygen -R failed: " + str(exc))
    path = os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts")
    if not os.path.isfile(path):
        return "No known_hosts file at " + path
    keep = []
    dropped = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                token = line.split()[0] if line.split() else ""
                if name in token.split(","):
                    dropped += 1
                    continue
                keep.append(line)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(keep)
    except OSError as exc:
        return "Could not edit known_hosts: " + str(exc)
    if dropped:
        return "Removed " + str(dropped) + " line(s) for " + name
    return "No known_hosts entry for " + name


def main():
    if JOB_FLAG in sys.argv:
        try:
            idx = sys.argv.index(JOB_FLAG)
            job = sys.argv[idx + 1]
        except IndexError:
            emit("ERROR missing job file path")
            sys.exit(2)
        sys.exit(run_flash_job(job))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#07070a"))
    pal.setColor(QPalette.WindowText, QColor("#ece8f4"))
    pal.setColor(QPalette.Base, QColor("#101014"))
    pal.setColor(QPalette.Text, QColor("#ece8f4"))
    pal.setColor(QPalette.Button, QColor("#16161c"))
    pal.setColor(QPalette.ButtonText, QColor("#ece8f4"))
    pal.setColor(QPalette.Highlight, QColor("#6d3dff"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
    win = App()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
