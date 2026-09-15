#!/usr/bin/env python3
"""ODŁOŻONE: surowy zapis obrazu Lite na PhysicalDrive (Windows/Linux).
Nie używane w aktualnym instalatorze — user nagrywa system Raspberry Pi Imagerem.
"""

from __future__ import annotations

import json
import lzma
import os
import re
import subprocess
import sys
import time
import urllib.request

from PySide6.QtCore import QObject, QThread, Signal
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
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

IMAGE_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "reticulum-wikipedia")
REPO_URL = "https://github.com/swizzyswizzy/reticulum-wikipedia.git"
MAX_CARD_BYTES = 256 * 1024 * 1024 * 1024

STYLE = """
QMainWindow, QWidget#root { background: #1b1e1c; color: #d7ddd4; }
QLabel { color: #d7ddd4; font-size: 13px; }
QLabel#title { color: #cfe7c4; font-size: 18px; font-weight: 700; }
QLabel#hint { color: #8b9486; font-size: 12px; }
QLabel#err { color: #e08a7a; font-size: 12px; }
QLineEdit, QComboBox {
  background: #111411; color: #e8eee4; border: 1px solid #3a4338;
  padding: 6px 8px; selection-background-color: #3d6b46;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #7dba8a; }
QRadioButton { spacing: 8px; }
QRadioButton::indicator { width: 12px; height: 12px; border: 1px solid #7dba8a; background: #111411; }
QRadioButton::indicator:checked { background: #7dba8a; }
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
QProgressBar { border: 1px solid #3a4338; background: #111411; color: #cfe7c4; text-align: center; height: 16px; }
QProgressBar::chunk { background: #3c6b46; }
QFrame#box { border: 1px solid #2c332c; background: #161916; }
"""


def gb(n):
    try:
        return f"{int(n) / (1024 ** 3):.1f} GB"
    except Exception:
        return "?"


def ps(cmd):
    return subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", cmd],
        text=True, stderr=subprocess.STDOUT,
    )


def list_disks():
    disks = []
    if os.name == "nt":
        raw = ps(
            "Get-CimInstance Win32_DiskDrive | "
            "Select-Object Index,Model,Size,InterfaceType,MediaType | ConvertTo-Json -Compress"
        ).strip()
        data = json.loads(raw or "[]")
        if isinstance(data, dict):
            data = [data]
        for d in data:
            size = int(d.get("Size") or 0)
            idx = d.get("Index")
            if idx is None or int(idx) == 0 or size <= 0 or size > MAX_CARD_BYTES:
                continue
            iface = (d.get("InterfaceType") or "").upper()
            if iface in ("NVME", "IDE") and "REMOVABLE" not in (d.get("MediaType") or "").upper():
                continue
            model = (d.get("Model") or "Dysk").strip()
            kind = (d.get("InterfaceType") or d.get("MediaType") or "").strip()
            disks.append({
                "id": str(idx),
                "label": f"{model} · {gb(size)}" + (f" · {kind}" if kind else ""),
                "dev": f"\\\\.\\PhysicalDrive{idx}",
                "size": size,
                "index": int(idx),
            })
    else:
        raw = subprocess.check_output(["lsblk", "-J", "-b", "-o", "NAME,SIZE,MODEL,TRAN,TYPE,RM,MOUNTPOINT"], text=True)
        data = json.loads(raw)
        for d in data.get("blockdevices") or []:
            if d.get("type") != "disk":
                continue
            size = int(d.get("size") or 0)
            if size <= 0 or size > MAX_CARD_BYTES:
                continue
            mounts = []

            def walk(node):
                if node.get("mountpoint"):
                    mounts.append(node["mountpoint"])
                for ch in node.get("children") or []:
                    walk(ch)

            walk(d)
            if any(m in ("/", "/boot", "/home") for m in mounts):
                continue
            name = d.get("name")
            model = (d.get("model") or name or "dysk").strip()
            disks.append({
                "id": name,
                "label": f"{model} · {gb(size)}" + (f" · {d.get('tran')}" if d.get("tran") else ""),
                "dev": "/dev/" + name,
                "size": size,
                "index": name,
            })
    return disks


