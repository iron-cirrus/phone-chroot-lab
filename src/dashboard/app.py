#!/usr/bin/env python3
"""Tableau de bord CPU / GPU / NPU du telephone (chroot Debian).

Page: http://<ip>:8090/   JSON: http://<ip>:8090/api/stats
Lecture seule. Echantillonne en arriere-plan, la page se rafraichit toutes les 2 s.
"""
import glob
import re
import os
import json
import select
import subprocess
import threading
import time
import urllib.request
from collections import deque

from flask import Flask, Response, jsonify, request

PORT = 8090
SMALL = [0, 1, 2]          # Cortex-A510 (LowPerf)
MID = [3, 4, 5, 6]       # Cortex-A715 (MedPerf)
BIG = [7]                 # Cortex-X3 (HiPerf)
WINDOW = 2.0               # secondes de moyenne
SAMPLE = 0.5

app = Flask(__name__)
lock = threading.Lock()
cpu_hist = deque()         # (t, {cpu: (busy, total)})
gpu_hist = deque()
npu_hist = deque()
state = {"npu_ok": False}


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def read_int(path, default=None):
    v = read(path)
    try:
        return int(v.split()[0])
    except Exception:
        return default


def cpu_stat():
    out = {}
    for line in read("/proc/stat", "").splitlines():
        p = line.split()
        if p and p[0].startswith("cpu") and p[0] != "cpu":
            v = list(map(int, p[1:9]))
            # user nice system idle iowait irq softirq steal
            # "steal" = temps ou le noyau met le coeur de cote (core_ctl : le X3 est endormi quand il n'est pas utile, jusqu'a
            # 40 % du temps). Ce n'est pas du travail : on l'exclut du travail ET du total, l'usage est donc mesure sur le temps
            # ou le coeur etait disponible.
            busy = v[0] + v[1] + v[2] + v[5] + v[6]
            total = sum(v) - v[7]
            out[int(p[0][3:])] = (busy, total)
    return out


def zone_temps():
    temps = {}
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        t = read(z + "/type")
        v = read_int(z + "/temp")
        if t and v is not None and v > -100000:
            temps[t] = v / 1000.0
    return temps


def avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def npu_busy():
    """1 si llama-server (NPU Hexagon) traite une requete, 0 sinon, None si injoignable."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/slots", timeout=0.4) as r:
            slots = json.load(r)
        return 1.0 if any(s.get("is_processing") for s in slots) else 0.0
    except Exception:
        return None


# --- Telemetrie : consommation estimee, reseau, disque -----------------------------------------
# Modele d'energie du noyau (Qualcomm, /sys/kernel/debug/energy_model) : kHz -> mW par coeur a 100 % de charge.
# Puissance dynamique seulement (les fuites/statique et le reste du SoC ne sont pas comptes).
EM = {
    0: [(307200, 9), (441600, 14), (556800, 18), (672000, 23), (787200, 29), (902400, 33), (1017600, 41),
        (1113600, 46), (1228800, 55), (1344000, 64), (1459200, 74), (1555200, 83), (1670400, 93),
        (1785600, 104), (1900800, 114), (2016000, 125)],
    3: [(499200, 41), (614400, 55), (729600, 69), (844800, 86), (940800, 101), (1056000, 121), (1171200, 137),
        (1286400, 160), (1401600, 183), (1536000, 212), (1651200, 244), (1785600, 281), (1920000, 321),
        (2054400, 365), (2188800, 412), (2323200, 468), (2457600, 523), (2592000, 582), (2707200, 640),
        (2803200, 691)],
    7: [(595200, 105), (729600, 134), (864000, 166), (998400, 202), (1132800, 239), (1248000, 278),
        (1363200, 316), (1478400, 355), (1593600, 403), (1708800, 453), (1843200, 524), (1977600, 595),
        (2092800, 666), (2227200, 764), (2342400, 855), (2476800, 969), (2592000, 1075), (2726400, 1196),
        (2841600, 1317), (2956800, 1458), (3187200, 1712)],
}
POLICY = {c: (0 if c in SMALL else 3 if c in MID else 7) for c in SMALL + MID + BIG}
TELE_PERIOD = 2.0
POWER_WINDOW = 60.0
tele = {"power": None, "net": None, "disk": None, "chroot_cpu": None}
power_hist = deque()           # (t, chroot_W, cpu_W)
energy_hist = deque()          # (t, chroot_J, cpu_J) sur la derniere heure
energy_total = {"chroot": 0.0, "cpu": 0.0, "since": time.time()}
# consommation max theorique du CPU : chaque coeur a 100 % a sa derniere frequence (3 x A510, 4 x A715/A710, 1 x X3)
CPU_MAX_W = sum(EM[POLICY[c]][-1][1] for c in POLICY) / 1000.0


def em_power(cpu, khz):
    """mW d'un coeur a 100 % a la frequence donnee (interpolation lineaire dans la table)."""
    table = EM[POLICY[cpu]]
    if not khz:
        return 0.0
    if khz <= table[0][0]:
        return float(table[0][1])
    for (f0, p0), (f1, p1) in zip(table, table[1:]):
        if khz <= f1:
            return p0 + (p1 - p0) * (khz - f0) / (f1 - f0)
    return float(table[-1][1])


def proc_stat_fields(pid):
    s = read(f"/proc/{pid}/stat")
    if not s:
        return None
    f = s[s.rfind(")") + 2:].split()      # champs a partir de 'state' (champ 3)
    try:
        return int(f[11]) + int(f[12]), int(f[36])   # utime+stime (14,15), processor (39)
    except Exception:
        return None


def proc_io(pid):
    r = w = 0
    for line in read(f"/proc/{pid}/io", "").splitlines():
        if line.startswith("read_bytes:"):
            r = int(line.split()[1])
        elif line.startswith("write_bytes:"):
            w = int(line.split()[1])
    return r, w


def chroot_pids():
    """Processus du chroot : leur racine est la meme que la notre."""
    import os
    me = os.stat("/")
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                st = os.stat(f"/proc/{d}/root")
            except Exception:
                continue
            if st.st_ino == me.st_ino and st.st_dev == me.st_dev:
                out.append(int(d))
    return out


