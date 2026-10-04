"""RADE V1 (Radio Autoencoder) digital voice through the shared library built by scripts/build_rade.sh (rade_c + native/rade/rade_glue.c).

The library is optional and is NOT part of the normal install: it is a separate download (see scripts/install_rade.sh). Without it the
FreeDV tab simply does not offer RADE. Interface: the same "core" shape as the codec2 modes in freedv.py (rx / tx on int16 arrays), but the
speech runs at 16 kHz and the modem at 8 kHz (real audio, no IQ needed: the OFDM carriers sit at about 1.0-1.9 kHz).
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("rade")

ABI_VERSION = 1
MODEM_RATE = 8000
SPEECH_RATE = 16000
SPEECH_BLOCK = 160                                # 10 ms of speech: the unit the transmit side works in
LIB_NAME = "librade-rr.so"
INSTALL_HINT = "run: sudo /opt/radio-remote/current/scripts/install_rade.sh"

_LIB = None
_ERR = ""
_LOCK = threading.Lock()


class RadeUnavailable(Exception):
    pass


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get("RADIO_REMOTE_RADE_LIB")
    if env:
        out.append(Path(env))
    out.append(Path("/opt/radio-remote/lib") / LIB_NAME)                       # where install_rade.sh puts it (survives updates)
    out.append(Path(__file__).resolve().parents[3] / "lib" / LIB_NAME)         # next to the program (development)
    return out


def _load():
    global _LIB, _ERR
    with _LOCK:
        if _LIB is not None or _ERR:
            return _LIB
        path = next((p for p in _candidates() if p.is_file()), None)
        if path is None:
            _ERR = f"RADE is not installed ({INSTALL_HINT})"
            return None
        try:
            lib = ctypes.CDLL(str(path))
            lib.rg_abi_version.restype = ctypes.c_int
            if lib.rg_abi_version() != ABI_VERSION:
                _ERR = f"the installed RADE library is for another version of Radio Remote ({INSTALL_HINT})"
                return None
            P16, PI, PF = ctypes.POINTER(ctypes.c_int16), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_float)
            lib.rg_open.restype, lib.rg_open.argtypes = ctypes.c_void_p, []
            lib.rg_close.restype, lib.rg_close.argtypes = None, [ctypes.c_void_p]
            lib.rg_rx.restype, lib.rg_rx.argtypes = ctypes.c_int, [ctypes.c_void_p, P16, ctypes.c_int, P16, ctypes.c_int, PI, PF]
            lib.rg_rx_max_out.restype, lib.rg_rx_max_out.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]
            lib.rg_tx.restype, lib.rg_tx.argtypes = ctypes.c_int, [ctypes.c_void_p, P16, ctypes.c_int, P16, ctypes.c_int]
            lib.rg_tx_max_out.restype, lib.rg_tx_max_out.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]
        except (OSError, AttributeError) as e:
            _ERR = f"the RADE library could not be loaded ({e}); {INSTALL_HINT}"
            return None
        _LIB = lib
        log.info("RADE library loaded from %s", path)
        return lib


def available() -> tuple[bool, str]:
    return (True, "") if _load() else (False, _ERR)


class RadeCore:
    """One RADE modem context (receive and transmit state). Same shape as freedv.FreeDV: rx(), tx(), sync, snr."""

    speech_rate, modem_rate, n_speech = SPEECH_RATE, MODEM_RATE, SPEECH_BLOCK

    def __init__(self):
        lib = _load()
        if lib is None:
            raise RadeUnavailable(_ERR)
        self.lib = lib
        self.g = lib.rg_open()
        if not self.g:
            raise RadeUnavailable("the RADE library could not open a modem")
        self.sync, self.snr = 0, 0.0
        self.mode = "RADE"

    def close(self) -> None:
        if self.g:
            self.lib.rg_close(self.g)
            self.g = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def rx(self, modem: np.ndarray) -> np.ndarray:
        """8 kHz modem samples -> decoded 16 kHz speech (empty until a whole OFDM symbol group has been decoded)."""
        x = np.ascontiguousarray(modem, dtype="<i2")
        out = np.empty(self.lib.rg_rx_max_out(self.g, len(x)), dtype="<i2")
        sync, snr = ctypes.c_int(), ctypes.c_float()
        n = self.lib.rg_rx(self.g, x.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(x),
                           out.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(out), ctypes.byref(sync), ctypes.byref(snr))
        self.sync, self.snr = int(sync.value), float(snr.value)
        return out[:n]

    def tx(self, speech: np.ndarray) -> np.ndarray:
        """16 kHz speech (any length) -> 8 kHz modem samples (a modem frame comes out for every 120 ms of speech, the rest waits)."""
        x = np.ascontiguousarray(speech, dtype="<i2")
        out = np.empty(self.lib.rg_tx_max_out(self.g, len(x)), dtype="<i2")
        n = self.lib.rg_tx(self.g, x.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(x),
                           out.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(out))
        return out[:n]
