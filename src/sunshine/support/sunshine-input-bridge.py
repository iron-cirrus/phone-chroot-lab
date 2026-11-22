#!/usr/bin/env python3
"""
Pont d'entrees Sunshine -> Xvnc.
Sunshine cree des peripheriques virtuels du noyau (uinput, nommes "libvirtualhid ...").
Xvnc ne les lit pas, et Android les adopte. Ce pont :
  - les prend en EXCLUSIVITE (EVIOCGRAB) : Android ne recoit plus ces evenements ;
  - rejoue chaque evenement dans l'ecran X :1 via l'extension XTest.
Les peripheriques apparaissant/disparaissant sont suivis toutes les 2 s.
"""
import os, select, sys, time, evdev
from evdev import ecodes as E
from Xlib import X, display
from Xlib.ext import xtest

PREFIX = "libvirtualhid"

# Sunshine convertit les codes Windows (VK) en positions de touches AMERICAINES (libvirtualhid).
# Pour un client AZERTY, on remet chaque touche a sa vraie position physique AZERTY,
# car le bureau est en disposition francaise. Numeros = codes evdev (linux/input-event-codes.h).
LAYOUT = "us"
try:
    for _l in open("/etc/sunshine-bridge.conf"):
        if _l.strip().startswith("client_layout") and "=" in _l:
            LAYOUT = _l.split("=", 1)[1].strip().lower()
except OSError:
    pass
AZERTY = {
    30: 16, 16: 30,    # A <-> Q
    44: 17, 17: 44,    # Z <-> W
    50: 39,            # M  -> position du ; americain
    26: 12,            # ) (VK_OEM_4)
    27: 26,            # ^ (VK_OEM_6)
    39: 27,            # $ (VK_OEM_1)
    41: 40,            # u grave (VK_OEM_3)
    51: 50,            # , (VK_OEM_COMMA)
    52: 51,            # ; (VK_OEM_PERIOD)
    53: 52,            # : (VK_OEM_2)
    40: 41,            # ² (VK_OEM_7)
}
KEYMAP = AZERTY if LAYOUT == "fr" else {}

d = display.Display(os.environ.get("DISPLAY", ":1"))
root = d.screen().root

def screen_size():
    g = root.get_geometry()
    return g.width, g.height

BTN = {E.BTN_LEFT: 1, E.BTN_MIDDLE: 2, E.BTN_RIGHT: 3, E.BTN_SIDE: 8, E.BTN_EXTRA: 9,
       E.BTN_TOUCH: 1, E.BTN_STYLUS: 3}

class Dev:
    def __init__(self, path):
        self.dev = evdev.InputDevice(path)
        self.dev.grab()                       # exclusivite : plus rien pour Android
        caps = self.dev.capabilities(absinfo=True)
        self.abs = {k: v for k, v in caps.get(E.EV_ABS, [])}
        self.ax = self.ay = None
        self.rx = self.ry = 0
        log(f"pris en charge : {path} ({self.dev.name})")

    def scale(self, code, val, size):
        info = self.abs.get(code)
        if not info or info.max == info.min:
            return val
        return int((val - info.min) * (size - 1) / (info.max - info.min))

    def handle(self, ev):
        if ev.type == E.EV_KEY:
            if ev.value == 2:                 # repetition auto : X gere la sienne
                return
            press = ev.value == 1
            if ev.code in BTN:
                xtest.fake_input(d, X.ButtonPress if press else X.ButtonRelease, BTN[ev.code])
            elif ev.code < 248:               # touches clavier : keycode X = evdev + 8
                xtest.fake_input(d, X.KeyPress if press else X.KeyRelease, KEYMAP.get(ev.code, ev.code) + 8)
        elif ev.type == E.EV_REL:
            if ev.code == E.REL_X:
                self.rx += ev.value           # cumule jusqu'au SYN_REPORT
            elif ev.code == E.REL_Y:
                self.ry += ev.value
            elif ev.code in (E.REL_WHEEL, E.REL_HWHEEL):
                if ev.code == E.REL_WHEEL:
                    b = 4 if ev.value > 0 else 5
                else:
                    b = 7 if ev.value > 0 else 6
                for _ in range(abs(ev.value)):
                    xtest.fake_input(d, X.ButtonPress, b)
                    xtest.fake_input(d, X.ButtonRelease, b)
        elif ev.type == E.EV_ABS and ev.code in (E.ABS_X, E.ABS_Y, E.ABS_MT_POSITION_X, E.ABS_MT_POSITION_Y):
            w, h = screen_size()
            if ev.code in (E.ABS_X, E.ABS_MT_POSITION_X):
                self.ax = self.scale(ev.code, ev.value, w)
            else:
                self.ay = self.scale(ev.code, ev.value, h)
        elif ev.type == E.EV_SYN and ev.code == E.SYN_REPORT:
            if self.rx or self.ry:            # un seul deplacement relatif par paquet
                cur = root.query_pointer()
                xtest.fake_input(d, X.MotionNotify, detail=0, x=cur.root_x + self.rx, y=cur.root_y + self.ry)
                self.rx = self.ry = 0
            if self.ax is not None or self.ay is not None:
                cur = root.query_pointer()
                x = self.ax if self.ax is not None else cur.root_x
                y = self.ay if self.ay is not None else cur.root_y
                xtest.fake_input(d, X.MotionNotify, detail=0, x=x, y=y)
                self.ax = self.ay = None
            d.flush()

def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)

devs = {}
last_scan = 0
log("pont d'entrees demarre (client_layout=%s, %d touches corrigees)" % (LAYOUT, len(KEYMAP)))
while True:
    now = time.time()
    if now - last_scan > 2:
        last_scan = now
        for p in evdev.list_devices():
            if p in devs:
                continue
            try:
                name = evdev.InputDevice(p).name
                if name.startswith(PREFIX):
                    devs[p] = Dev(p)
            except OSError:
                pass
    fds = {dv.dev.fd: p for p, dv in devs.items()}
    if not fds:
        time.sleep(1)
        continue
    r, _, _ = select.select(list(fds), [], [], 1.0)
    for fd in r:
        p = fds[fd]
        try:
            for ev in devs[p].dev.read():
                devs[p].handle(ev)
        except OSError:
            log(f"retire : {p}")
            devs.pop(p, None)