def net_dev():
    out = {}
    for line in read("/proc/net/dev", "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        v = rest.split()
        if len(v) >= 9:
            out[name.strip()] = (int(v[0]), int(v[8]))
    return out


def net_group(name):
    if name.startswith("wlan"):
        return "wifi"
    if name.startswith("wg"):
        return "wg"
    if name.startswith("rmnet_data") or name.startswith("ccmni"):
        return "mobile"
    return None


# --- encodeur/decodeur video materiel du SoC (Qualcomm msm_vidc, distinct du GPU) ---------------------
VIDC = "/sys/kernel/debug/msm_vidc/core"
VIDC_CLK = "/sys/kernel/debug/clk/video_cc_mvs0_clk"
VIDC_CAP = 7680 * 4320 * 30      # capacite annoncee du Snapdragon 8 Gen 2 : 8K a 30 i/s (~1 Gpixel/s)


def vidc_sessions():
    """Sessions actives : type, taille, compteur d'images traitees (ETB)."""
    out = {}
    for f in glob.glob(VIDC + "/inst_*/info"):
        txt = read(f, "")
        s = {"type": "Encoder" if "(Encoder)" in txt else "Decoder" if "(Decoder)" in txt else "?"}
        for line in txt.splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k in ("height", "width", "fps") and v.isdigit():
                s[k] = int(v)
            elif k == "ETB Count" and v.isdigit():
                s["etb"] = int(v)
        out[f] = s
    return out


_ai_cache = {"t": 0.0, "v": []}
mem_state = {"t": 0.0, "chroot": 0, "ai": 0}


def ai_pids():
    """Processus llama-server (IA) : scan de /proc mis en cache 4 s."""
    now = time.time()
    if now - _ai_cache["t"] < 4:
        return _ai_cache["v"]
    pids = [int(d) for d in os.listdir("/proc") if d.isdigit() and read(f"/proc/{d}/comm", "").startswith("llama-server")]
    _ai_cache.update(t=now, v=pids)
    return pids


# --- puissance TOTALE mesuree (pas estimee) : chargeur ou batterie ---------------------------------------------------------
# Branche : puissance tiree du chargeur (tension x courant USB) moins la charge de la batterie quand elle charge ; sur batterie :
# tension x courant de decharge. Fiable batterie pleine ou sur batterie ; approximative pendant une charge (difference de deux mesures,
# pertes du chargeur comprises).
PS = "/sys/class/power_supply/"
BATT_CELLS = 2
tot_hist = deque(maxlen=3)


def total_power():
    st = read(PS + "battery/status", "")
    vb, ib = read_int(PS + "battery/voltage_now"), read_int(PS + "battery/current_now")
    vu, iu = read_int(PS + "usb/voltage_now"), read_int(PS + "usb/current_now")
    plugged = read(PS + "usb/online") == "1" or read(PS + "wireless/online") == "1"
    if vb is None or ib is None:
        return None, None
    # current_now est en mA (pas en uA) et correspond a UNE des deux cellules en parallele du bloc de 5000 mAh : x2 pour le bloc
    # (verifie : le compteur de charge baisse deux fois plus vite que ce courant ne le laisse croire)
    p_batt = vb * abs(ib) * BATT_CELLS / 1e9
    if plugged and vu and iu is not None:
        p_in = vu * abs(iu) / 1e12
        return (max(p_in - p_batt, 0.0) if st == "Charging" else p_in), ("chargeur~" if st == "Charging" else "chargeur")
    if st in ("Discharging", "Not charging"):
        return p_batt, "batterie"
    return None, None


def total_power_avg():
    w, src = total_power()
    if w is None:
        return None, None
    tot_hist.append(w)
    return sum(tot_hist) / len(tot_hist), src


# --- part du chroot dans le reseau et le disque ------------------------------------------------------------------------
# Reseau : le chroot partage la pile reseau d'Android ; on lit donc les compteurs eBPF par UID de netd (map_netd_app_uid_stats_map,
# cumulatifs) pour les UID des processus du chroot (root, web...). Approximation : le trafic de bouclage local est inclus et les
# processus root d'Android comptent aussi, ce qui reste faible devant le trafic Wi-Fi.
# Disque : total = secteurs du disque UFS (/proc/diskstats) ; part du chroot = read_bytes / write_bytes de ses processus.
import ctypes
import struct
_libc = ctypes.CDLL(None, use_errno=True)
_BPF_MAP = "/proc/1/root/sys/fs/bpf/netd_shared/map_netd_app_uid_stats_map"
_bpf_state = {"fd": None, "uids": set(), "t": 0.0}


def _bpf(cmd, attr):
    buf = ctypes.create_string_buffer(attr, len(attr))
    return _libc.syscall(280, cmd, buf, len(attr))      # 280 = appel systeme bpf sur arm64


def uid_bytes(uids):
    """(octets recus, octets emis) cumules par les UID donnes, tous interfaces confondues ; None si indisponible."""
    try:
        if _bpf_state["fd"] is None:
            p = ctypes.create_string_buffer(_BPF_MAP.encode())
            _bpf_state["fd"] = _bpf(7, struct.pack("=QII", ctypes.addressof(p), 0, 0) + b"\0" * 100)     # BPF_OBJ_GET
        fd = _bpf_state["fd"]
        if fd < 0:
            _bpf_state["fd"] = None
            return None
        rx = tx = 0
        for uid in uids:
            kb = ctypes.create_string_buffer(struct.pack("=I", uid))
            vb = ctypes.create_string_buffer(32)
            if _bpf(1, struct.pack("=IIQQQ", fd, 0, ctypes.addressof(kb), ctypes.addressof(vb), 0) + b"\0" * 100) == 0:    # LOOKUP
                _, rb, _, tb = struct.unpack("=QQQQ", vb.raw)
                rx += rb
                tx += tb
        return rx, tx
    except Exception:
        return None


def disk_sectors():
    """(octets lus, octets ecrits) cumules par les disques entiers sd* (secteurs de 512 octets)."""
    r = w = 0
    for line in read("/proc/diskstats", "").splitlines():
        p = line.split()
        if len(p) > 9 and re.fullmatch(r"sd[a-z]", p[2]):
            r += int(p[5]) * 512
            w += int(p[9]) * 512
    return r, w


# --- energie sortie de la batterie (compteur de charge) et ecran estime (modele d'Android) -----------------------------
# Batterie : on cumule les BAISSES du compteur de charge (charge_counter, en uAh) par paliers d'au moins 2 mAh pour ignorer son bruit ;
# la charge n'est donc jamais comptee. Branche et batterie pleine, le compteur ne bouge presque pas : la batterie ne fournit rien.
# Ecran : Android estime l'energie de l'ecran depuis la derniere charge ('dumpsys batterystats') ; on en tire une puissance moyenne
# quand l'ecran est allume (ne suit pas la luminosite instantanee). Relu toutes les 15 min dans un thread.
batt_state = {"ref": None, "wh": 0.0, "t0": time.time()}
batt_hist = deque()                   # (t, wh cumule) sur 10 min
_screen = {"w": None, "t": 0.0}
NOMINAL_V = 3.85                      # tension nominale utilisee pour convertir les mAh du modele d'Android


def batt_track(t):
    q, vb = read_int(PS + "battery/charge_counter"), read_int(PS + "battery/voltage_now")
    if q is None or not vb:
        return
    ref = batt_state["ref"]
    if ref is None or q > ref:
        batt_state["ref"] = q                                  # premiere mesure, ou la batterie a ete rechargee
    elif ref - q >= 2000:                                      # baisse d'au moins 2 mAh
        batt_state["wh"] += (ref - q) / 1e6 * vb / 1e6
        batt_state["ref"] = q
    batt_hist.append((t, batt_state["wh"]))
    while batt_hist and t - batt_hist[0][0] > 600:
        batt_hist.popleft()


def batt_info(t):
    w10 = None
    if len(batt_hist) > 1 and t - batt_hist[0][0] >= 20:
        w10 = (batt_hist[-1][1] - batt_hist[0][1]) / (batt_hist[-1][0] - batt_hist[0][0]) * 3600
    return {"batt_wh": batt_state["wh"], "batt_since_s": round(t - batt_state["t0"]), "batt_w10": w10}


def screen_now_w():
    """Puissance estimee de l'ecran : moyenne Android si l'ecran est allume, 0 sinon ; None si inconnue."""
    b = read_int("/sys/class/backlight/panel0-backlight/brightness")
    if _screen["w"] is None or b is None:
        return None
    return _screen["w"] if b > 0 else 0.0


def _hours(txt):
    m = {u: int(v) for v, u in re.findall(r"(\d+)([hms])(?![a-z])", txt)}
    return m.get("h", 0) + m.get("m", 0) / 60 + m.get("s", 0) / 3600


def _screen_loop():
    while True:
        try:
            out = subprocess.run(["nice", "-n", "19", "nsenter", "-t", "1", "-m", "--", "dumpsys", "batterystats"],
                                 capture_output=True, text=True, timeout=60).stdout
            m = re.search(r"^\s+screen: ([\d.]+) apps: [\d.]+ duration: ([^\n]+)$", out, re.M)
            if m:
                h = _hours(m.group(2))
                if h > 0.05:
                    _screen.update(w=float(m.group(1)) / h * NOMINAL_V / 1000, t=time.time())
        except Exception:
            pass
        time.sleep(900)


def telemetry():
    prev = None
    while True:
        try:
            t = time.time()
            cs = cpu_stat()
            freqs = {c: read_int(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq") for c in POLICY}
            procs = {}
            for pid in chroot_pids():
                st = proc_stat_fields(pid)
                if st:
                    procs[pid] = (st[0], st[1], proc_io(pid))
            # temps CPU par thread (un processus peut tourner sur plusieurs blocs a la fois)
            threads, vid_tids = {}, set()
            for pid in procs:
                try:
                    tids = os.listdir(f"/proc/{pid}/task")
                except Exception:
                    continue
                # encodeur video du flux Moonlight : Sunshine (x264 logiciel)
                video = read(f"/proc/{pid}/comm", "").startswith("sunshine-v4l2")
                for tid in tids:
                    st = proc_stat_fields(f"{pid}/task/{tid}")
                    if st:
                        threads[int(tid)] = st
                        if video:
                            vid_tids.add(int(tid))
            # processus IA (llama-server : Olala en CPU et partie CPU du serveur NPU), cote Android donc hors du chroot
            ai_tids = set()
            for pid in ai_pids():
                try:
                    tids = os.listdir(f"/proc/{pid}/task")
                except Exception:
                    continue
                for tid in tids:
                    st = proc_stat_fields(f"{pid}/task/{tid}")
                    if st:
                        threads[int(tid)] = st
                        ai_tids.add(int(tid))
            # memoire propre du chroot : PSS (les pages partagees sont reparties entre les processus qui les utilisent)
            now_m = time.time()
            if now_m - mem_state["t"] >= 10:       # smaps_rollup est cher (~160 ms sur Olala) : une lecture toutes les 10 s
                pss = 0
                for pid in procs:
                    for line in read(f"/proc/{pid}/smaps_rollup", "").splitlines():
                        if line.startswith("Pss:"):
                            pss += int(line.split()[1]) * 1024
                            break
                ai_mem = 0
                for pid in ai_pids():
                    for line in read(f"/proc/{pid}/smaps_rollup", "").splitlines():
                        if line.startswith("Pss_Anon:"):   # memoire privee (poids repackes, tampons), hors cache de fichiers
                            ai_mem += int(line.split()[1]) * 1024
                            break
                mem_state.update(t=now_m, chroot=pss, ai=ai_mem)
            with lock:
                tele["chroot_mem"] = mem_state["chroot"]
                tele["ai_mem"] = mem_state["ai"]
            vs = vidc_sessions()
            if prev is not None:
                dtv = t - prev["t"]
                sess, pix = [], 0.0
                for f, s in vs.items():
                    old = prev.get("vs", {}).get(f)
                    fps = (s.get("etb", 0) - old.get("etb", 0)) / dtv if old and dtv > 0 else None
                    if fps is not None and fps < 0:
                        fps = None
                    w, h = s.get("width", 0), s.get("height", 0)
                    if fps:
                        pix += w * h * fps
                    sess.append({"type": s["type"], "width": w, "height": h, "fps": fps})
                with lock:
                    tele["vidc"] = {
                        "sessions": sess,
                        "active": bool(sess),
                        "load": 100.0 * pix / VIDC_CAP,                       # charge estimee (pixels/s / capacite)
                        "clock_mhz": (read_int(VIDC_CLK + "/clk_rate") or 0) / 1e6,
                        "clock_on": (read_int(VIDC_CLK + "/clk_enable_count") or 0) > 0,
                    }
            nd = net_dev()
            # UID des processus du chroot (rafraichis toutes les 10 s) et compteurs cumules correspondants
            if time.time() - _bpf_state["t"] > 10:
                us = set()
                for pid in procs:
                    for line in read(f"/proc/{pid}/status", "").splitlines():
                        if line.startswith("Uid:"):
                            us.add(int(line.split()[1]))
                            break
                _bpf_state.update(uids=us, t=time.time())
            ub = uid_bytes(_bpf_state["uids"])
            ds = disk_sectors()
            if prev:
                dt = t - prev["t"]
                # energie par cluster et jiffies occupees par cluster
                e_cl, j_cl, t_cl, cpu_w = {}, {}, {}, 0.0
                for c in POLICY:
                    if c in cs and c in prev["cs"]:
                        busy = cs[c][0] - prev["cs"][c][0]
                        tot = cs[c][1] - prev["cs"][c][1]
                        frac = busy / tot if tot > 0 else 0.0
                        f = ((freqs.get(c) or 0) + (prev["freqs"].get(c) or 0)) / 2
                        w = frac * em_power(c, f) / 1000.0
                        cpu_w += w
                        p = POLICY[c]
                        e_cl[p] = e_cl.get(p, 0.0) + w * dt
                        j_cl[p] = j_cl.get(p, 0) + busy
                        t_cl[p] = t_cl.get(p, 0) + tot
                # part du chroot : jiffies de ses threads, rangees par bloc (coeur ou chaque thread a tourne)
                ch_j, vid_j, ai_j = {}, {}, {}
                for tid, (j, cpu) in threads.items():
                    old = prev["threads"].get(tid)
                    if old:
                        p = POLICY.get(cpu, 3)
                        dj = max(0, j - old[0])
                        if tid in ai_tids:
                            ai_j[p] = ai_j.get(p, 0) + dj
                            continue
                        ch_j[p] = ch_j.get(p, 0) + dj
                        if tid in vid_tids:
                            vid_j[p] = vid_j.get(p, 0) + dj
                ch_e = sum(ch_j[p] * e_cl[p] / j_cl[p] for p in ch_j if j_cl.get(p))
                ch_w = min(ch_e / dt, cpu_w)
                vid_e = sum(vid_j[p] * e_cl[p] / j_cl[p] for p in vid_j if j_cl.get(p))
                vid_w = min(vid_e / dt, ch_w)
                ai_e = sum(ai_j[p] * e_cl[p] / j_cl[p] for p in ai_j if j_cl.get(p))
                ai_w = min(ai_e / dt, max(cpu_w - ch_w, 0))
                # % de la capacite de chaque bloc utilise par le chroot et par l'encodeur video (meme base que l'utilisation du bloc)
                pct = lambda jj: {name: (100.0 * min(jj.get(p, 0), j_cl.get(p, 0)) / t_cl[p] if t_cl.get(p) else None)
                                  for name, p in (("small", 0), ("mid", 3), ("big", 7))}
                ch_pct, vid_pct, ai_pct = pct(ch_j), pct(vid_j), pct(ai_j)
                rd = wr = 0
                for pid, (j, cpu, (r, w_)) in procs.items():
                    old = prev["procs"].get(pid)
                    if old:
                        rd += max(0, r - old[2][0])
                        wr += max(0, w_ - old[2][1])
                net = {}
                for name, (rx, tx) in nd.items():
                    g = net_group(name)
                    if g and name in prev["nd"]:
                        a = net.setdefault(g, [0.0, 0.0])
                        a[0] += max(0, rx - prev["nd"][name][0]) / dt
                        a[1] += max(0, tx - prev["nd"][name][1]) / dt
                # part du chroot dans le trafic reseau (plafonnee au total des interfaces Wi-Fi + mobile)
                net_ch = None
                if ub is not None and prev.get("ub") is not None:
                    net_ch = [max(0, ub[0] - prev["ub"][0]) / dt, max(0, ub[1] - prev["ub"][1]) / dt]
                    tot_rx = sum(v[0] for g, v in net.items() if g != "wg")
                    tot_tx = sum(v[1] for g, v in net.items() if g != "wg")
                    net_ch = [min(net_ch[0], tot_rx), min(net_ch[1], tot_tx)]
                dsk_tot = [max(0, ds[0] - prev["ds"][0]) / dt, max(0, ds[1] - prev["ds"][1]) / dt]
                with lock:
                    tw, tsrc = total_power_avg()
                    batt_track(t)
                    power_hist.append((t, ch_w, cpu_w))
                    energy_hist.append((t, ch_w * dt, cpu_w * dt, vid_w * dt))
                    while energy_hist and t - energy_hist[0][0] > 3600:
                        energy_hist.popleft()
                    energy_total["chroot"] += ch_w * dt
                    energy_total["cpu"] += cpu_w * dt
                    energy_total["video"] = energy_total.get("video", 0.0) + vid_w * dt
                    while power_hist and t - power_hist[0][0] > POWER_WINDOW:
                        power_hist.popleft()
                    n = len(power_hist)
                    tele["power"] = {
                        "chroot_w": sum(h[1] for h in power_hist) / n,
                        "cpu_w": sum(h[2] for h in power_hist) / n,
                        "chroot_now": ch_w, "cpu_now": cpu_w,
                        "window_s": round(t - power_hist[0][0] + TELE_PERIOD),
                        "cpu_max_w": CPU_MAX_W,
                        # energie en Wh (1 Wh = 3600 J) : derniere heure glissante et depuis le demarrage
                        "chroot_wh_1h": sum(e[1] for e in energy_hist) / 3600.0,
                        "cpu_wh_1h": sum(e[2] for e in energy_hist) / 3600.0,
                        "span_1h_s": round(t - energy_hist[0][0] + TELE_PERIOD) if energy_hist else 0,
                        "chroot_wh_total": energy_total["chroot"] / 3600.0,
                        "cpu_wh_total": energy_total["cpu"] / 3600.0,
                        "since_s": round(t - energy_total["since"]),
                        # encodeur video Sunshine (inclus dans le chroot)
                        "video_now": vid_w,
                        "total_w": tw, "total_src": tsrc,
                        "screen_w": screen_now_w(), **batt_info(t),
                        "ai_now": ai_w,
                        "video_wh_1h": sum(e[3] for e in energy_hist) / 3600.0,
                        "video_wh_total": energy_total["video"] / 3600.0,
                    }
                    tele["net"] = net
                    tele["disk"] = {"read": max(dsk_tot[0], rd / dt), "write": max(dsk_tot[1], wr / dt), "chroot_read": rd / dt, "chroot_write": wr / dt, "procs": len(procs)}
                    tele["net_chroot"] = net_ch
                    tele["chroot_cpu"] = ch_pct
                    tele["video_cpu"] = vid_pct
                    tele["ai_cpu"] = ai_pct
            prev = {"t": t, "cs": cs, "freqs": freqs, "procs": procs, "threads": threads, "nd": nd, "vs": vs, "ub": ub, "ds": ds}
        except Exception as e:
            print("telemetrie:", e, flush=True)
        time.sleep(TELE_PERIOD)


def sampler():
    while True:
        now = time.time()
        gpu = read_int("/sys/class/kgsl/kgsl-3d0/gpu_busy_percentage")
        npu = npu_busy()
        with lock:
            cpu_hist.append((now, cpu_stat()))
            while len(cpu_hist) > 2 and now - cpu_hist[1][0] >= WINDOW:
                cpu_hist.popleft()
            if gpu is not None:
                gpu_hist.append((now, gpu))
            if npu is not None:
                npu_hist.append((now, npu))
            state["npu_ok"] = npu is not None
            for h in (gpu_hist, npu_hist):
                while h and now - h[0][0] > WINDOW:
                    h.popleft()
        time.sleep(SAMPLE)


def cpu_percent(cpus):
    with lock:
        if len(cpu_hist) < 2:
            return None
        a, b = cpu_hist[0][1], cpu_hist[-1][1]
    busy = sum(b[c][0] - a[c][0] for c in cpus if c in a and c in b)
    total = sum(b[c][1] - a[c][1] for c in cpus if c in a and c in b)
    return 100.0 * busy / total if total > 0 else None


def cluster(cpus, zones):
    cur, cap, hw = [], [], []
    for c in cpus:
        base = f"/sys/devices/system/cpu/cpu{c}/cpufreq/"
        cur.append(read_int(base + "scaling_cur_freq"))
        cap.append(read_int(base + "scaling_max_freq"))
        hw.append(read_int(base + "cpuinfo_max_freq"))
    cur_ok = [c for c in cur if c]
    return {
        "cpus": cpus,
        "usage": cpu_percent(cpus),
        "temp": avg([zone_t for zone_t in zones]),
        "freq_avg": avg(cur_ok) / 1000.0 if cur_ok else None,
        "freq_peak": max(cur_ok) / 1000.0 if cur_ok else None,
        "freq_cap": max([c for c in cap if c], default=0) / 1000.0,
        "freq_hw": max([c for c in hw if c], default=0) / 1000.0,
        "governor": read(f"/sys/devices/system/cpu/cpu{cpus[0]}/cpufreq/scaling_governor"),
        "per_core": [
            {"cpu": c, "freq": (f or 0) / 1000.0, "usage": cpu_percent([c])}
            for c, f in zip(cpus, cur)
        ],
    }


def meminfo():
    m = {}
    for line in read("/proc/meminfo", "").splitlines():
        p = line.split()
        if len(p) >= 2:
            m[p[0].rstrip(":")] = int(p[1]) * 1024  # octets
    return m


_npu_cache = {"t": 0.0, "v": None}


def npu_memory():
    """Memoire partagee avec le NPU : buffers dma-buf (heap 'system') tenus par llama-server."""
    now = time.time()
    if now - _npu_cache["t"] < 1.5:
        return _npu_cache["v"]
    total, found = 0, False
    for d in glob.glob("/proc/[0-9]*"):
        comm = read(d + "/comm", "")
        if not comm.startswith("llama-server"):
            continue
        found = True
        for fi in glob.glob(d + "/fdinfo/*"):
            txt = read(fi, "")
            if "exp_name" in txt:
                for line in txt.splitlines():
                    if line.startswith("size:"):
                        total += int(line.split()[1])
    _npu_cache.update(t=now, v=total if found else None)
    return _npu_cache["v"]


def mem_block(used, total, extra=None):
    d = {"used": used, "total": total,
         "pct": 100.0 * used / total if used is not None and total else None}
    if extra:
        d.update(extra)
    return d


# --- gouverneur (affichage seulement) : reglage manuel de l'app Pilote, plafond inattendu -----------------
GOVF_PATH = "/proc/1/root/data/local/tmp/gov-manual"     # fichier cote Android, lu a travers /proc/1/root
PS_CAP3, ECO_CAP3 = 1286400, 1785600                     # plafonds Econome et eco du bloc Equilibre (cf. phone-ctl.sh)


def gov_info():
    man = {}
    for line in read(GOVF_PATH, "").splitlines():
        p = line.split()
        if len(p) == 2 and p[0].isdigit():
            man[int(p[0])] = p[1]
    st = read("/sys/class/power_supply/battery/status", "")
    on_power = (read("/sys/class/power_supply/usb/online") == "1" or read("/sys/class/power_supply/wireless/online") == "1"
                or st in ("Charging", "Full"))
    base = "/sys/devices/system/cpu/cpufreq/policy3/"
    cap, hw = read_int(base + "scaling_max_freq") or 0, read_int(base + "cpuinfo_max_freq") or 0
    if man.get(3) == "powersave":
        exp = PS_CAP3
    elif man.get(3) == "performance" or on_power:
        exp = hw
    else:
        exp = ECO_CAP3
    govs = [read(f"/sys/devices/system/cpu/cpufreq/policy{n}/scaling_governor") for n in (0, 3, 7)]
    return {"governor": govs[1] if len(set(govs)) == 1 else "mixte", "manual": bool(man),
            "clamp": cap < exp, "cap_mhz": cap / 1000.0, "on_power": on_power}


_svc_cache = {"t": 0.0, "v": {"vnc": False, "virgl": False, "sunshine": False}}


def svc_info():
    """Services affiches dans la page : Xvnc (bureau) et le serveur VirGL (GPU) ; scan de /proc mis en cache 4 s."""
    now = time.time()
    if now - _svc_cache["t"] < 4:
        return _svc_cache["v"]
    vnc = virgl = sun = qwen = olala = False
    for d in os.listdir("/proc"):
        if d.isdigit():
            c = read(f"/proc/{d}/comm", "")
            if c == "Xvnc":
                vnc = True
            elif c.startswith("sunshine-v4l2"):
                sun = True
            elif c.startswith("llama-server"):                       # Qwen (NPU) et Olala (CPU) : meme nom, on les distingue par leur modele
                cl = read(f"/proc/{d}/cmdline", "")
                qwen = qwen or "Qwen2.5-3B" in cl
                olala = olala or "Olala-v1.0" in cl
            elif c.startswith("virgl_test_ser"):
                virgl = True
    _svc_cache.update(t=now, v={"vnc": vnc, "virgl": virgl, "sunshine": sun, "llm": qwen, "olala": olala})
    return _svc_cache["v"]


def with_chroot(cl, name):
    """Ajoute au bloc CPU la part de sa capacite utilisee par les threads du chroot (en %)."""
    cl["chroot_usage"] = (tele.get("chroot_cpu") or {}).get(name)
    cl["video_usage"] = (tele.get("video_cpu") or {}).get(name)
    cl["ai_usage"] = (tele.get("ai_cpu") or {}).get(name)
    return cl


def build_stats():
    t = zone_temps()
    small_t = [t.get(f"cpu-1-{i}") for i in (8, 9, 10)]
    mid_t = [t.get(f"cpu-0-{i}") for i in range(3)] + [t.get(f"cpu-1-{i}") for i in range(2, 8)]
    big_t = [t.get(f"cpu-1-{i}") for i in (0, 1)]
    gpu_t = avg([t.get(f"gpuss-{i}") for i in range(8)])
    nsp_t = avg([t.get(f"nspss-{i}") for i in range(4)])
    with lock:
        gpu_u = avg([v for _, v in gpu_hist])
        npu_u = avg([v for _, v in npu_hist])
        npu_ok = state["npu_ok"]
    k = "/sys/class/kgsl/kgsl-3d0/"
    gpu_clk = read_int(k + "gpuclk")
    gpu_max = read_int(k + "max_gpuclk")
    mi = meminfo()
    ram_total = mi.get("MemTotal", 0)
    ram_used = ram_total - mi.get("MemAvailable", 0)
    swap_total = mi.get("SwapTotal", 0)
    swap_used = swap_total - mi.get("SwapFree", 0)
    gpu_mem = mi.get("GPUTotalUsed")
    gpu_peak = read_int("/sys/class/kgsl/kgsl/page_alloc_max")
    npu_mem = npu_memory()
    return json.dumps({
        "time": time.strftime("%H:%M:%S"),
        "ram": mem_block(ram_used, ram_total, {
            "cached": mi.get("Cached"),
            "chroot": tele.get("chroot_mem"),
            "ai": tele.get("ai_mem"),
            "swap": mem_block(swap_used, swap_total)}),
        "vram": mem_block(gpu_mem, ram_total, {"peak": gpu_peak}),
        "nram": mem_block(npu_mem, ram_total),
        "small": with_chroot(cluster(SMALL, small_t), "small"),
        "mid": with_chroot(cluster(MID, mid_t), "mid"),
        "big": with_chroot(cluster(BIG, big_t), "big"),
        "gpu": {
            "usage": gpu_u,
            "temp": gpu_t,
            "freq": gpu_clk / 1e6 if gpu_clk else None,
            "freq_max": gpu_max / 1e6 if gpu_max else None,
        },
        "npu": {
            "usage": None if not npu_ok or npu_u is None else npu_u * 100.0,
            "temp": nsp_t,
            "note": "actif = llama-server traite une requete (le NPU n'expose pas de compteur de charge)",
        },
        "skin": t.get("skin-msm-therm"),
        "power": tele["power"],
        "net": tele["net"],
        "disk": tele["disk"],
        "net_chroot": tele.get("net_chroot"),
        "vidc": dict(tele.get("vidc") or {}, temp=t.get("video")),
        "gov": gov_info(),
        "svc": svc_info(),
    })


# Les mesures sont calculees au plus une fois toutes les 1,5 s, par un seul thread a la fois : les autres requetes recoivent la
# derniere reponse. Sans cela, quand le telephone est charge (Olala + plafonds Econome) les requetes s'empilent, chacune dans
# son propre thread, et le tableau de bord finit par occuper plus de 2 coeurs.
_stats_cache = {"t": 0.0, "body": ""}
_stats_lock = threading.Lock()


@app.route("/api/stats")
def stats():
    fresh = time.time() - _stats_cache["t"] < 1.5
    if not fresh:
        got = _stats_lock.acquire(blocking=not _stats_cache["body"])    # la toute premiere fois, on attend le calcul
        if got:
            try:
                _stats_cache["body"] = build_stats()
                _stats_cache["t"] = time.time()
            finally:
                _stats_lock.release()
    return Response(_stats_cache["body"], mimetype="application/json")


# --- apercu du bureau capture par Sunshine : MJPEG 480 px a 3 i/s (pas le flux encode lui-meme) ----------------
# Un seul ffmpeg (x11grab -> MJPEG) tourne tant que l'app Pilote demande l'image, et s'arrete apres 8 s sans requete : une capture
# continue a 3 i/s coute bien moins qu'un instantane xwd+convert par image (~0,45 s de CPU chacun).
# Reserve aux requetes locales : un apercu du bureau ne doit pas etre lisible depuis le reseau.
PREVIEW_FPS = 3
_pv = {"body": b"", "t": 0.0, "last": 0.0, "proc": None}


def _x_env():
    return dict(os.environ, DISPLAY=":1", XAUTHORITY="/root/.Xauthority")


def _display_size():
    try:
        out = subprocess.run("xdpyinfo | awk '/dimensions:/{print $2}'", shell=True, capture_output=True,
                             text=True, timeout=4, env=_x_env()).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return "2560x1440"


def _pv_loop():
    buf, started = b"", 0.0
    while True:
        p = _pv["proc"]
        if time.time() - _pv["last"] > 8:                         # personne ne regarde : on coupe la capture
            if p is not None:
                p.kill()
                p.wait()
                _pv["proc"], buf = None, b""
            time.sleep(0.4)
            continue
        if p is None or p.poll() is not None:
            if p is not None and time.time() - started < 3:       # ffmpeg meurt tout de suite (pas de bureau X) : on temporise
                time.sleep(3)
            started, buf = time.time(), b""
            p = subprocess.Popen(
                ["ffmpeg", "-loglevel", "quiet", "-f", "x11grab", "-framerate", str(PREVIEW_FPS), "-video_size", _display_size(),
                 "-i", ":1", "-vf", "scale=480:-2", "-q:v", "8", "-f", "image2pipe", "-c:v", "mjpeg", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_x_env())
            _pv["proc"] = p
        if not select.select([p.stdout], [], [], 0.5)[0]:
            continue
        chunk = os.read(p.stdout.fileno(), 65536)
        if not chunk:
            time.sleep(0.3)
            continue
        buf += chunk
        while True:                                               # decoupe des images : chaque JPEG finit par FF D9
            end = buf.find(b"\xff\xd9")
            if end < 0:
                break
            frame, buf = buf[:end + 2], buf[end + 2:]
            if frame.startswith(b"\xff\xd8"):
                _pv["body"], _pv["t"] = frame, time.time()


@app.route("/preview.jpg")
def preview():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return Response("apercu reserve a l'app Pilote", status=403)
    _pv["last"] = time.time()
    for _ in range(40):                                           # au demarrage, on attend la premiere image (~1 s)
        if _pv["body"] and time.time() - _pv["t"] < 3:
            break
        time.sleep(0.1)
    if not _pv["body"] or time.time() - _pv["t"] >= 3:
        return Response("pas d'image", status=503)
    return Response(_pv["body"], mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telephone - CPU / GPU / NPU</title>
<style>
:root{--bg:#0e1116;--card:#171c24;--line:#262d38;--txt:#e6edf3;--mut:#8b96a5;
--ok:#3fb950;--mid:#d29922;--hot:#f85149;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.4 system-ui,Segoe UI,Roboto,sans-serif;padding:16px}
h1{font-size:18px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;transition:background-color .8s,border-color .8s}
.card h2{font-size:14px;margin:0 0 2px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.big{font-size:40px;font-weight:700;font-variant-numeric:tabular-nums;margin:6px 0}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-bottom:12px}
.bar>i{display:block;height:100%;width:0;background:var(--ok);transition:width 1.8s cubic-bezier(.25,.8,.25,1),background 1.8s}
dl{display:grid;grid-template-columns:1fr auto;gap:5px 12px;margin:0}
dt{color:var(--mut)}dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.cores{margin-top:12px;border-top:1px solid var(--line);padding-top:8px;font-size:13px;color:var(--mut)}
.cores div{display:flex;justify-content:space-between}
.note{margin-top:10px;font-size:12px;color:var(--mut)}
/* fond de la carte selon le gouverneur */
.gov-performance{background:#2b1517;border-color:#7a2f35}
.gov-performance .gov{color:#ff7b72}
.gov-uag,.gov-schedutil,.gov-walt{background:#10261a;border-color:#2a6b43}
.gov-uag .gov,.gov-schedutil .gov,.gov-walt .gov{color:#56d364}
.gov-powersave,.gov-conservative{background:#10223d;border-color:#2b5a9a}
.gov-powersave .gov,.gov-conservative .gov{color:#79c0ff}
.gov{font-weight:700}
.legend{font-size:12px;color:var(--mut);margin:-6px 0 14px}
.legend b{padding:1px 7px;border-radius:9px;margin-right:6px;color:var(--txt)}
.lg-p{background:#7a2f35}.lg-a{background:#2a6b43}.lg-e{background:#2b5a9a}
.nav{margin:0 0 10px;font-size:13px}.nav a{color:var(--acc);text-decoration:none;margin-right:12px}
#err{color:var(--hot);margin-top:12px;display:none}
</style></head><body>
<h1>Telephone : CPU, GPU, NPU</h1>
<div class="nav"><a href="/">Detail</a><a href="/resume">Synthese</a></div>
<div class="sub">Rafraichi toutes les 2 s - <span id="t">--</span> - peau <span id="skin">--</span></div>
<div class="legend">Fond des cartes CPU, gouverneur : <b class="lg-p">performance</b><b class="lg-a">adaptatif (uag, schedutil)</b><b class="lg-e">economie (powersave)</b></div>
<div class="grid" id="g"></div>
<div id="err">Connexion perdue, nouvel essai...</div>
<script>
const f=(v,d=0,u='')=>v==null?'--':v.toFixed(d)+u;
// --- rendu fluide : on met a jour le DOM en place (au lieu de le recreer) pour que les transitions CSS s'animent
const nativeSet=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML').set;
const nativeGet=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML').get;
function patch(a,b){
  const an=a.childNodes,bn=b.childNodes;
  for(let i=0;i<bn.length;i++){
    const x=an[i],y=bn[i];
    if(!x){a.appendChild(y.cloneNode(true));continue;}
    if(x.nodeType!==y.nodeType||x.nodeName!==y.nodeName){a.replaceChild(y.cloneNode(true),x);continue;}
    if(x.nodeType===3){if(x.nodeValue!==y.nodeValue)x.nodeValue=y.nodeValue;continue;}
    for(const at of Array.from(x.attributes)) if(!y.hasAttribute(at.name)) x.removeAttribute(at.name);
    for(const at of Array.from(y.attributes)) if(x.getAttribute(at.name)!==at.value) x.setAttribute(at.name,at.value);
    patch(x,y);
  }
  while(an.length>bn.length) a.removeChild(a.lastChild);
}
function put(el,html){
  if(!el._init){el._init=1;nativeSet.call(el,html.replace(/width:[0-9.]+%/g,'width:0%'));void el.offsetWidth;}
  const t=document.createElement('template');nativeSet.call(t,html);patch(el,t.content);
}
['g'].forEach(id=>{const el=document.getElementById(id);Object.defineProperty(el,'innerHTML',{set(h){put(el,h);},get(){return nativeGet.call(el);}});});
const col=p=>p>=85?'var(--hot)':p>=60?'var(--mid)':'var(--ok)';
function card(title,u,rows,extra='',cls=''){
  const p=u==null?0:u;
  return `<div class="card ${cls}"><h2>${title}</h2>
  <div class="big">${f(u,0,' %')}</div>
  <div class="bar"><i style="width:${p}%;background:${col(p)}"></i></div>
  <dl>${rows.map(r=>`<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join('')}</dl>${extra}</div>`;
}
function cpuCard(title,c){
  const cores=c.per_core.map(x=>`<div><span>cpu${x.cpu}</span><span>${f(x.usage,0,' %')} - ${f(x.freq,0,' MHz')}</span></div>`).join('');
  return card(title,c.usage,[
    ['Temperature moyenne',f(c.temp,1,' \\u00b0C')],
    ['Frequence moyenne',f(c.freq_avg,0,' MHz')],
    ['Frequence max atteinte',f(c.freq_peak,0,' MHz')],
    ['Plafond autorise',f(c.freq_cap,0,' MHz')],
    ['Maximum materiel',f(c.freq_hw,0,' MHz')],
    ['Gouverneur',`<span class="gov">${c.governor||'--'}</span>`]
  ],`<div class="cores">${cores}</div>`,'gov-'+(c.governor||'none'));
}
const gb=b=>b==null?'--':(b>=1073741824?(b/1073741824).toFixed(2)+' Go':(b/1048576).toFixed(0)+' Mo');
function memCard(title,m,rows,note=''){
  const p=m.pct==null?0:m.pct;
  return `<div class="card"><h2>${title}</h2>
  <div class="big">${f(m.pct,1,' %')}</div>
  <div class="bar"><i style="width:${Math.min(p,100)}%;background:${col(p)}"></i></div>
  <dl><dt>Occupee</dt><dd>${gb(m.used)}</dd><dt>Taille</dt><dd>${gb(m.total)}</dd>${rows.map(r=>`<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join('')}</dl>
  ${note?`<div class="note">${note}</div>`:''}</div>`;
}
async function tick(){
  try{
    const d=await (await fetch('/api/stats',{cache:'no-store'})).json();
    document.getElementById('t').textContent=d.time;
    document.getElementById('skin').textContent=f(d.skin,1,' \\u00b0C');
    document.getElementById('g').innerHTML=
      cpuCard('Coeurs Eco - petits (cpu0-2)',d.small)+
      cpuCard('Coeurs Equilibre - moyens (cpu3-6)',d.mid)+
      cpuCard('Coeur Turbo - gros (cpu7)',d.big)+
      card('GPU Adreno',d.gpu.usage,[
        ['Temperature moyenne',f(d.gpu.temp,1,' \\u00b0C')],
        ['Frequence',f(d.gpu.freq,0,' MHz')],
        ['Frequence max',f(d.gpu.freq_max,0,' MHz')]])+
      card('NPU Hexagon',d.npu.usage,[
        ['Temperature moyenne',f(d.npu.temp,1,' \\u00b0C')],
        ['Etat',d.npu.usage==null?'serveur NPU arrete':(d.npu.usage>0?'en calcul':'au repos')]],
        `<div class="note">${d.npu.note}</div>`)+
      memCard('Memoire RAM',d.ram,[
        ['Cache',gb(d.ram.cached)],
        ['Swap occupe',`${gb(d.ram.swap.used)} / ${gb(d.ram.swap.total)} (${f(d.ram.swap.pct,0,' %')})`]])+
      memCard('VRAM GPU (memoire partagee)',d.vram,[
        ['Pic depuis le demarrage',gb(d.vram.peak)]],
        'Pas de VRAM dediee : le GPU Adreno utilise la RAM. Taille = RAM totale.')+
      memCard('RAM du NPU (memoire partagee)',d.nram,[
        ['Etat',d.nram.used==null?'serveur NPU arrete':'modele charge']],
        'Pas de RAM dediee : buffers dma-buf du modele (poids + contexte) tenus par llama-server. Taille = RAM totale.');
    document.getElementById('err').style.display='none';
  }catch(e){document.getElementById('err').style.display='block';}
}
tick();setInterval(tick,2000);
</script></body></html>"""


SUMMARY = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Container Debian 13</title>
<style>
:root{--bg:#0e1116;--card:#171c24;--line:#262d38;--txt:#e6edf3;--mut:#8b96a5;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:13px/1.3 system-ui,Segoe UI,Roboto,sans-serif;padding:16px}
.wrap{max-width:400px;margin:0 auto}
.nav{margin:0 0 10px;font-size:13px}.nav a{color:var(--acc);text-decoration:none;margin-right:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.hd{display:flex;justify-content:space-between;align-items:center;font-size:14px;color:#e6edf3;margin-bottom:4px;padding:3px 5px;min-height:28px}
.hd b{display:flex;align-items:center;gap:8px}.hd .ic{width:24px;height:24px;margin:0}
.hd b{text-transform:uppercase;letter-spacing:.04em}
.blk{border:1px solid var(--line);border-radius:9px;padding:6px 8px;margin-bottom:6px}
.blk>h2,.encl>h2{display:flex;align-items:center;gap:7px;min-height:20px;margin:0 0 3px;font-size:13px;font-weight:700;color:#e6edf3;text-transform:uppercase;letter-spacing:.05em}
.blk>h2 .ic,.encl>h2 .ic{order:2;margin-left:auto;opacity:.9}
.ic{width:17px;height:17px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.one{margin-top:4px}
.cols>.blk{margin-bottom:0;padding:6px 8px}
.cols .dt{white-space:normal}
.nm{font-weight:600;display:flex;align-items:center;gap:6px}
.bar{height:9px;background:var(--line);border-radius:5px;overflow:hidden;display:flex;margin:3px 0 2px}
.bar>i{display:block;flex:none;height:100%;width:0;transition:width .45s ease-out,background-color .45s}
.bar>i:last-child{border-radius:0 5px 5px 0}
.dt{font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums;}
.gv{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--line)}
.gv-performance{background:#ff7b72}.gv-uag,.gv-schedutil,.gv-walt{background:#56d364}.gv-powersave,.gv-conservative{background:#79c0ff}
.lg{margin-top:4px;font-size:11px;color:var(--mut)}
.lg .k{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 6px;vertical-align:-1px}
.lg .k:first-child{margin-left:0}
.mbar{position:relative;height:9px;background:var(--line);border-radius:5px;overflow:hidden;margin:3px 0 2px}
.mbar i{position:absolute;top:0;bottom:0;transition:width .45s ease-out,left .45s ease-out}
.mbar u{position:absolute;top:0;bottom:0;width:2px;background:var(--txt);opacity:.55}
.mtx{display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 10px;font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums}
.mtx .l{display:flex;flex-wrap:wrap;gap:2px 10px}
.mtx s{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;text-decoration:none}
.br{display:flex;align-items:center;gap:6px}.br .bar{flex:1}.br small{font-size:9px;color:var(--mut);min-width:46px;text-align:right;font-variant-numeric:tabular-nums}
.t-pow{background:#2a2214;border-color:#4d3d1e}.t-proc{background:#15233a;border-color:#27416a}.t-mem{background:#241a38;border-color:#433066}.t-net{background:#0f2c29;border-color:#1f5550}
.t-ai{background:#2c1a30;border-color:#52305a}#ai{display:flex;gap:10px}.air{flex:1;display:flex;align-items:center;gap:8px;padding:2px 0;font-size:12px;min-width:0}.air>div{display:flex;flex-direction:column;line-height:1.2}.air em{font-style:normal;font-size:10px;color:var(--mut)}
.gch{display:flex;gap:3px;margin:4px 0 3px}.gch span{flex:1;text-align:center;font-size:9px;padding:2px 0;border-radius:4px;background:var(--line);color:var(--mut)}.gch span.on{background:#58a6ff;color:#0e1116;font-weight:700}
.gch span.clk{cursor:pointer;padding:5px 0;font-size:10px}.gbo{margin-left:auto;font-weight:400;background:var(--line);border-radius:9px;padding:1px 7px;cursor:pointer}
.hdr{display:flex;align-items:center;gap:8px}.hbtn{background:var(--line);border-radius:9px;padding:0 7px;font-size:13px;font-weight:400;cursor:pointer}
#hdc{display:flex;align-items:center;gap:6px}.hd .ic{transform:scaleY(-1)}
.gh2{display:flex;align-items:center}.gh2>span{margin-left:auto;display:flex;align-items:center;gap:6px}.dd{width:8px;height:8px;border-radius:50%;background:#56d364}.dd.off{background:#ff7b72}
.svr{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;margin-top:6px;padding-top:5px;border-top:1px solid rgba(255,255,255,.1);font-size:11px;color:var(--txt)}.svr label{display:flex;align-items:center;gap:6px}
.encw{display:flex;gap:8px;align-items:flex-start}.encl>h2{margin-bottom:3px}.encl{flex:1;min-width:0}#pv{flex:none;width:46%}#pv img{display:block;width:100%;height:auto;border-radius:6px;background:#000}
.lk{text-decoration:underline;text-decoration-color:rgba(255,255,255,.35);text-underline-offset:3px;cursor:pointer}
.sw.off{background:#4b5563}.sw.off i{right:auto;left:2px}
.sw{display:inline-block;position:relative;width:30px;height:16px;border-radius:8px;background:#56d364;cursor:pointer;vertical-align:middle}.sw i{position:absolute;top:50%;right:2px;width:12px;height:12px;margin-top:-6px;border-radius:50%;background:#fff}
.tb{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin:1px 0 3px}
.tb>i{display:block;height:100%;width:0;border-radius:3px;transition:width .45s ease-out,background-color .45s}
.bd{display:inline-block;margin-left:5px;padding:0 5px;border-radius:4px;font-size:9px;font-weight:700;letter-spacing:.04em;color:#0e1116;vertical-align:1px}
.bd-w{background:#f0b429}.bd-t{background:#ff7b72}
/* paysage (largeur >= 640 px) dans l app : deux colonnes equilibrees ; le titre occupe une colonne comme les autres blocs */
@media (min-width:640px){body.inapp .card{display:grid;grid-template-columns:1fr 1fr;gap:6px 8px}body.inapp .card>*{margin:0!important;min-width:0}body.inapp .card>.cols{display:contents}body.inapp .card>.cols>.blk{margin:0!important}body.inapp .card>.encw,body.inapp .card>.t-ai,body.inapp .card>.blk.t-proc{grid-column:1/-1}body.inapp .card>.hd{grid-column:1;grid-row:1;align-self:center}body.inapp #ram{grid-column:1;grid-row:2}body.inapp #pw{grid-column:2;grid-row:1/3}}
body.bare .nav{display:none}body.bare{padding:4px}body.bare .wrap{max-width:none}
</style></head><body><div class="wrap">
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-deb" viewBox="0 0 24 24"><path d="M13.5 3.2C8.6 2.6 4.3 6.4 4.6 11.3c.3 4.7 4.6 8.1 9 7.2 3.6-.7 5.2-4.4 3.2-6.9-1.6-2-4.7-1.5-5.1.8-.3 1.6 1.3 2.7 2.5 2" fill="none" stroke="#d70a53" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-pow" viewBox="0 0 24 24"><path d="M13 2 5 14h6l-1 8 8-12h-6z"/></symbol>
<symbol id="i-mem" viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="9" rx="1.5"/><path d="M6 16v3M10 16v3M14 16v3M18 16v3M6 10.5h3M11 10.5h2M15 10.5h3"/></symbol>
<symbol id="i-cpu" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/></symbol>
<symbol id="i-gpu" viewBox="0 0 24 24"><rect x="2" y="6" width="17" height="11" rx="1.5"/><circle cx="10" cy="11.5" r="3"/><path d="M19 8h3v7h-3M5 17v3M8 17v3"/></symbol>
<symbol id="i-npu" viewBox="0 0 24 24"><circle cx="12" cy="12" r="2.4"/><circle cx="5" cy="6" r="1.8"/><circle cx="5" cy="18" r="1.8"/><circle cx="19" cy="6" r="1.8"/><circle cx="19" cy="18" r="1.8"/><path d="M6.6 7 10 10.4M6.6 17 10 13.6M17.4 7 14 10.4M17.4 17 14 13.6"/></symbol>
<symbol id="i-enc" viewBox="0 0 24 24"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="m16 10 6-3.5v11L16 14z"/></symbol>
<symbol id="i-net" viewBox="0 0 24 24"><path d="M8 20V5M4.5 8.5 8 5l3.5 3.5M16 4v15M12.5 15.5 16 19l3.5-3.5"/></symbol>
<symbol id="i-dsk" viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/></symbol>
<symbol id="i-ai" viewBox="0 0 24 24"><g id="brh"><path d="M12 4.5C10.6 2.8 7.6 3.2 7.1 5.6C4.7 5.8 3.5 8.6 5 10.4C3.3 12.2 3.9 15.4 6.2 16C6.6 18.6 10 19.7 12 18.2L12 4.5M8.2 9.6C9.2 10.6 10.6 10.6 11.2 9.4M7.6 13.4C8.8 12.6 10.4 12.8 11.2 14"/></g><g transform="translate(24 0) scale(-1 1)"><path d="M12 4.5C10.6 2.8 7.6 3.2 7.1 5.6C4.7 5.8 3.5 8.6 5 10.4C3.3 12.2 3.9 15.4 6.2 16C6.6 18.6 10 19.7 12 18.2L12 4.5M8.2 9.6C9.2 10.6 10.6 10.6 11.2 9.4M7.6 13.4C8.8 12.6 10.4 12.8 11.2 14"/></g></symbol>
</defs></svg>
<div class="nav"><a href="/">Detail</a><a href="/resume">Synthese</a></div>
<div class="card">
<div class="hd"><b><svg class="ic"><use href="#i-deb"/></svg>Container Debian 13</b><span class="hdr"><span id="hdc"></span><span id="t">--</span></span></div>
<div class="blk t-pow" id="pw"></div>
<div class="blk t-mem" id="ram"></div>
<div class="blk t-proc"><h2><svg class="ic"><use href="#i-cpu"/></svg>CPU Kryo &middot; Snapdragon 8 Gen 2 (SM8550)</h2>
  <div class="cols"><div id="c0"></div><div id="c1"></div></div>
  <div class="cols one"><div id="c2"></div><div id="gv"></div></div>
  <div class="lg"><span class="k" style="background:#e3b341"></span>vid&eacute;o (Sunshine) <span class="k" style="background:#58a6ff"></span>chroot <span class="k" style="background:#db61a2"></span>IA <span class="k" style="background:#6e7681"></span>reste du syst&egrave;me</div>
</div>
<div class="cols">
  <div class="blk t-proc"><h2><svg class="ic"><use href="#i-gpu"/></svg>GPU Adreno 740</h2><div id="gpu"></div><div class="svr" id="vgl"></div></div>
  <div class="blk t-proc"><h2><svg class="ic"><use href="#i-npu"/></svg>NPU Hexagon v73</h2><div id="npu"></div></div>
</div>
<div class="blk t-proc encw" style="margin-top:6px">
  <div class="encl"><h2><svg class="ic"><use href="#i-enc"/></svg>Encodeur vid&eacute;o</h2><div id="vpu"></div><div class="svr" id="vnc"></div></div>
  <div id="pv" style="display:none"><img id="pvi" alt="" decoding="async"></div>
</div>
<div class="cols" style="margin-top:6px">
  <div class="blk t-net"><h2><svg class="ic"><use href="#i-net"/></svg>R&eacute;seau</h2><div id="net"></div></div>
  <div class="blk t-net"><h2><svg class="ic"><use href="#i-dsk"/></svg>Disque</h2><div id="dsk"></div></div>
</div>
<div class="blk t-ai" id="aib" style="margin-top:6px;display:none"><h2><svg class="ic"><use href="#i-ai"/></svg>IA locale</h2><div id="ai"></div></div>
</div></div>
<script>
if(window.Android)document.body.classList.add('inapp');
if(location.search.indexOf('bare')>=0)document.body.classList.add('bare');   // fenetre Chromium sans bandeau (fond d'ecran)   // l'app Pilote : mise en page en deux colonnes en paysage
const f=(v,d=0,u='')=>v==null?'--':v.toFixed(d)+u;
// --- rendu fluide : on met a jour le DOM en place (au lieu de le recreer) pour que les transitions CSS s'animent
const nativeSet=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML').set;
const nativeGet=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML').get;
function patch(a,b){
  const an=a.childNodes,bn=b.childNodes;
  for(let i=0;i<bn.length;i++){
    const x=an[i],y=bn[i];
    if(!x){a.appendChild(y.cloneNode(true));continue;}
    if(x.nodeType!==y.nodeType||x.nodeName!==y.nodeName){a.replaceChild(y.cloneNode(true),x);continue;}
    if(x.nodeType===3){if(x.nodeValue!==y.nodeValue)x.nodeValue=y.nodeValue;continue;}
    for(const at of Array.from(x.attributes)) if(!y.hasAttribute(at.name)) x.removeAttribute(at.name);
    for(const at of Array.from(y.attributes)) if(x.getAttribute(at.name)!==at.value) x.setAttribute(at.name,at.value);
    patch(x,y);
  }
  while(an.length>bn.length) a.removeChild(a.lastChild);
}
function put(el,html){
  if(!el._init){el._init=1;nativeSet.call(el,html.replace(/width:[0-9.]+%/g,'width:0%'));void el.offsetWidth;}
  const t=document.createElement('template');nativeSet.call(t,html);patch(el,t.content);
}
['c0','c1','c2','ram','gpu','npu','pw','net','dsk','vpu','gv'].forEach(id=>{const el=document.getElementById(id);Object.defineProperty(el,'innerHTML',{set(h){put(el,h);},get(){return nativeGet.call(el);}});});
// chroot : redemarrage et arret depuis le titre (dans l'app Pilote seulement ; tout depend de lui)
if(window.Android&&window.Android.chroot){
  const h=document.getElementById('hdc');
  h.innerHTML='<b class="hbtn" data-c="reboot" title="Red\u00e9marrer le chroot">\u21bb</b><span class="sw" data-c="stop" title="Chroot actif - toucher pour l arreter"><i></i></span>';
  h.addEventListener('click',e=>{const x=e.target.closest('[data-c]'); if(x) Android.chroot(x.dataset.c);});
}
// VNC (bloc Encodeur) et VirGL (bloc GPU) : commandes dans l'app Pilote ; dans un navigateur, simple affichage de l'etat
const SV_BRIDGE=!!(window.Android&&window.Android.svc);
if(SV_BRIDGE){
  for(const id of ['vnc','vgl']) document.getElementById(id).addEventListener('click',e=>{
    const o=e.target.closest('[data-o]'); if(o){Android.open(o.dataset.o); return;}   // libelle Sunshine : ouvre Moonlight
    const x=e.target.closest('[data-x]'); if(!x) return; const [n,a]=x.dataset.x.split(':'); Android.svc(n,a);});
}
const AI_BRIDGE=!!(window.Android&&window.Android.ai);
if(AI_BRIDGE){
  document.getElementById('aib').style.display='';
  document.getElementById('ai').addEventListener('click',e=>{
    if(e.target.closest('[data-w]')){Android.open('webui');return;}
    const x=e.target.closest('[data-a]'); if(!x) return; const [n,a]=x.dataset.a.split(':'); Android.ai(n,a);});
}
// apercu du bureau dans la carte Encodeur : seulement dans l'app Pilote (le serveur refuse les autres clients), seulement quand un flux est actif
let pvActive=false;
if(SV_BRIDGE){
  // ~3 images/s : la suivante est demandee 280 ms apres l'arrivee de la precedente (pas d'empilement si le telephone rame)
  const box=document.getElementById('pv'), img=document.getElementById('pvi');
  const step=()=>{
    if(!pvActive||document.visibilityState==='hidden'){box.style.display='none';return setTimeout(step,1000);}
    const n=new Image();
    n.onload=()=>{img.src=n.src;box.style.display='';setTimeout(step,280);};
    n.onerror=()=>{box.style.display='none';setTimeout(step,2000);};
    n.src='/preview.jpg?'+Date.now();
  };
  step();
}
const GV_BRIDGE=!!(window.Android&&window.Android.gov);
const GV_NAMES=['auto','powersave','uag','performance'];
let gvPending=null;
if(GV_BRIDGE){
  document.getElementById('gv').addEventListener('click',e=>{
    const b=e.target.closest('[data-b]'); if(b){Android.boost();return;}
    const s=e.target.closest('[data-g]'); if(!s) return;
    const i=+s.dataset.g; gvPending={pos:i,t:Date.now()};
    document.querySelectorAll('#gv .gch span').forEach((x,k)=>x.classList.toggle('on',k===i));
    Android.gov(GV_NAMES[i]);
  });
}
const gb1=b=>b==null?'--':(b/1073741824).toFixed(1);
const clamp=x=>Math.max(0,Math.min(1,x));
const loadCol=p=>p>=85?'hsl(0,80%,55%)':p>=60?'hsl(38,85%,50%)':'hsl(130,60%,45%)';
// une couleur par gouverneur
const govCol=g=>({performance:'#ff7b72',uag:'#56d364',schedutil:'#56d364',walt:'#56d364',powersave:'#79c0ff',conservative:'#79c0ff'}[g]||'#8b96a5');
function bar(len,color,title){return `<div class="bar" title="${title||''}"><i style="width:${(clamp(len/100)*100).toFixed(1)}%;background-color:${color}"></i></div>`;}
// longueur = performance absolue = utilisation x (frequence moyenne / frequence max autorisee)
// temperature : barre 20..100 C, couleur selon seuils (warning = chaud, throttle = limitation thermique probable)
const TH={cpu:[75,85],gpu:[70,85],npu:[65,80],vpu:[75,90]};
const tCol=(t,k)=>t==null?'#4b5563':t>=TH[k][1]?'#ff7b72':t>=TH[k][0]?'#f0b429':'#56d364';
function tempBar(t,k){const w=t==null?0:Math.max(0,Math.min(100,(t-20)/80*100));return `<div class="tb" title="seuils : warning ${TH[k][0]} \\u00b0C, throttle ${TH[k][1]} \\u00b0C"><i style="width:${w.toFixed(1)}%;background-color:${tCol(t,k)}"></i></div>`;}
function tempTxt(t,k){const b=t==null?'':t>=TH[k][1]?'<span class="bd bd-t">THROTTLE</span>':t>=TH[k][0]?'<span class="bd bd-w">WARNING</span>':'';return `${f(t,0,' \\u00b0C')}${b}`;}
// temperature + reste de la ligne ; en WARNING / THROTTLE le badge prend la place du reste, la hauteur de la carte ne change pas
const tr=(t,k,rest)=>t!=null&&t>=TH[k][0]?tempTxt(t,k):tempTxt(t,k)+' - '+rest;
function cpuCell(name,c){
  const ratio=c.freq_cap>0?c.freq_avg/c.freq_cap:0;
  const abs=(c.usage==null?0:c.usage)*ratio;
  // meme echelle (utilisation x freq / max) : encodeur video (jaune), reste du chroot (bleu), reste du systeme (gris)
  const ch=Math.min(abs,(c.chroot_usage==null?0:c.chroot_usage)*ratio);
  const vd=Math.min(ch,(c.video_usage==null?0:c.video_usage)*ratio);
  const tip=`utilisation ${f(c.usage,0,' %')} dont chroot ${f(c.chroot_usage,0,' %')} (encodeur video ${f(c.video_usage,0,' %')}) x ${f(c.freq_avg,0)} / ${f(c.freq_cap,0)} MHz = ${f(abs,0,' %')} de la puissance max autorisee - gouverneur ${c.governor}`;
  const L=x=>(clamp(x/100)*100).toFixed(1);
  // processus IA (llama-server : Olala, partie CPU du serveur NPU), hors chroot : segment rose
  const ia=Math.min(Math.max(abs-ch,0),(c.ai_usage==null?0:c.ai_usage)*ratio);
  const cbar=`<div class="bar" title="${tip} - IA ${f(c.ai_usage,0,' %')}"><i style="width:${L(vd)}%;background-color:#e3b341"></i><i style="width:${L(ch-vd)}%;background-color:#58a6ff"></i><i style="width:${L(ia)}%;background-color:#db61a2"></i><i style="width:${L(Math.max(abs-ch-ia,0))}%;background-color:#6e7681"></i></div>`;
  return `<div class="nm">${name}</div>${cbar}${tempBar(c.temp,'cpu')}
  <div class="dt">${tr(c.temp,'cpu',`${f(c.freq_avg,0)} MHz (max ${f(c.freq_cap,0)})`)}</div>`;
}
const rate=b=>b==null?'--':b>=1048576?(b/1048576).toFixed(1)+' Mo/s':b>=1024?(b/1024).toFixed(0)+' Ko/s':b.toFixed(0)+' o/s';
const w2=v=>v==null?'--':(v<1?v.toFixed(2):v.toFixed(1)).replace('.',',')+' W';
// echelle dynamique commune a deux barres : max des 60 derniers releves (2 min), arrondi a 1-2-5 x 10^n, 1 Ko/s minimum
const HIST={};
function dynScale(key,vals){
  const h=HIST[key]||(HIST[key]=[]); h.push(Math.max(...vals.map(v=>v||0))); if(h.length>60) h.shift();
  const m=Math.max(1024,...h), u=m>=1048576?1048576:1024, x=m/u, e=Math.pow(10,Math.floor(Math.log10(x)));
  return [1,2,5,10].map(k=>k*e).find(k=>k>=x)*u;   // en Ko/s ou Mo/s ronds
}
function sbar(v,scale,color,title){return `<div class="br">${bar(100*(v||0)/scale,color,title+' - echelle '+rate(scale))}<small>${rate(scale)}</small></div>`;}
function sbar2(v,c,scale,title){const L=x=>(clamp(x/scale)*100).toFixed(1); v=v||0; c=Math.min(c||0,v);
  return `<div class="br"><div class="bar" title="${title} - dont chroot ${rate(c)} - echelle ${rate(scale)}"><i style="width:${L(c)}%;background-color:#58a6ff"></i><i style="width:${L(v-c)}%;background-color:#6e7681"></i></div><small>${rate(scale)}</small></div>`;}
const logLen=b=>Math.min(100,100*Math.log10(1+(b||0)/1024)/Math.log10(1+102400));   // 0 .. 100 Mo/s
function cell(name,len,color,detail,tb){return `${name?`<div class="nm">${name}</div>`:''}${bar(len,color)}${tb||''}<div class="dt">${detail}</div>`;}
async function tick(){
  try{
    const d=await (await fetch('/api/stats',{cache:'no-store'})).json();
    const $=id=>document.getElementById(id);
    $('t').textContent=d.time;
    $('c0').innerHTML=cpuCell('Cortex-A510 ×3',d.small);
    $('c1').innerHTML=cpuCell('Cortex-A715/A710 ×4',d.mid);
    $('c2').innerHTML=cpuCell('Cortex-X3',d.big);
    pvActive=!!(d.vidc&&d.vidc.active);
    // lignes on/off en bas des blocs (interrupteurs dans l'app Pilote, simples voyants dans un navigateur)
    const sv=d.svc||{};
    const tg=(n,l,on)=>`<label>${SV_BRIDGE?`<span class="sw ${on?'':'off'}" data-x="${n}:${on?'stop':'start'}" title="${l} ${on?'actif - toucher pour l arreter':'arrete - toucher pour le demarrer'}"><i></i></span>`:`<i class="dd ${on?'':'off'}"></i>`}${SV_BRIDGE&&n==='sunshine'?`<span class="lk" data-o="moonlight" title="Ouvrir Moonlight">${l}</span>`:l}</label>`;
    $('vnc').innerHTML=tg('sunshine','Sunshine',sv.sunshine)+tg('vnc','VNC',sv.vnc);
    $('vgl').innerHTML=tg('virgl','VirGL',sv.virgl);
    if(AI_BRIDGE){
      const ar=(n,l,on)=>`<div class="air"><span class="sw ${on?'':'off'}" data-a="${n}:${on?'stop':'start'}"><i></i></span><span class="lk" data-w="1" title="Ouvrir Open WebUI">${l}</span></div>`;
      $('ai').innerHTML=ar('llm','Qwen (NPU)',sv.llm)+ar('olala','Olala (CPU)',sv.olala);
    }
    // gouverneur : affichage dans un navigateur ; dans l'app Pilote les pastilles et l'eclair sont cliquables (pont Android)
    const g=d.gov;
    if(g){
      let pos=!g.manual?0:({powersave:1,uag:2,performance:3}[g.governor]??2);
      if(gvPending&&Date.now()-gvPending.t<6000){ if(pos===gvPending.pos) gvPending=null; else pos=gvPending.pos; }
      const clk=GV_BRIDGE?' clk':'';
      const chips=['Auto','\u00c9co','Normal','Max'].map((l,i)=>`<span class="${(i===pos?'on':'')+clk}" data-g="${i}">${l}</span>`).join('');
      $('gv').innerHTML=`<div class="nm">Gouverneur${g.clamp?' <span title="frequence max plafonnee (constructeur ou garde-fou thermique)">&#128274;</span>':''}${GV_BRIDGE?'<b class="gbo" data-b="1" title="Rebooster la performance">&#9889;</b>':''}</div><div class="gch">${chips}</div><div class="dt">${g.governor} - max ${f(g.cap_mhz,0)} MHz</div>`;
    }
    const r=d.ram, s=r.swap;
    // memoire unifiee : la RAM utilisee se decoupe en RAM systeme + VRAM GPU + RAM NPU
    // RAM IA = memoire partagee du NPU (dma-buf) + memoire privee des processus IA (llama-server)
    const vr=d.vram.used||0, nr=(d.nram.used||0)+(r.ai||0), sys=Math.max(0,(r.used||0)-vr-nr);
    const cap=(r.total||0)+(s.total||0), pc=x=>cap?(100*x/cap).toFixed(1):'0', mark=cap?100*r.total/cap:0;
    // memoire propre du chroot (PSS de ses processus) : une partie de la RAM systeme, en bleu comme ailleurs
    const chm=Math.min(r.chroot||0,sys);
    $('ram').innerHTML=`<h2><svg class="ic"><use href="#i-mem"/></svg>M&eacute;moire</h2>
      <div class="mbar" title="chroot ${gb1(chm)} + autre RAM ${gb1(sys-chm)} + VRAM ${gb1(vr)} + RAM IA ${gb1(nr)} Go, swap ${gb1(s.used)} Go, sur ${gb1(cap)} Go">
        <i style="left:0;width:${pc(chm)}%;background:#58a6ff"></i>
        <i style="left:${pc(chm)}%;width:${pc(sys-chm)}%;background:#f0883e"></i>
        <i style="left:${pc(sys)}%;width:${pc(vr)}%;background:#2dd4bf"></i>
        <i style="left:${(+pc(sys)+ +pc(vr)).toFixed(1)}%;width:${pc(nr)}%;background:#db61a2"></i>
        <i style="right:0;width:${pc(s.used||0)}%;background:#a371f7"></i>
        <u style="left:${mark.toFixed(1)}%"></u></div>
      <div class="mtx"><span class="l"><span><s style="background:#58a6ff"></s>Chroot ${gb1(chm)}</span>
        <span><s style="background:#f0883e"></s>Autre RAM ${gb1(sys-chm)}</span>
        <span><s style="background:#2dd4bf"></s>VRAM ${gb1(vr)}</span>
        <span><s style="background:#db61a2"></s>RAM IA ${gb1(nr)}</span>
        </span></div>
      <div class="mtx"><span>RAM ${gb1(r.used)} / ${gb1(r.total)} Go</span><span><s style="background:#a371f7"></s>Swap ${gb1(s.used)} / ${gb1(s.total)} Go</span></div>`;
    const gAbs=d.gpu.freq_max>0?(d.gpu.usage||0)*d.gpu.freq/d.gpu.freq_max:0;
    $('gpu').innerHTML=cell('',gAbs,'hsl(190,70%,50%)',tr(d.gpu.temp,'gpu',`${f(d.gpu.freq,0)} MHz (max ${f(d.gpu.freq_max,0)})`),tempBar(d.gpu.temp,'gpu'));
    $('npu').innerHTML=cell('',d.npu.usage||0,'hsl(210,80%,55%)',d.npu.usage==null?'serveur arrete':tr(d.npu.temp,'npu',d.npu.usage>0?'en calcul':'au repos'),tempBar(d.npu.temp,'npu'));
    // encodeur video materiel : charge estimee = pixels traites par seconde / capacite annoncee (8K a 30 i/s)
    const vc=d.vidc||{}, vs=(vc.sessions||[])[0];
    $('vpu').innerHTML=cell('',vc.load||0,'#e3b341',(vs?tr(vc.temp,'vpu',`${vs.type==='Encoder'?'encodage':'d\u00e9codage'} ${vs.width}\u00d7${vs.height} ${vs.fps!=null?f(vs.fps,0)+' i/s':''} - horloge ${f(vc.clock_mhz,0)} MHz`):tr(vc.temp,'vpu','au repos')),tempBar(vc.temp,'vpu'));
    // conso estimee : modele d'energie Qualcomm x charge de chaque coeur ; part du chroot = temps CPU de ses processus
    // puissance en watts, moyenne sur 2 s ; barre pleine = consommation max theorique du CPU (8 coeurs a 100 % a frequence max)
    const p=d.power;
    if(p){
      const MAX=p.cpu_max_w||4.85, ch=p.chroot_now, cpu=p.cpu_now, vd=Math.min(p.video_now||0,ch), ai=Math.min(p.ai_now||0,Math.max(cpu-ch,0));
      const v=100*Math.min(vd,MAX)/MAX, a=100*Math.min(ch-vd,MAX)/MAX, ia=100*Math.min(ai,MAX)/MAX, b=100*Math.min(Math.max(cpu-ch-ai,0),MAX)/MAX;
      // puissance totale mesuree : 2e barre (echelle 10 W) = part CPU estimee + autres (ecran, GPU, encodeur, memoire, radio, pertes)
      const TOTMAX=10, tot=p.total_w, srcTxt=({chargeur:'chargeur','chargeur~':'chargeur, en charge',batterie:'batterie'})[p.total_src]||'';
      // total = CPU (estime) + ecran (estime par Android, 0 ecran eteint) + reste ; la batterie n'apparait que quand elle fournit de l'energie
      const rest0=tot==null?0:Math.max(tot-cpu,0), scr=tot==null?0:Math.min(p.screen_w||0,rest0), rest=Math.max(rest0-scr,0);
      const P=x=>(100*Math.min(x,TOTMAX)/TOTMAX).toFixed(1), tcpu=tot==null?0:Math.min(cpu,tot);
      const bw=p.batt_w10, battTxt=(bw!=null&&bw>=0.05)?`<div class="dt">Batterie : ${w2(bw)} (moy. 10 min) \u00b7 ${(p.batt_wh<1?p.batt_wh.toFixed(3):p.batt_wh.toFixed(2)).replace('.',',')} Wh depuis ${Math.max(1,Math.round(p.batt_since_s/60))} min</div>`:'';
      // energie consommee (Wh) = puissance (W) accumulee dans le temps ; la barre montre la puissance du moment (moy. 2 s)
      const wh=v=>v==null?'--':(v<1?v.toFixed(3):v.toFixed(2)).replace('.',',')+' Wh';
      const dur=s=>s>=3600?Math.floor(s/3600)+' h '+String(Math.floor(s%3600/60)).padStart(2,'0'):Math.max(1,Math.round(s/60))+' min';
      $('pw').innerHTML=`<h2><svg class="ic"><use href="#i-pow"/></svg>Puissance</h2>
        <div class="mbar" title="puissance (moy. 2 s) ; barre pleine = ${w2(MAX)} : 8 coeurs a 100 % a frequence max (modele d energie du noyau)"><i style="left:0;width:${v.toFixed(1)}%;background:#e3b341"></i><i style="left:${v.toFixed(1)}%;width:${a.toFixed(1)}%;background:#58a6ff"></i><i style="left:${(v+a).toFixed(1)}%;width:${ia.toFixed(1)}%;background:#db61a2"></i><i style="left:${(v+a+ia).toFixed(1)}%;width:${b.toFixed(1)}%;background:#6e7681"></i></div>
        <div class="mtx"><span><s style="background:#e3b341"></s>Vid\\u00e9o ${w2(vd)}</span><span><s style="background:#58a6ff"></s>Chroot ${w2(ch)}</span><span><s style="background:#db61a2"></s>IA ${w2(ai)}</span><span><s style="background:#6e7681"></s>CPU ${w2(cpu)}</span>${tot==null?`<span>max ${w2(MAX)} (moy. 2 s)</span>`:''}</div>${tot==null?'':`
        <div class="mbar tbar" title="puissance TOTALE du telephone, mesuree (${srcTxt}) ; barre pleine = ${TOTMAX} W. CPU = estimation du modele d'energie du noyau ; ecran = moyenne estimee par Android (ne suit pas la luminosite) ; reste = GPU, encodeur, memoire, radio, pertes."><i style="left:0;width:${P(tcpu)}%;background:#6e7681"></i><i style="left:${P(tcpu)}%;width:${P(scr)}%;background:#aab4c0"></i><i style="left:${P(tcpu+scr)}%;width:${P(rest)}%;background:#e6edf3"></i></div>
        <div class="mtx"><span><s style="background:#aab4c0"></s>\u00c9cran \u2248${w2(scr)}</span><span><s style="background:#e6edf3"></s>Reste ${w2(rest)}</span><span>Total ${w2(tot)} (${srcTxt})</span></div>${battTxt}`}`;
    } else $('pw').innerHTML='<h2><svg class="ic"><use href="#i-pow"/></svg>Puissance</h2><div class="dt">mesure en cours...</div>';
    const n=d.net||{}, lab={wifi:'Wi-Fi',mobile:'4G/5G'};   // WireGuard retire pour le moment
    const rx=(n.wifi?n.wifi[0]:0)+(n.mobile?n.mobile[0]:0), tx=(n.wifi?n.wifi[1]:0)+(n.mobile?n.mobile[1]:0);
    // entrant et sortant : meme echelle dynamique (max des 2 dernieres minutes), valeur max en bout de barre
    const ns=dynScale('net',[rx,tx]);
    const nc=d.net_chroot, crx=nc?nc[0]:null, ctx=nc?nc[1]:null;
    const sq='<s style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;background:#58a6ff;text-decoration:none"></s>';
    // barres a deux segments : part du chroot (bleu) + reste du telephone (gris), meme echelle dynamique pour les deux sens
    $('net').innerHTML=`<div class="dt">\u2193 Entrant ${rate(rx)}</div>${sbar2(rx,crx,ns,'entrant')}`
      +`<div class="dt">\u2191 Sortant ${rate(tx)}</div>${sbar2(tx,ctx,ns,'sortant')}`
      +`<div class="dt">${sq}chroot \u2193 ${rate(crx)} \u2191 ${rate(ctx)}</div>`;
    const k=d.disk;
    const ds=k?dynScale('dsk',[k.read,k.write]):1;
    $('dsk').innerHTML=k?`<div class="dt">Lecture ${rate(k.read)}</div>${sbar2(k.read,k.chroot_read,ds,'lecture')}<div class="dt">\u00c9criture ${rate(k.write)}</div>${sbar2(k.write,k.chroot_write,ds,'ecriture')}<div class="dt">${sq}chroot ${rate(k.chroot_read)} \u00b7 ${rate(k.chroot_write)}</div>`:'<div class="dt">mesure en cours...</div>';
  }catch(e){document.getElementById('t').textContent='hors ligne';}
}
tick();setInterval(tick,2000);
</script></body></html>"""

@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/resume")
def resume():
    return Response(SUMMARY, mimetype="text/html")


if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=telemetry, daemon=True).start()
    threading.Thread(target=_pv_loop, daemon=True).start()
    threading.Thread(target=_screen_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