def is_admin():
    if os.name != "nt":
        return os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    if os.name != "nt" or is_admin():
        return
    import ctypes
    script = os.path.abspath(__file__)
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}"', os.path.dirname(script), 1,
    )
    if rc > 32:
        sys.exit(0)


def prepare_windows_disk(index):
    ps(
        f"Get-Disk -Number {index} | Get-Partition | ForEach-Object {{ "
        f"if ($_.DriveLetter) {{ $d = $_.DriveLetter + ':'; "
        f"try {{ mountvol $d /P }} catch {{}} }} }}"
    )


def win_last_error():
    import ctypes
    err = ctypes.GetLastError()
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.kernel32.FormatMessageW(0x00001000, None, err, 0, buf, 255, None)
    return err, buf.value.strip()


FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
FSCTL_ALLOW_EXTENDED_DASD_IO = 0x00090083
IOCTL_DISK_SET_DISK_ATTRIBUTES = 0x0007C0C4


def win_ioctl(handle, code):
    import ctypes
    from ctypes import wintypes
    returned = wintypes.DWORD()
    ctypes.windll.kernel32.DeviceIoControl(handle, code, None, 0, None, 0, ctypes.byref(returned), None)


def win_clear_readonly(handle):
    import ctypes
    from ctypes import wintypes

    class SET_DISK_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.DWORD),
            ("Persist", wintypes.BOOLEAN),
            ("Reserved1", ctypes.c_ubyte * 3),
            ("Attributes", ctypes.c_ulonglong),
            ("AttributesMask", ctypes.c_ulonglong),
            ("Reserved2", wintypes.DWORD * 4),
        ]

    info = SET_DISK_ATTRIBUTES()
    info.Version = ctypes.sizeof(SET_DISK_ATTRIBUTES)
    info.Persist = 1
    info.Attributes = 0
    info.AttributesMask = 0x1 | 0x2
    returned = wintypes.DWORD()
    ctypes.windll.kernel32.DeviceIoControl(
        handle, IOCTL_DISK_SET_DISK_ATTRIBUTES,
        ctypes.byref(info), ctypes.sizeof(info),
        None, 0, ctypes.byref(returned), None,
    )


