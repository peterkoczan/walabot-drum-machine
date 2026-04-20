# Walabot Drum Machine

An 8-pad radar drum machine powered by the [Walabot](https://walabot.com) sensor. Wave your hands in front of the sensor to hit drums — no contact required.

![8-pad layout](https://raw.githubusercontent.com/peterkoczan/walabot-drum-machine/main/layout.png)

## Pad layout

```
         LEFT  ←————————————————→  RIGHT
FAR    [Crash] [  Tom ] [ Ride ] [Open HH]
NEAR   [Hi-Hat] [ Kick ] [Snare] [  Clap ]
              ↑ sensor faces you ↑
```

The sensor splits the space in front of it into a 4×2 grid. Each zone has a dead zone gap around it so adjacent pads don't bleed into each other.

## Requirements

- **Hardware**: [Walabot Makers Series](https://walabot.com) USB sensor
- **OS**: Linux x86_64 (Ubuntu / Debian recommended)
- **Python**: 3.8+
- **Walabot SDK**: [download from Vayyar](https://api.walabot.com/_download.html) and install the `.deb`

Python packages (all standard or pip-installable):

```bash
pip install WalabotAPI
```

`tkinter` must be available — on Ubuntu: `sudo apt-get install python3-tk`

## Installation

```bash
git clone https://github.com/peterkoczan/walabot-drum-machine.git
cd walabot-drum-machine

# Generate the drum sounds (standard library only, no dependencies)
python3 generate_sounds.py
```

## Running

Plug in the Walabot, then:

```bash
python3 walabeat2_gui.py
```

> **Permissions**: if the sensor is not found, add your user to the `plugdev` group and reload udev rules, or run with `sudo` once to verify hardware access.

## How it works

- Uses `PROF_SENSOR` + `GetRawImageSlice()` + `FILTER_TYPE_MTI` (motion filter)
- Arena: R 15–60 cm, Phi ±60°, Theta ±1°
- The 2D image slice is divided into 4 azimuth zones × 2 depth zones = 8 pads
- Dead zones of 1 data bin between each pair of adjacent zones reduce cross-talk
- Energy in each zone is compared to a threshold; crossing it triggers the pad
- A sustained trigger in the near outer-right zone fires the snare roll

## Tweaking

At the top of `walabeat2_gui.py`:

| Constant | Default | Effect |
|---|---|---|
| `ENERGY_THRESHOLD` | 300 | Sensitivity — lower = more sensitive |
| `BAR_MAX` | 1500 | Glow scale |
| `DEAD_R_PX` | 20 | Visual gap at near/far radial boundary |
| `DEAD_PHI_DEG` | 4 | Visual angular gap between phi sectors |
| `FLASH_MS` | 140 | How long a pad stays lit after a hit |
| `R_MIN / R_MAX` | 15 / 60 cm | Detection depth range |

## Running on macOS (Apple Silicon) or Windows

The Walabot SDK only ships for Linux x86_64 and Linux ARMhf. On macOS or Windows you need a Linux VM with USB passthrough. See the [Walabot community forums](https://walabot.com/community) for setup guidance.

## License

MIT — free to use, modify, and share.
