# Walabot Drum Machine

An 8-pad radar drum machine powered by the [Walabot](https://walabot.com) sensor. Wave your hands in front of the sensor to hit drums — no contact required.

## Pad layout

```
         LEFT  ←————————————————→  RIGHT
FAR    [Crash] [  Tom ] [ Ride ] [Open HH]
NEAR   [Hi-Hat] [ Kick ] [Snare] [  Clap ]
              ↑ sensor faces you ↑
```

The sensor splits the space in front of it into a 4 azimuth × 2 depth grid. Dead zone gaps between each pad prevent adjacent zones from bleeding into each other.

## Requirements

### Hardware
- [Walabot Makers Series](https://walabot.com) USB sensor

### Walabot SDK
The SDK is only available for **Linux x86_64** and **Linux ARMhf**. Download and install the `.deb` from [walabot.com](https://walabot.com):

```bash
sudo dpkg -i walabot_maker_<version>_linux_x64.deb
pip install WalabotAPI
```

> **macOS / Windows**: the SDK does not run natively. You need a Linux VM with USB passthrough (e.g. UTM on macOS). Audio and the GUI code itself are cross-platform.

### Python
Python 3.8+ with `tkinter`:

```bash
# Ubuntu / Debian
sudo apt-get install python3-tk
```

No other Python packages are required.

## Installation

```bash
git clone https://github.com/peterkoczan/walabot-drum-machine.git
cd walabot-drum-machine

# Generate drum sounds (pure Python standard library — no external tools needed)
python3 generate_sounds.py
```

## Running

```bash
python3 walabeat2_gui.py
```

> If the sensor is not found, add your user to the `plugdev` group and reload udev rules — or run with `sudo` once to verify hardware access.

## Controls

| Control | Action |
|---|---|
| Wave hand in a sector | Hit that drum |
| Wave rapidly near-right | Snare roll |
| Threshold slider | Tune sensitivity live (lower = triggers on lighter touches) |
| RESET button | Zero all hit counters |

## Audio

Playback uses the platform's built-in audio command — no extra packages needed:

| Platform | Command used |
|---|---|
| Linux | `aplay` (ALSA, pre-installed) |
| macOS | `afplay` (pre-installed) |
| Windows | `winsound` (Python standard library) |

## Tweaking

At the top of `walabeat2_gui.py`:

| Constant | Default | Effect |
|---|---|---|
| `ENERGY_THRESHOLD` | 300 | Starting threshold (also adjustable via slider) |
| `BAR_MAX` | 1500 | Energy level that pegs the glow at full brightness |
| `DEAD_R_PX` | 20 | Visual gap at the near/far radial boundary |
| `DEAD_PHI_DEG` | 4 | Visual angular gap between phi sectors |
| `FLASH_MS` | 140 | How long a pad stays lit after a hit |
| `R_MIN / R_MAX` | 15 / 60 cm | Detection depth range |

## License

MIT — free to use, modify, and share.