def win_kill_writeprotect_policy():
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\StorageDevicePolicies")
        winreg.SetValueEx(key, "WriteProtect", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
    except Exception:
        pass


def win_open_path(path, share=3):
    import ctypes
    from ctypes import wintypes
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW(
        path, GENERIC_READ | GENERIC_WRITE, share,
        None, OPEN_EXISTING, 0, None,
    )
    if handle == ctypes.c_void_p(-1).value or int(handle) in (-1, 0xFFFFFFFF):
        err, msg = win_last_error()
        raise RuntimeError(f"{path} ({err}) {msg}")
    return handle


def win_letters(index):
    raw = ps(f"Get-Partition -DiskNumber {index} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DriveLetter")
    out = []
    for line in raw.splitlines():
        s = line.strip().rstrip(":")
        if len(s) == 1 and s.isalpha():
            out.append(s.upper())
    return out


def win_ensure_letter(index):
    letters = win_letters(index)
    if letters:
        return letters
    script = os.path.join(CACHE, "diskpart-part.txt")
    os.makedirs(CACHE, exist_ok=True)
    with open(script, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(f"select disk {index}\r\n")
        fh.write("online disk noerr\r\n")
        fh.write("attribute disk clear readonly\r\n")
        fh.write("create partition primary noerr\r\n")
        fh.write("assign noerr\r\n")
    subprocess.check_output(["diskpart", "/s", script], text=True, stderr=subprocess.STDOUT)
    time.sleep(2)
    return win_letters(index)


def win_lock_volumes(letters):
    handles = []
    for letter in letters:
        path = f"\\\\.\\{letter}:"
        h = win_open_path(path, share=3)
        win_ioctl(h, FSCTL_ALLOW_EXTENDED_DASD_IO)
        win_ioctl(h, FSCTL_LOCK_VOLUME)
        win_ioctl(h, FSCTL_DISMOUNT_VOLUME)
        handles.append(h)
    return handles


def win_open_disk(index):
    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    INVALID = ctypes.c_void_p(-1).value

    win_kill_writeprotect_policy()
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    paths = [f"\\\\.\\PhysicalDrive{index}", f"\\\\.\\Harddisk{index}Partition0"]
    last = "brak uchwytu"
    for path in paths:
        for share in (0, 3):
            handle = k32.CreateFileW(
                path, GENERIC_READ | GENERIC_WRITE, share,
                None, OPEN_EXISTING, FILE_FLAG_WRITE_THROUGH, None,
            )
            if handle == INVALID or int(handle) in (-1, 0xFFFFFFFF):
                err, msg = win_last_error()
                last = f"{path} share={share} ({err}) {msg}"
                continue
            win_clear_readonly(handle)
            win_ioctl(handle, FSCTL_ALLOW_EXTENDED_DASD_IO)
            win_ioctl(handle, FSCTL_LOCK_VOLUME)
            win_ioctl(handle, FSCTL_DISMOUNT_VOLUME)
            return handle
    raise RuntimeError("CreateFile " + last)


def win_write_disk(handle, data):
    import ctypes
    from ctypes import wintypes
    written = wintypes.DWORD()
    ok = ctypes.windll.kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
    if not ok:
        err, msg = win_last_error()
        raise RuntimeError(f"WriteFile ({err}) {msg}")
    return written.value


def win_close(handle):
    import ctypes
    ctypes.windll.kernel32.FlushFileBuffers(handle)
    ctypes.windll.kernel32.CloseHandle(handle)


def win_clean_and_offline(index):
    script = os.path.join(CACHE, "diskpart.txt")
    os.makedirs(CACHE, exist_ok=True)
    with open(script, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("automount disable\r\n")
        fh.write("automount scrub\r\n")
        fh.write(f"select disk {index}\r\n")
        fh.write("online disk noerr\r\n")
        fh.write("attribute disk clear readonly\r\n")
        fh.write("clean\r\n")
        fh.write("online disk noerr\r\n")
        fh.write("attribute disk clear readonly\r\n")
    out = subprocess.check_output(["diskpart", "/s", script], text=True, stderr=subprocess.STDOUT)
    try:
        ps(
            f"Set-Disk -Number {index} -IsReadOnly $false -ErrorAction SilentlyContinue; "
            f"Set-Disk -Number {index} -IsOffline $false -ErrorAction SilentlyContinue"
        )
    except Exception:
        pass
    return out


def read_boot_text(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if not data or b"\x00" in data[:80]:
        raise RuntimeError("plik boot uszkodzony")
    return data.decode("ascii", errors="strict")


def write_boot_text(path, text):
    raw = text.replace("\r\n", "\n").encode("ascii")
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def cmdline_ok(path):
    try:
        text = read_boot_text(path)
    except Exception:
        return False
    return "root=" in text and "console=" in text


def find_bootfs(timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.name == "nt":
            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:/"
                cmd = os.path.join(root, "cmdline.txt")
                cfg = os.path.join(root, "config.txt")
                if os.path.isfile(cmd) and os.path.isfile(cfg) and cmdline_ok(cmd):
                    return root
        else:
            for base in ("/media", "/run/media", "/Volumes", "/mnt"):
                if not os.path.isdir(base):
                    continue
                for dirpath, dirnames, filenames in os.walk(base):
                    cmd = os.path.join(dirpath, "cmdline.txt")
                    if "cmdline.txt" in filenames and "config.txt" in filenames and cmdline_ok(cmd):
                        return dirpath
                    if dirpath[len(base):].count(os.sep) >= 3:
                        dirnames.clear()
        time.sleep(2)
    return ""


def patch_cmdline(text: str) -> str:
    line = " ".join(text.replace("\n", " ").split())
    line = re.sub(r"\bsystemd\.run\S*", "", line)
    line = re.sub(r"\bmodules-load=\S*", "", line)
    line = re.sub(r"\s+", " ", line).strip()
    if re.search(r"\brootwait\b", line):
        line = re.sub(r"\brootwait\b", "rootwait modules-load=dwc2,g_ether", line, count=1)
    else:
        line += " modules-load=dwc2,g_ether"
    line += " systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=none systemd.run_failure_action=none"
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


def nm_file(kind, ssid, psk, dhcp, ip, prefix, gw, dns):
    lines = ["[connection]", "id=rns-net", f"type={'wifi' if kind == 'wifi' else 'ethernet'}", "autoconnect=true", ""]
    if kind == "wifi":
        lines += ["[wifi]", f"ssid={ssid}", "mode=infrastructure", "", "[wifi-security]", "key-mgmt=wpa-psk", f"psk={psk}", ""]
    else:
        lines += ["[ethernet]", ""]
    lines += ["[ipv4]"]
    if dhcp:
        lines.append("method=auto")
    else:
        lines += ["method=manual", f"address1={ip}/{prefix},{gw}", f"dns={dns};"]
    lines += ["", "[ipv6]", "method=auto", ""]
    return "\n".join(lines) + "\n"


def firstrun_script(kind):
    repo_q = REPO_URL.replace("'", "'\\''")
    return f"""#!/bin/bash
set +e
exec > /boot/firmware/rns-firstboot.log 2>&1 || exec > /boot/rns-firstboot.log 2>&1
echo "RNS firstboot start"
BOOT=/boot/firmware
[ -f "$BOOT/cmdline.txt" ] || BOOT=/boot
if ! id rnode >/dev/null 2>&1; then
  useradd -m -s /bin/bash rnode
  echo 'rnode:rnode' | chpasswd
  usermod -aG sudo rnode
  echo 'rnode ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/rnode
fi
systemctl enable ssh >/dev/null 2>&1
systemctl start ssh >/dev/null 2>&1
if [ -f "$BOOT/rns-net.nmconnection" ]; then
  mkdir -p /etc/NetworkManager/system-connections
  cp "$BOOT/rns-net.nmconnection" /etc/NetworkManager/system-connections/rns-net.nmconnection
  chmod 600 /etc/NetworkManager/system-connections/rns-net.nmconnection
  nmcli connection reload >/dev/null 2>&1
  nmcli connection up rns-net >/dev/null 2>&1
fi
echo "czekam na siec ({kind})"
for i in $(seq 1 45); do
  ip=$(hostname -I 2>/dev/null | awk '{{print $1}}')
  [ -n "$ip" ] && break
  sleep 2
done
echo "IP=$ip"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wget ca-certificates git python3 python3-pip openssl
RAW="{repo_q}"
RAW="${{RAW%.git}}"
RAW="https://raw.githubusercontent.com/${{RAW#https://github.com/}}/refs/heads/master/install.sh"
wget -O /tmp/rns-install.sh "$RAW" && bash /tmp/rns-install.sh '{repo_q}'
sed -i -E 's/ systemd.run[^ ]*//g' "$BOOT/cmdline.txt" 2>/dev/null
rm -f "$BOOT/firstrun.sh" "$BOOT/rns-net.nmconnection"
echo "RNS firstboot done"
"""


def write_boot(boot, kind, wifi, static):
    cmd_path = os.path.join(boot, "cmdline.txt")
    cfg_path = os.path.join(boot, "config.txt")
    cmd = read_boot_text(cmd_path)
    write_boot_text(cmd_path, patch_cmdline(cmd))
    if not cmdline_ok(cmd_path):
        raise RuntimeError("cmdline.txt zepsuty po zapisie")
    write_boot_text(os.path.join(boot, "firstrun.sh"), firstrun_script(kind))
    write_boot_text(cfg_path, patch_config(read_boot_text(cfg_path)))
    if kind == "wifi" or kind == "ethernet" or not static["dhcp"]:
        write_boot_text(
            os.path.join(boot, "rns-net.nmconnection"),
            nm_file(
                kind, wifi["ssid"], wifi["psk"], static["dhcp"],
                static["ip"], static["prefix"], static["gw"], static["dns"],
            ),
        )
    write_boot_text(os.path.join(boot, "ssh"), "")


class Worker(QObject):
    progress = Signal(int)
    log = Signal(str)
    done = Signal()
    fail = Signal(str)

    def __init__(self, disk, kind, wifi, static):
        super().__init__()
        self.disk = disk
        self.kind = kind
        self.wifi = wifi
        self.static = static

    def run(self):
        try:
            xz = self.download()
            self.flash(xz)
            self.log.emit("czekam aż bootfs będzie czytelny…")
            if os.name == "nt":
                try:
                    ps("Update-HostStorageCache")
                except Exception:
                    pass
                time.sleep(4)
            boot = find_bootfs(120)
            if not boot:
                raise RuntimeError("po wgraniu nie widzę bootfs — wyjmij i włóż kartę, Odśwież nie pomoże, uruchom RUN jeszcze raz")
            self.log.emit("dopisuję firstboot")
            write_boot(boot, self.kind, self.wifi, self.static)
            self.progress.emit(100)
            self.done.emit()
        except Exception as exc:
            self.fail.emit(str(exc))

    def download(self):
        os.makedirs(CACHE, exist_ok=True)
        dest = os.path.join(CACHE, "raspios-lite-arm64.img.xz")
        if os.path.isfile(dest) and os.path.getsize(dest) > 20_000_000:
            self.log.emit("obraz z cache")
            self.progress.emit(20)
            return dest
        self.log.emit("pobieram Raspberry Pi OS Lite 64-bit")
        req = urllib.request.Request(IMAGE_URL, headers={"User-Agent": "rns-imager"})
        with urllib.request.urlopen(req, timeout=60) as src:
            total = int(src.headers.get("Content-Length") or 0)
            got = 0
            tmp = dest + ".part"
            with open(tmp, "wb") as out:
                while True:
                    chunk = src.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if total:
                        self.progress.emit(int(got * 20 / total))
            os.replace(tmp, dest)
        self.progress.emit(20)
        return dest

    def flash(self, xz):
        self.log.emit("kasuję kartę i wgrywam obraz")
        if os.name == "nt":
            self.flash_windows(xz)
        else:
            self.flash_unix(xz)
        self.progress.emit(95)

    def flash_unix(self, xz):
        flags = os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(self.disk["dev"], flags)
        written = 0
        try:
            with lzma.open(xz, "rb") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    os.write(fd, chunk)
                    written += len(chunk)
                    self.progress.emit(20 + min(75, int(written * 75 / (2800 * 1024 * 1024))))
            os.fsync(fd)
        finally:
            os.close(fd)
        self.log.emit(f"wgrane {gb(written)}")

    def flash_windows(self, xz):
        if not is_admin():
            raise RuntimeError("brak uprawnień administratora — kliknij bat i zatwierdź UAC")
        idx = self.disk["index"]
        win_kill_writeprotect_policy()
        self.log.emit("zamykam woluminy (schemat Win32 Disk Imager / Rufus)")
        letters = win_ensure_letter(idx)
        self.log.emit("litery: " + (", ".join(letters) if letters else "(brak)"))
        locked = []
        if letters:
            locked = win_lock_volumes(letters)
            self.log.emit("woluminy zablokowane")
        handle = None
        written = 0
        try:
            handle = win_open_path(f"\\\\.\\PhysicalDrive{idx}", share=0)
            win_clear_readonly(handle)
            win_ioctl(handle, FSCTL_ALLOW_EXTENDED_DASD_IO)
            win_ioctl(handle, FSCTL_LOCK_VOLUME)
            self.log.emit("PhysicalDrive otwarty na wyłączność")
            with lzma.open(xz, "rb") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    if len(chunk) % 512:
                        chunk = chunk + b"\x00" * (512 - len(chunk) % 512)
                    off = 0
                    while off < len(chunk):
                        off += win_write_disk(handle, chunk[off:])
                    written += len(chunk)
                    self.progress.emit(20 + min(75, int(written * 75 / (2800 * 1024 * 1024))))
        finally:
            if handle:
                win_close(handle)
            for h in locked:
                try:
                    win_close(h)
                except Exception:
                    pass
        if written == 0:
            raise RuntimeError("zero bajtów zapisanych")
        self.log.emit(f"wgrane {gb(written)}")
        try:
            ps("Update-HostStorageCache")
        except Exception:
            pass


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
        self.setWindowTitle("Reticulum SD")
        self.resize(640, 640)
        self.disks = []
        self.thread = None
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)

        title = QLabel("RETICULUM  ·  karta SD")
        title.setObjectName("title")
        hint = QLabel("Wybierz nośnik. RUN ściągnie Lite 64-bit i wgra go od zera (cała karta idzie w diabły).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(hint)

        b1 = box()
        row = QHBoxLayout()
        row.addWidget(QLabel("Nośnik"))
        self.card = QComboBox()
        row.addWidget(self.card, 1)
        scan = QPushButton("Odśwież")
        scan.clicked.connect(self.refresh)
        row.addWidget(scan)
        b1.layout().addLayout(row)
        self.err_card = QLabel("")
        self.err_card.setObjectName("err")
        b1.layout().addWidget(self.err_card)
        col.addWidget(b1)

        b2 = box()
        r = QHBoxLayout()
        self.eth = QRadioButton("Ethernet HAT")
        self.wifi = QRadioButton("Wi-Fi")
        self.eth.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.eth)
        g.addButton(self.wifi)
        r.addWidget(self.eth)
        r.addWidget(self.wifi)
        r.addStretch()
        b2.layout().addLayout(r)
        self.wifi_box = QWidget()
        wg = QGridLayout(self.wifi_box)
        wg.setContentsMargins(0, 6, 0, 0)
        self.ssid = QLineEdit()
        self.psk = QLineEdit()
        self.psk.setEchoMode(QLineEdit.Password)
        wg.addWidget(QLabel("SSID"), 0, 0)
        wg.addWidget(self.ssid, 0, 1)
        wg.addWidget(QLabel("Hasło"), 1, 0)
        wg.addWidget(self.psk, 1, 1)
        b2.layout().addWidget(self.wifi_box)
        self.err_net = QLabel("")
        self.err_net.setObjectName("err")
        b2.layout().addWidget(self.err_net)
        col.addWidget(b2)

        b3 = box()
        r = QHBoxLayout()
        self.dhcp = QRadioButton("DHCP")
        self.static = QRadioButton("Stałe IP")
        self.dhcp.setChecked(True)
        g2 = QButtonGroup(self)
        g2.addButton(self.dhcp)
        g2.addButton(self.static)
        r.addWidget(self.dhcp)
        r.addWidget(self.static)
        r.addStretch()
        b3.layout().addLayout(r)
        self.ip_box = QWidget()
        ig = QGridLayout(self.ip_box)
        ig.setContentsMargins(0, 6, 0, 0)
        self.ip = QLineEdit("192.168.0.50")
        self.prefix = QLineEdit("24")
        self.gw = QLineEdit("192.168.0.1")
        self.dns = QLineEdit("1.1.1.1")
        ig.addWidget(QLabel("IP"), 0, 0)
        ig.addWidget(self.ip, 0, 1)
        ig.addWidget(QLabel("/"), 0, 2)
        ig.addWidget(self.prefix, 0, 3)
        ig.addWidget(QLabel("Bramka"), 1, 0)
        ig.addWidget(self.gw, 1, 1, 1, 3)
        ig.addWidget(QLabel("DNS"), 2, 0)
        ig.addWidget(self.dns, 2, 1, 1, 3)
        b3.layout().addWidget(self.ip_box)
        self.err_ip = QLabel("")
        self.err_ip.setObjectName("err")
        b3.layout().addWidget(self.err_ip)
        col.addWidget(b3)

        info = QLabel("Po starcie Pi dociągnie reticulum-wikipedia z github.com/swizzyswizzy/reticulum-wikipedia")
        info.setObjectName("hint")
        info.setWordWrap(True)
        col.addWidget(info)

        self.bar = QProgressBar()
        col.addWidget(self.bar)
        run = QPushButton("RUN")
        run.setObjectName("run")
        run.clicked.connect(self.run)
        col.addWidget(run)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        col.addWidget(self.log)

        self.eth.toggled.connect(self.sync)
        self.wifi.toggled.connect(self.sync)
        self.dhcp.toggled.connect(self.sync)
        self.static.toggled.connect(self.sync)
        self.sync()
        self.refresh()
        if os.name == "nt" and not is_admin():
            self.say("uruchom jako administrator, inaczej zapis całego dysku padnie")

    def sync(self):
        self.wifi_box.setVisible(self.wifi.isChecked())
        self.ip_box.setVisible(self.static.isChecked())

    def say(self, text):
        self.log.append(text)
        self.log.moveCursor(QTextCursor.End)

    def mark(self, w, bad):
        w.setStyleSheet("border: 1px solid #e08a7a;" if bad else "")

    def refresh(self):
        self.card.clear()
        try:
            self.disks = list_disks()
        except Exception as exc:
            self.disks = []
            self.say("skan dysków: " + str(exc))
        if not self.disks:
            self.card.addItem("włóż kartę i odśwież")
            return
        for d in self.disks:
            self.card.addItem(d["label"])
        self.say("widzę " + str(len(self.disks)) + " nośnik(i)")

    def selected(self):
        i = self.card.currentIndex()
        if i < 0 or i >= len(self.disks):
            return None
        return self.disks[i]

    def run(self):
        self.err_card.setText("")
        self.err_net.setText("")
        self.err_ip.setText("")
        disk = self.selected()
        bad = False
        if not disk:
            self.err_card.setText("tu: wybierz nośnik z listy")
            bad = True
        if self.wifi.isChecked() and not self.ssid.text().strip():
            self.err_net.setText("tu: brak SSID")
            self.mark(self.ssid, True)
            bad = True
        else:
            self.mark(self.ssid, False)
        if self.static.isChecked() and not re.match(r"^\d+\.\d+\.\d+\.\d+$", self.ip.text().strip()):
            self.err_ip.setText("tu: IP wygląda źle")
            self.mark(self.ip, True)
            bad = True
        else:
            self.mark(self.ip, False)
        if bad:
            self.say("stop")
            return
        if QMessageBox.question(
            self, "Kasowanie karty",
            f"To WYMAŻE cały nośnik:\n{disk['label']}\n\nKontynuować?",
        ) != QMessageBox.Yes:
            self.say("anulowane")
            return
        kind = "wifi" if self.wifi.isChecked() else "ethernet"
        self.thread = QThread()
        self.worker = Worker(
            disk, kind,
            {"ssid": self.ssid.text(), "psk": self.psk.text()},
            {
                "dhcp": self.dhcp.isChecked(),
                "ip": self.ip.text().strip(),
                "prefix": self.prefix.text().strip() or "24",
                "gw": self.gw.text().strip(),
                "dns": self.dns.text().strip() or "1.1.1.1",
            },
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.log.connect(self.say)
        self.worker.done.connect(lambda: self.say("gotowe. karta do Pi, zasilanie, 3–5 min, http://IP:4240"))
        self.worker.fail.connect(lambda e: self.fail(e))
        self.worker.done.connect(self.thread.quit)
        self.worker.fail.connect(self.thread.quit)
        self.thread.start()

    def fail(self, err):
        self.err_card.setText("tu: " + err)
        self.say("błąd: " + err)


def main():
    elevate()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    win = App()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
