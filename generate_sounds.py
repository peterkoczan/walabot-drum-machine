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

def sine(freq, dur, amp=32767, decay=8):
    n = int(RATE * dur)
    return [amp * math.exp(-decay * i / RATE) * math.sin(2 * math.pi * freq * i / RATE)
            for i in range(n)]

def chirp(f0, f1, dur, amp=32767, decay=8):
    n = int(RATE * dur)
    return [amp * math.exp(-decay * i / RATE) *
            math.sin(2 * math.pi * (f0 * i / RATE + (f1 - f0) / (2 * dur) * (i / RATE) ** 2))
            for i in range(n)]

def noise(dur, amp=32767, decay=30):
    n = int(RATE * dur)
    return [amp * math.exp(-decay * i / RATE) * (random.random() * 2 - 1) for i in range(n)]

def mix(*tracks):
    length = max(len(t) for t in tracks)
    out = [0] * length
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    peak = max(abs(v) for v in out) or 1
    scale = 32767 / peak
    return [v * scale for v in out]

# Hi-hat: short high-frequency noise burst
save('hh',      noise(0.08, decay=60))

# Snare: noise + low sine body
save('snare',   mix(noise(0.15, amp=24000, decay=25),
                    sine(180, 0.15, amp=16000, decay=20)))

# Kick: low chirp dropping in pitch
save('kick',    chirp(160, 50, 0.35, decay=10))

# Clap: very short layered noise burst
save('clap',    mix(noise(0.06, amp=28000, decay=80),
                    noise(0.06, amp=20000, decay=120)))

# Crash: long noise with slow decay
save('crash',   noise(0.6, amp=30000, decay=8))

# Tom: mid-frequency pitch drop
save('tom',     chirp(220, 80, 0.30, decay=9))

# Ride: medium-length high sine (bell-like)
save('ride',    sine(900, 0.40, amp=28000, decay=12))

# Open hi-hat: longer noise than closed hh
save('open_hh', noise(0.30, amp=28000, decay=15))

print('All sounds generated.')
