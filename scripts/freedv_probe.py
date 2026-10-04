#!/usr/bin/env python3
"""How hard does FreeDV work this machine? Runs each available mode (codec2 and RADE) as a transmit -> receive loopback on 20 s of synthetic
speech and prints the CPU time as a percentage of real time (the share of ONE core the server needs to keep up).

On the Pi:   /opt/radio-remote/venv/bin/python /opt/radio-remote/current/scripts/freedv_probe.py
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from radio_remote.audio import freedv  # noqa: E402
from radio_remote.audio.levels import FRAME_SAMPLES, RATE  # noqa: E402

SECONDS = 20


def speech(n_frames):
    t = np.arange(n_frames * FRAME_SAMPLES) / RATE
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    x = (np.sin(2 * np.pi * 150 * t) + 0.7 * np.sin(2 * np.pi * 450 * t) + 0.5 * np.sin(2 * np.pi * 1100 * t)) * env
    x += 0.05 * np.random.default_rng(1).standard_normal(len(t))
    return (x / np.abs(x).max() * 12000).astype("<i2")


def main() -> int:
    status = freedv.mode_status()
    for mode in freedv.ALL_MODES:
        if status.get(mode):
            print(f"{mode:5s} not available: {status[mode]}")
            continue
        n = SECONDS * 50
        mic = speech(n)
        tx, rx = freedv.TxChain(mode, 0.5), freedv.RxChain(mode)
        t0 = time.process_time()
        tones = []
        for i in range(n):
            tones += tx.process(mic[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES].tobytes())
        t_tx = time.process_time() - t0
        t0 = time.process_time()
        synced = False
        for f in tones:
            rx.process(f)
            synced = synced or rx.sync == 1
        t_rx = time.process_time() - t0
        print(f"{mode:5s} transmit {100 * t_tx / SECONDS:5.1f}%   receive {100 * t_rx / SECONDS:5.1f}%   of one core   (loopback locked: {'yes' if synced else 'NO'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
