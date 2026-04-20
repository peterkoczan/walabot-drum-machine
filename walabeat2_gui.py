"""
walabeat2_gui.py — Walabot 8-pad drum machine with top-down radar view.

4 azimuth zones × 2 depth zones = 8 pads.
Wave a hand in any sector to hit that drum.

    FAR   [Crash] [Tom]  [Ride]  [Open HH]
    NEAR  [HiHat] [Kick] [Snare] [Clap]
"""
from __future__ import print_function, division
import os, math, subprocess, signal, platform, threading, wave, struct
import WalabotAPI as wlbt
try:
    import tkinter as tk
except ImportError:
    import Tkinter as tk

# Reap zombie subprocesses on Linux so audio never blocks (no-op on other platforms)
if hasattr(signal, 'SIGCHLD'):
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

# ── Audio ─────────────────────────────────────────────────────────────────────
_DIR    = os.path.dirname(os.path.abspath(__file__))
_SYSTEM = platform.system()

def _wav(name):
    return os.path.join(_DIR, name + '.wav')


class _Mixer:
    """Single aplay process + in-process PCM mixer (Linux).

    Replicates pygame.mixer channel behaviour: every _play() call adds a new
    stream to the mix so sounds overlap naturally instead of cutting each other.
    One persistent aplay reads raw S16_LE mono from stdin; the mixing thread
    sums all active streams each chunk and writes the result.
    """
    RATE  = 44100
    CHUNK = 256   # ~6 ms per chunk — smaller = lower audio latency

    def __init__(self):
        self._streams = []          # list of open wave.Wave_read objects
        self._lock    = threading.Lock()
        self._proc    = subprocess.Popen(
            ['aplay', '-q', '-t', 'raw', '-f', 'S16_LE',
             '-r', str(self.RATE), '-c', '1', '-'],
            stdin=subprocess.PIPE,
            bufsize=0,              # unbuffered — every chunk reaches aplay immediately
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._run, daemon=True).start()

    def play(self, path):
        try:
            wf = wave.open(path, 'rb')
            with self._lock:
                self._streams.append(wf)
        except Exception:
            pass

    def _run(self):
        silence = b'\x00' * self.CHUNK * 2
        while True:
            with self._lock:
                alive, chunks = [], []
                for wf in self._streams:
                    data = wf.readframes(self.CHUNK)
                    if data:
                        chunks.append(data)
                        alive.append(wf)
                    else:
                        wf.close()
                self._streams = alive

            if chunks:
                out = [0] * self.CHUNK
                for data in chunks:
                    for i, s in enumerate(
                            struct.unpack('<%dh' % (len(data) // 2), data)):
                        out[i] += s
                buf = struct.pack(
                    '<%dh' % self.CHUNK,
                    *[max(-32768, min(32767, s)) for s in out])
            else:
                buf = silence   # keep aplay alive between hits

            try:
                self._proc.stdin.write(buf)
            except (BrokenPipeError, OSError):
                break


# Instantiate the right playback backend once at import time
if _SYSTEM == 'Linux':
    _mixer = _Mixer()
    def _play(path):
        _mixer.play(path)
elif _SYSTEM == 'Darwin':
    def _play(path):
        subprocess.Popen(['afplay', path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
elif _SYSTEM == 'Windows':
    def _play(path):
        import winsound
        threading.Thread(target=winsound.PlaySound,
                         args=(path, winsound.SND_FILENAME),
                         daemon=True).start()
else:
    def _play(path):
        pass

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
ENERGY_THRESHOLD = 300   # default; adjustable via slider at runtime
DELAY_FRAMES     = 8     # at 20ms/frame = 160ms > FLASH_MS=140ms — glow never races the restore
FLASH_MS         = 140
BAR_MAX          = 1500
# Radar signal attenuates ~R^4 with distance, so far-zone pads return far less
# raw energy than near-zone pads for the same hand movement. This multiplier
# scales far-zone energy before threshold comparison and glow to equalise sensitivity.
FAR_BOOST        = 3.0

# ── Drum-roll constants ────────────────────────────────────────────────────────
# Roll occupies the extreme-right phi strip (separate from CLAP/OpenHH).
# It spans ALL R depths — a full open-hand sweep over the strip fires it;
# a small two-finger touch won't reliably reach the extreme bins.
ROLL_WAV             = _wav('snare')
ROLL_LOCKOUT_FRAMES  = 2    # fires every 3 frames while sustained
ROLL_SUSTAIN_FRAMES  = 2    # frames above threshold before roll activates
ROLL_FLASH_MS        = 80
# Roll strip is only 3-4 phi bins wide; boost energy so threshold comparisons
# are proportional to the narrower zone (same hand wave → similar energy score)
ROLL_BOOST           = 3.0

# Screen-angle range for the roll-strip visual on the fan (extreme right sector)
ROLL_STRIP_A0 = 30
ROLL_STRIP_A1 = 44

# ── Two-hand target tracking ─────────────────────────────────────────────────
# GetSensorTargets() returns individual hand objects (xPosCm, zPosCm, amplitude).
# phi_deg = atan2(x, z), r_cm = hypot(x, z).  MTI filter means every returned
# target is a moving object — any target in a zone triggers that pad.
# Zones are in degrees; boundaries match the phi_ranges computed in start_scan().
PHI_ZONE_RANGES  = [(-60, -30), (-28, -2), (2, 28), (32, 44)]  # per pad zone
ROLL_PHI_MIN_DEG = 45      # targets with phi > this → roll strip
R_NEAR_MAX_CM    = 37      # near-zone threshold (cm)
R_FAR_MIN_CM     = 38      # far-zone threshold (cm)
MAX_TARGETS      = 6       # canvas dots — more than this is noise

# ── Walabot arena ─────────────────────────────────────────────────────────────
# Higher resolution for finer per-hand discrimination.
R_MIN, R_MAX, R_RES             = 15, 60, 5
PHI_MIN, PHI_MAX, PHI_RES       = -60, 60, 3
THETA_MIN, THETA_MAX, THETA_RES = -1, 1, 1

# ── Canvas geometry ───────────────────────────────────────────────────────────
CW, CH     = 580, 390
SX, SY     = CW // 2, 360   # sensor position (bottom-centre of canvas)
R_NEAR_PX  = 130
R_FAR_PX   = 270

# Dead zone gaps shown as dark space between active sectors.
# Detection code skips the corresponding data bins too (see start_scan).
DEAD_R_PX    = 20   # pixel gap at near/far radial boundary
DEAD_PHI_DEG =  4   # angular gap between adjacent phi sectors

# Active sector screen-angle ranges for the 8 drum pads.
# The outer-right zone (CLAP / Open HH) is narrowed: the 3 extreme-right phi bins
# are reserved for the roll strip (ROLL_STRIP_A0..ROLL_STRIP_A1), drawn separately.
PHI_SECTORS = [
    (120 + DEAD_PHI_DEG, 150),
    ( 90 + DEAD_PHI_DEG, 120 - DEAD_PHI_DEG),
    ( 60 + DEAD_PHI_DEG,  90 - DEAD_PHI_DEG),
    ( ROLL_STRIP_A1 + 2,  60 - DEAD_PHI_DEG),   # outer-right — narrowed; roll strip on right
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


def target_canvas_pos(phi_deg, r_cm):
    """Map a Walabot target's (phi, r) to canvas (x, y)."""
    r_px = R_NEAR_PX + (r_cm - R_MIN) * (R_FAR_PX - R_NEAR_PX) / float(R_MAX - R_MIN)
    r_px = max(5, min(R_FAR_PX, r_px))
    a = math.radians(90.0 - phi_deg)   # screen angle: 90° = forward, right = less
    return SX + r_px * math.cos(a), SY - r_px * math.sin(a)


# ── App ────────────────────────────────────────────────────────────────────────
class DrumApp(tk.Frame):

    def __init__(self, master):
        tk.Frame.__init__(self, master, bg='#0a0a0a')
        self.pad_state    = {p[0]: 'out' for p in PADS}
        self.pad_delay    = {p[0]: 0     for p in PADS}
        self.pad_hits     = {p[0]: 0     for p in PADS}
        self.cycleId        = None
        self.r_ranges       = None
        self.phi_ranges     = None
        self.roll_phi_range = None
        self.roll_lockout   = 0
        self.roll_sustain   = 0
        self._dbg           = 0   # diagnostic frame counter — remove after calibration
        self.roll_hits      = 0
        self.target_dots    = []   # canvas oval IDs for live hand-position indicators
        self.threshold    = ENERGY_THRESHOLD   # live-adjustable via slider

        self.statusVar = tk.StringVar(value='Connecting...')
        tk.Label(self, textvariable=self.statusVar, font='TkFixedFont 9',
                 bg='#0a0a0a', fg='#aaaaaa', anchor=tk.W
                 ).pack(fill=tk.X, padx=6, pady=(4, 0))

        self.canvas = tk.Canvas(self, width=CW, height=CH,
                                bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(padx=8, pady=4)

        self._build_canvas()
        self._build_controls()

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

        # Zone depth labels — guide the user's gestures
        c.create_text(SX - R_FAR_PX - 8, SY - (R_NEAR_PX + R_FAR_PX) / 2,
                      text='FULL HAND', fill='#444', font='TkFixedFont 7', anchor=tk.E)
        c.create_text(SX - R_FAR_PX - 8, SY - R_NEAR_PX * 0.55,
                      text='2 FINGERS', fill='#444', font='TkFixedFont 7', anchor=tk.E)

        # Roll strip — narrow sector at extreme right, full radial depth
        roll_pts = sector_poly(SX, SY, 5, R_FAR_PX, ROLL_STRIP_A0, ROLL_STRIP_A1)
        self.roll_zone_id = c.create_polygon(*roll_pts, fill='#111b11', outline='#335533', width=1)
        rlx, rly = label_pos(SX, SY, (5 + R_FAR_PX) / 2, ROLL_STRIP_A0, ROLL_STRIP_A1)
        c.create_text(rlx, rly - 8,  text='↕',    fill='#446644', font='TkFixedFont 11 bold', anchor=tk.CENTER)
        c.create_text(rlx, rly + 8,  text='ROLL',  fill='#446644', font='TkFixedFont 7',      anchor=tk.CENTER)
        self.roll_count_id = c.create_text(
            rlx, rly + 22, text='', fill='#557755', font='TkFixedFont 8', anchor=tk.CENTER)

        # Sector polygons, name labels, hit count labels
        for pid, label, r_idx, phi_idx, col_idle, col_hit, wav in PADS:
            r_in  = 5                       if r_idx == 0 else R_NEAR_PX + DEAD_R_PX
            r_out = R_NEAR_PX - DEAD_R_PX   if r_idx == 0 else R_FAR_PX
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

        # Live hand-position dots (one per tracked target, hidden until visible)
        for _ in range(MAX_TARGETS):
            dot = c.create_oval(0, 0, 0, 0, fill='#ffffff', outline='#00ffff',
                                width=2, state=tk.HIDDEN)
            self.target_dots.append(dot)

    def _build_controls(self):
        bar = tk.Frame(self, bg='#0a0a0a')
        bar.pack(fill=tk.X, padx=8, pady=(0, 6))

        tk.Label(bar, text='THRESHOLD', font='TkFixedFont 7',
                 bg='#0a0a0a', fg='#555').pack(side=tk.LEFT, padx=(0, 4))

        self.threshVar = tk.IntVar(value=ENERGY_THRESHOLD)
        self.threshVar.trace_add('write', self._on_threshold_change)
        tk.Scale(bar, from_=50, to=1000, orient=tk.HORIZONTAL,
                 variable=self.threshVar, showvalue=True,
                 bg='#0a0a0a', fg='#888888', troughcolor='#1a1a1a',
                 activebackground='#444', highlightthickness=0,
                 font='TkFixedFont 7', length=420, sliderlength=12,
                 bd=0).pack(side=tk.LEFT)

        tk.Button(bar, text='RESET', font='TkFixedFont 8',
                  bg='#1a1a1a', fg='#888888', activebackground='#333',
                  activeforeground='#ffffff', relief=tk.FLAT, bd=1,
                  padx=8, command=self._reset).pack(side=tk.RIGHT, padx=(8, 0))

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

        # 2 R zones — skip 1 bin at the near/far boundary as dead zone
        mid = sX // 2
        self.r_ranges = [range(0, mid), range(mid + 1, sX)]

        # 4 drum-pad phi zones — skip 1 bin on each side facing an adjacent zone.
        # The extreme-right 3 bins are reserved exclusively for the roll strip.
        q = sY // 4
        self.phi_ranges = [
            range(0,           q - 1),        # outer-left
            range(q + 1,   2*q - 1),          # inner-left
            range(2*q + 1, 3*q - 1),          # inner-right
            range(3*q + 1, sY - 4),           # outer-right (CLAP / Open HH)
        ]
        # Roll strip: extreme-right 4 bins, spans ALL R depths (full arm sweep)
        self.roll_phi_range = range(sY - 4, sY)

        self._update_status()
        self.cycleId = self.after(20, self.loop)

    def loop(self):
        try:
            wlbt.Trigger()
            res     = wlbt.GetRawImageSlice()
            img     = res[0]
            targets = wlbt.GetSensorTargets()   # for canvas dots only; may return empty
        except wlbt.WalabotError:
            self.statusVar.set('Lost connection — reconnecting…')
            self.cycleId = self.after(2000, self._reconnect)
            return

        thresh = self.threshold
        sX = len(img)

        # ── Canvas dots: show hand positions if sensor provides targets ────────
        # (GetSensorTargets may return empty list with PROF_SENSOR + narrow theta)
        dot_positions = []
        for t in targets:
            r_cm    = t.xPosCm
            phi_deg = t.yPosCm
            if R_MIN <= r_cm <= R_MAX and PHI_MIN <= phi_deg <= PHI_MAX:
                dot_positions.append((phi_deg, r_cm))

        for k, dot_id in enumerate(self.target_dots):
            if k < len(dot_positions):
                phi_deg, r_cm = dot_positions[k]
                cx, cy = target_canvas_pos(phi_deg, r_cm)
                self.canvas.coords(dot_id, cx - 7, cy - 7, cx + 7, cy + 7)
                self.canvas.itemconfigure(dot_id, state=tk.NORMAL)
            else:
                self.canvas.itemconfigure(dot_id, state=tk.HIDDEN)

        # ── Pad glow + hit — energy-based (two hands in different zones both
        #    fire independently since each zone's energy sum is computed separately)
        for pid, label, r_idx, phi_idx, col_idle, col_hit, wav in PADS:
            energy = sum(img[i][j]
                         for i in self.r_ranges[r_idx]
                         for j in self.phi_ranges[phi_idx])
            if r_idx == 1:
                energy *= FAR_BOOST

            # Glow proportional to energy (suppressed during flash)
            if self.pad_delay[pid] == 0:
                frac = min(energy / float(BAR_MAX), 1.0) * 0.45
                r1, g1, b1 = self._hex_rgb(col_idle)
                r2, g2, b2 = self._hex_rgb(col_hit)
                fill = '#{:02x}{:02x}{:02x}'.format(
                    int(r1 + (r2-r1)*frac),
                    int(g1 + (g2-g1)*frac),
                    int(b1 + (b2-b1)*frac))
                self.canvas.itemconfigure(self.poly_ids[pid][0], fill=fill)

            # Hit detection
            if energy > thresh:
                if self.pad_state[pid] == 'out' and self.pad_delay[pid] == 0:
                    self._hit(pid, wav, col_hit)
                self.pad_state[pid] = 'in'
            else:
                self.pad_state[pid] = 'out'
            if self.pad_delay[pid] > 0:
                self.pad_delay[pid] -= 1

        # ── Roll strip — energy-based (more stable for rapid motion) ──────────
        roll_e = sum(img[i][j]
                     for i in range(sX)
                     for j in self.roll_phi_range) * ROLL_BOOST

        if self.roll_lockout == 0:
            roll_frac = min(roll_e / float(BAR_MAX), 1.0) * 0.45
            self.canvas.itemconfigure(
                self.roll_zone_id,
                fill='#{:02x}{:02x}{:02x}'.format(
                    int(0x11 + (0x44 - 0x11) * roll_frac),
                    int(0x1b + (0xff - 0x1b) * roll_frac),
                    int(0x11 + (0x44 - 0x11) * roll_frac)))

        if roll_e > thresh:
            self.roll_sustain = min(self.roll_sustain + 1, ROLL_SUSTAIN_FRAMES + 1)
        else:
            self.roll_sustain = 0

        if self.roll_lockout > 0:
            self.roll_lockout -= 1
        if self.roll_sustain >= ROLL_SUSTAIN_FRAMES and self.roll_lockout == 0:
            self._roll_hit()

        total = sum(self.pad_hits.values()) + self.roll_hits
        self.statusVar.set('Ready · {} hits'.format(total))

        self.cycleId = self.after(20, self.loop)

    def _reconnect(self):
        try:
            wlbt.Stop()
        except Exception:
            pass
        try:
            wlbt.Disconnect()
        except Exception:
            pass
        try:
            wlbt.ConnectAny()
            wlbt.Start()
            self.statusVar.set('Reconnected — warming up…')
            for _ in range(5):
                wlbt.Trigger()
            self._update_status()
            self.cycleId = self.after(20, self.loop)
        except Exception:
            self.statusVar.set('Reconnect failed — retrying in 3 s…')
            self.cycleId = self.after(3000, self._reconnect)

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
        self._update_status()

    def _roll_hit(self):
        self.roll_hits += 1
        self.roll_lockout = ROLL_LOCKOUT_FRAMES
        self.canvas.itemconfigure(self.roll_zone_id, fill='#44ff44')
        self.after(ROLL_FLASH_MS, lambda: self.canvas.itemconfigure(
            self.roll_zone_id, fill='#111b11'))
        self.canvas.itemconfigure(self.roll_count_id, text=str(self.roll_hits))
        _play(ROLL_WAV)
        self._update_status()

    def _reset(self):
        for pid in self.pad_hits:
            self.pad_hits[pid] = 0
            self.canvas.itemconfigure(self.count_ids[pid], text='0')
        self.roll_hits = 0
        self.canvas.itemconfigure(self.roll_count_id, text='')
        self._update_status()

    def _update_status(self):
        total = sum(self.pad_hits.values()) + self.roll_hits
        self.statusVar.set('Ready · {} hits'.format(total))

    def _on_threshold_change(self, *_):
        try:
            self.threshold = self.threshVar.get()
        except tk.TclError:
            pass

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
