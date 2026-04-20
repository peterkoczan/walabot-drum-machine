"""
generate_sounds.py — Synthesise all eight drum WAV files.
Run once before launching walabeat2_gui.py if any .wav is missing.

    python3 generate_sounds.py

No dependencies beyond the Python standard library.
"""
import wave, struct, math, random, os

RATE = 44100
OUT  = os.path.dirname(os.path.abspath(__file__))

def save(name, samples):
    samples = [max(-32767, min(32767, int(s))) for s in samples]
    path = os.path.join(OUT, name + '.wav')
    with wave.open(path, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(struct.pack('<%dh' % len(samples), *samples))
    print('wrote', path)

def mix(*tracks):
    length = max(len(t) for t in tracks)
    out = [0.0] * length
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    peak = max(abs(v) for v in out) or 1
    return [v * 32767 / peak for v in out]

def env(samples, decay):
    return [s * math.exp(-decay * i / RATE) for i, s in enumerate(samples)]

def sine(freq, dur, decay=8):
    n = int(RATE * dur)
    return env([math.sin(2 * math.pi * freq * i / RATE) * 32767 for i in range(n)], decay)

def chirp(f0, f1, dur, decay=8):
    n = int(RATE * dur)
    return env([32767 * math.sin(2 * math.pi * (
        f0 * i / RATE + (f1 - f0) / (2 * dur) * (i / RATE) ** 2))
        for i in range(n)], decay)

def noise(dur, decay=30):
    return env([32767 * (random.random() * 2 - 1) for _ in range(int(RATE * dur))], decay)

def click(dur=0.003):
    return env([32767 * (random.random() * 2 - 1) for _ in range(int(RATE * dur))], decay=400)

# ── Kick: click + pitched body + sub-bass ─────────────────────────────────────
save('kick', mix(
    click(0.004),
    chirp(150, 45, 0.35, decay=9),
    sine(55, 0.35, decay=7),
    noise(0.02, decay=80),
))

# ── Snare: crack transient + noise rattle + body tone ─────────────────────────
save('snare', mix(
    click(0.003),
    noise(0.18, decay=22),
    sine(200, 0.12, decay=18),
    sine(320, 0.08, decay=25),
))

# ── Hi-hat: tight noise burst ─────────────────────────────────────────────────
save('hh', noise(0.07, decay=70))

# ── Open hi-hat: longer shimmer ───────────────────────────────────────────────
save('open_hh', mix(
    noise(0.35, decay=12),
    sine(800, 0.25, decay=14),
))

# ── Clap: staggered noise layers (simulates many hands) ───────────────────────
def clap_layer(offset_ms):
    offset = int(RATE * offset_ms / 1000)
    return [0.0] * offset + noise(0.06, decay=90)

save('clap', mix(clap_layer(0), clap_layer(6), clap_layer(12)))

# ── Crash: long complex noise ─────────────────────────────────────────────────
save('crash', mix(
    noise(0.8, decay=6),
    sine(680, 0.6, decay=7),
    sine(1100, 0.4, decay=9),
))

# ── Tom: mid pitch drop ───────────────────────────────────────────────────────
save('tom', mix(
    click(0.003),
    chirp(200, 70, 0.30, decay=9),
    sine(80, 0.25, decay=10),
))

# ── Ride: bell ring + shimmer ─────────────────────────────────────────────────
save('ride', mix(
    sine(850, 0.5, decay=10),
    sine(1300, 0.3, decay=14),
    noise(0.05, decay=60),
))

print('All sounds generated.')
