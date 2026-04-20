"""
walabeat2_gui.py — Walabot 8-pad drum machine with top-down radar view.

4 azimuth zones × 2 depth zones = 8 pads.
Wave a hand in any sector to hit that drum.

    FAR   [Crash] [Tom]  [Ride]  [Open HH]
    NEAR  [HiHat] [Kick] [Snare] [Clap]
"""
from __future__ import print_function, division
import os, math, subprocess, signal
import WalabotAPI as wlbt
try:
    import tkinter as tk
except ImportError:
    import Tkinter as tk

# Auto-reap finished aplay subprocesses (no zombies = no audio dropouts)
signal.signal(signal.SIGCHLD, signal.SIG_IGN)

# ── Audio ─────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))

def _wav(name):
    return os.path.join(_DIR, name + '.wav')

def _play(path):
    subprocess.Popen(['aplay', '-q', path],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ── Pad definitions ────────────────────────────────────────────────────────────
# (id, label, r_idx 0=near/1=far, phi_idx 0‥3 left→right, idle_color, hit_color, wav)
PADS = [
    ('hihat',   'HI-HAT',  0, 0, '#112244', '#3388FF', _wav('hh')),
    ('kick',    'KICK',    0, 1, '#441111', '#FF3333', _wav('kick')),
    ('snare',   'SNARE',   0, 2, '#443311', '#FF9933', _wav('snare')),
    ('clap',    'CLAP',    0, 3, '#334411', '#AAFF33', _wav('clap')),
    ('crash',   'CRASH',   1, 0, '#441166', '#BB44FF', _wav('crash')),
    ('tom',     'TOM',     1, 1, '#114422', '#44EE66', _wav('tom')),
    ('ride',    'RIDE',    1, 2, '#114444', '#22DDDD', _wav('ride')),
    ('openhh',  'OPEN HH', 1, 3, '#443322', '#FFCC55', _wav('open_hh')),
]

# ── Detection constants ────────────────────────────────────────────────────────
ENERGY_THRESHOLD = 300
DELAY_FRAMES     = 5   # at ~30fps this is 165ms > FLASH_MS, so glow never races the restore
FLASH_MS         = 140
BAR_MAX          = 1500

# ── Drum-roll constants (NEAR + outer-right = bottom-right of fan) ────────────
ROLL_WAV            = _wav('snare')  # snare roll
ROLL_THRESHOLD      = 300            # same zone size as one pad (~16 cells near+outer-right)
ROLL_LOCKOUT_FRAMES = 2              # sustained: fires every 3 frames while above threshold
ROLL_FLASH_MS       = 80             # badge flash duration

# ── Walabot arena ─────────────────────────────────────────────────────────────
R_MIN, R_MAX, R_RES         = 15, 60, 10
PHI_MIN, PHI_MAX, PHI_RES   = -60, 60, 5
THETA_MIN, THETA_MAX, THETA_RES = -1, 1, 1

# ── Canvas geometry ───────────────────────────────────────────────────────────
CW, CH     = 580, 390
SX, SY     = CW // 2, 360   # sensor position (bottom-centre of canvas)
R_NEAR_PX  = 130
R_FAR_PX   = 270

# Dead zone gaps (pixels / degrees) shown as dark space between active sectors.
# Detection code skips the corresponding data bins too (see start_scan).
DEAD_R_PX   = 20   # pixel gap at near/far radial boundary
DEAD_PHI_DEG = 4   # angular gap between adjacent phi sectors (half applied per side)

# Active sector screen-angle ranges (shrunk inward from full 30° zones by DEAD_PHI_DEG):
# outer sectors only lose half-gap on their inner edge (canvas edge = their outer boundary)
PHI_SECTORS = [
    (120 + DEAD_PHI_DEG, 150),                            # index 0: outer-left
    ( 90 + DEAD_PHI_DEG,  120 - DEAD_PHI_DEG),            # index 1: inner-left
    ( 60 + DEAD_PHI_DEG,   90 - DEAD_PHI_DEG),            # index 2: inner-right
    ( 30,                  60 - DEAD_PHI_DEG),             # index 3: outer-right
]


def sector_poly(cx, cy, r_in, r_out, a0, a1, steps=20):
    """Flat [x,y,…] polygon for an annular sector (math angles, degrees)."""
    pts = []
    for k in range(steps + 1):
        a = math.radians(a0 + k * (a1 - a0) / steps)
        pts += [cx + r_out * math.cos(a), cy - r_out * math.sin(a)]
    for k in range(steps + 1):
        a = math.radians(a1 - k * (a1 - a0) / steps)
        pts += [cx + r_in * math.cos(a), cy - r_in * math.sin(a)]
    return pts


def label_pos(cx, cy, r, a0, a1):
    a = math.radians((a0 + a1) / 2)
    return cx + r * math.cos(a), cy - r * math.sin(a)


# ── App ────────────────────────────────────────────────────────────────────────
class DrumApp(tk.Frame):

    def __init__(self, master):
        tk.Frame.__init__(self, master, bg='#0a0a0a')
        self.pad_state  = {p[0]: 'out' for p in PADS}
        self.pad_delay  = {p[0]: 0     for p in PADS}
        self.pad_hits   = {p[0]: 0     for p in PADS}
        self.cycleId    = None
        self.r_ranges   = None
        self.phi_ranges = None
        self.sX         = None
        # Drum-roll state
        self.roll_lockout = 0
        self.roll_hits    = 0

        self.statusVar = tk.StringVar(value='Connecting...')
        tk.Label(self, textvariable=self.statusVar, font='TkFixedFont 9',
                 bg='#0a0a0a', fg='#aaaaaa', anchor=tk.W
                 ).pack(fill=tk.X, padx=6, pady=(4, 0))

        self.canvas = tk.Canvas(self, width=CW, height=CH,
                                bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(padx=8, pady=4)

        self._build_canvas()

        self._init_walabot()
        self.after(200, self.start_scan)

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _build_canvas(self):
        c = self.canvas
        self.poly_ids  = {}
        self.label_ids = {}
        self.count_ids = {}

        # Guide arcs and dividing lines
        for r in (R_NEAR_PX, R_FAR_PX):
            c.create_arc(SX-r, SY-r, SX+r, SY+r,
                         start=30, extent=120,
                         outline='#333', style=tk.ARC, width=1)
        for a_deg in (30, 60, 90, 120, 150):
            a = math.radians(a_deg)
            c.create_line(SX, SY,
                          SX + R_FAR_PX * math.cos(a),
                          SY - R_FAR_PX * math.sin(a),
                          fill='#333', width=1)

        # Sensor dot
        c.create_oval(SX-6, SY-6, SX+6, SY+6, fill='#555', outline='#888')
        c.create_text(SX, SY+14, text='SENSOR', fill='#555', font='TkFixedFont 7')

        # Zone depth labels
        r_near_label = R_NEAR_PX * 0.55
        r_far_label  = (R_NEAR_PX + R_FAR_PX) / 2
        c.create_text(SX - R_FAR_PX - 8, SY - r_far_label,
                      text='FAR', fill='#444', font='TkFixedFont 8', anchor=tk.E)
        c.create_text(SX - R_FAR_PX - 8, SY - r_near_label,
                      text='NEAR', fill='#444', font='TkFixedFont 8', anchor=tk.E)

        # ROLL badge — bottom-right canvas corner (below the CLAP sector)
        # Canvas bottom-right empty area: x≈415-572, y≈310-378
        _bx1, _by1, _bx2, _by2 = 418, 312, 572, 375
        _bmx = (_bx1 + _bx2) // 2
        self.roll_badge_bg = c.create_rectangle(
            _bx1, _by1, _bx2, _by2,
            fill='#111b11', outline='#335533', width=2)
        c.create_text(_bmx, _by1 + 16,
                      text='↕  ROLL', fill='#446644',
                      font='TkFixedFont 10 bold', anchor=tk.CENTER)
        self.roll_count_id = c.create_text(
            _bmx, _by1 + 34,
            text='', fill='#557755', font='TkFixedFont 9', anchor=tk.CENTER)
        c.create_text(_bmx, _by2 - 10,
                      text='wave near-right rapidly',
                      fill='#2a3a2a', font='TkFixedFont 7', anchor=tk.CENTER)

        # Sector polygons, name labels, hit count labels
        for pid, label, r_idx, phi_idx, col_idle, col_hit, wav in PADS:
            # Inset by DEAD_R_PX at the near/far boundary to show dead zone gap
            r_in  = 5                            if r_idx == 0 else R_NEAR_PX + DEAD_R_PX
            r_out = R_NEAR_PX - DEAD_R_PX        if r_idx == 0 else R_FAR_PX
            r_lbl = (r_in + r_out) / 2
            a0, a1 = PHI_SECTORS[phi_idx]

            pts = sector_poly(SX, SY, r_in, r_out, a0, a1)
            poly_id = c.create_polygon(*pts, fill=col_idle, outline='#555', width=1)
            self.poly_ids[pid] = (poly_id, col_idle, col_hit)

            lx, ly = label_pos(SX, SY, r_lbl, a0, a1)
            self.label_ids[pid] = c.create_text(
                lx, ly, text=label, fill='#cccccc',
                font='TkFixedFont 8 bold', anchor=tk.CENTER)
            self.count_ids[pid] = c.create_text(
                lx, ly + 13, text='0', fill='#666',
                font='TkFixedFont 8', anchor=tk.CENTER)

    # ── Walabot ───────────────────────────────────────────────────────────────

    def _init_walabot(self):
        wlbt.Init()
        wlbt.SetSettingsFolder()
        wlbt.ConnectAny()
        wlbt.SetProfile(wlbt.PROF_SENSOR)
        wlbt.SetArenaR(R_MIN, R_MAX, R_RES)
        wlbt.SetArenaPhi(PHI_MIN, PHI_MAX, PHI_RES)
        wlbt.SetArenaTheta(THETA_MIN, THETA_MAX, THETA_RES)
        wlbt.SetDynamicImageFilter(wlbt.FILTER_TYPE_MTI)
        wlbt.SetThreshold(35)
        wlbt.Start()

    def start_scan(self):
        self.statusVar.set('Warming up...')
        for _ in range(5):
            wlbt.Trigger()
        wlbt.Trigger()
        res = wlbt.GetRawImageSlice()
        sX, sY = res[1], res[2]
        self.sX = sX   # stored for roll detection (all R bins)

        # 2 R zones — skip 1 bin at the near/far boundary as dead zone
        mid = sX // 2
        self.r_ranges = [range(0, mid), range(mid + 1, sX)]

        # 4 phi zones — skip 1 bin on each side facing an adjacent zone
        q = sY // 4
        self.phi_ranges = [
            range(0,           q - 1),
            range(q + 1,   2*q - 1),
            range(2*q + 1, 3*q - 1),
            range(3*q + 1,     sY),
        ]

        self.statusVar.set('Ready  |  sX={} sY={}'.format(sX, sY))
        self.cycleId = self.after(33, self.loop)   # ~30fps cap

    def loop(self):
        try:
            wlbt.Trigger()
            res = wlbt.GetRawImageSlice()
            img = res[0]
        except wlbt.WalabotError as e:
            self.statusVar.set('Error: {}'.format(e))
            return

        for pid, label, r_idx, phi_idx, col_idle, col_hit, wav in PADS:
            energy = sum(img[i][j]
                         for i in self.r_ranges[r_idx]
                         for j in self.phi_ranges[phi_idx])

            # Glow proportional to energy
            if self.pad_delay[pid] == 0:
                frac = min(energy / float(BAR_MAX), 1.0) * 0.45
                r1,g1,b1 = self._hex_rgb(col_idle)
                r2,g2,b2 = self._hex_rgb(col_hit)
                fill = '#{:02x}{:02x}{:02x}'.format(
                    int(r1 + (r2-r1)*frac),
                    int(g1 + (g2-g1)*frac),
                    int(b1 + (b2-b1)*frac))
                self.canvas.itemconfigure(self.poly_ids[pid][0], fill=fill)

            # Hit detection
            if energy > ENERGY_THRESHOLD:
                if self.pad_state[pid] == 'out' and self.pad_delay[pid] == 0:
                    self._hit(pid, wav, col_hit)
                self.pad_state[pid] = 'in'
            else:
                self.pad_state[pid] = 'out'
            if self.pad_delay[pid] > 0:
                self.pad_delay[pid] -= 1

        # ── Drum-roll detection: NEAR + outer-right (bottom-right of fan) ───────
        # Sustained trigger: fires every ROLL_LOCKOUT_FRAMES+1 frames while above threshold
        roll_e = sum(img[i][j]
                     for i in self.r_ranges[0]    # NEAR range only
                     for j in self.phi_ranges[3])
        if self.roll_lockout > 0:
            self.roll_lockout -= 1
        if roll_e > ROLL_THRESHOLD and self.roll_lockout == 0:
            self._roll_hit()

        self.cycleId = self.after(33, self.loop)   # ~30fps cap

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hit(self, pid, wav, col_hit):
        self.pad_hits[pid] += 1
        self.pad_delay[pid] = DELAY_FRAMES
        poly_id, col_idle, _ = self.poly_ids[pid]
        self.canvas.itemconfigure(poly_id, fill=col_hit)
        self.after(FLASH_MS, lambda p=poly_id, c=col_idle:
                   self.canvas.itemconfigure(p, fill=c))
        self.canvas.itemconfigure(self.count_ids[pid],
                                  text=str(self.pad_hits[pid]))
        _play(wav)

    def _roll_hit(self):
        self.roll_hits += 1
        self.roll_lockout = ROLL_LOCKOUT_FRAMES
        # Flash ONLY the dedicated badge — never write to pad polygons (avoids glow conflict)
        self.canvas.itemconfigure(self.roll_badge_bg, fill='#aaffaa')
        self.after(ROLL_FLASH_MS, lambda: self.canvas.itemconfigure(
            self.roll_badge_bg, fill='#111b11'))
        self.canvas.itemconfigure(self.roll_count_id, text=str(self.roll_hits))
        _play(ROLL_WAV)

    @staticmethod
    def _hex_rgb(h):
        h = h.lstrip('#')
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def on_close(self):
        if self.cycleId:
            self.after_cancel(self.cycleId)
        try:
            wlbt.Stop()
            wlbt.Disconnect()
        except Exception:
            pass
        self.master.destroy()


def main():
    root = tk.Tk()
    root.title('Walabot Drum Machine 2')
    root.configure(bg='#0a0a0a')
    root.option_add('*Font', 'TkFixedFont')
    app = DrumApp(root)
    app.pack(fill=tk.BOTH, expand=True)
    root.update_idletasks()
    root.resizable(False, False)
    root.protocol('WM_DELETE_WINDOW', app.on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
