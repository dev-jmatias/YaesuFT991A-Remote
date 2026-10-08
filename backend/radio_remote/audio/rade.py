"""RADE V1 and RADE V2 (Radio Autoencoder) digital voice through the shared library built by scripts/build_rade.sh (rade_c + native/rade/rade_glue.c).

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
_PATH: Path | None = None                              # the library file that was loaded
_EXTRA_DIR: Path | None = None                     # the service's own folder (data dir/lib): where the web page installs the library


def set_install_dir(path: Path | str | None) -> None:
    """Where the Install RADE button of the web page puts the library. The service user can write there (it cannot write /opt/radio-remote/lib)."""
    global _EXTRA_DIR
    _EXTRA_DIR = Path(path) if path else None


def install_dir() -> Path | None:
    return _EXTRA_DIR


def loaded_path() -> str:
    return str(_PATH) if _PATH else ""


def installable() -> bool:
    """The RADE library is built for 64-bit Linux on ARM (Raspberry Pi) and on a PC (x86_64)."""
    import platform
    return platform.machine().lower() in ("aarch64", "arm64", "x86_64")


def reset(force: bool = False) -> None:
    """Forget a failed load, so a library that has just been installed is picked up without restarting the service. force=True also forgets a library that
    loaded fine, so the next look re-resolves which file to use (a newer one installed from the page wins). Modems that are already open keep the copy they have."""
    global _LIB, _ERR, _PATH
    with _LOCK:
        if _LIB is None:
            _ERR = ""
        elif force:
            _LIB, _PATH, _ERR = None, None, ""


class RadeUnavailable(Exception):
    pass


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get("RADIO_REMOTE_RADE_LIB")
    if env:
        out.append(Path(env))
    if _EXTRA_DIR is not None:
        out.append(_EXTRA_DIR / LIB_NAME)                                      # where the Install RADE button puts it (wins: it is the newer copy)
    out.append(Path("/opt/radio-remote/lib") / LIB_NAME)                       # where install_rade.sh puts it (survives updates)
    out.append(Path(__file__).resolve().parents[3] / "lib" / LIB_NAME)         # next to the program (development)
    return out


def _load():
    global _LIB, _ERR, _PATH
    with _LOCK:
        if _LIB is not None or _ERR:
            return _LIB
        def _readable_file(p: Path) -> bool:
            try:
                return p.is_file()
            except OSError:                                                    # for example a library folder this user may not look into
                return False

        path = next((p for p in _candidates() if _readable_file(p)), None)
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
            if hasattr(lib, "rg_open_mode"):                               # libraries built from this version on can also do RADE V2; an older one does V1 only
                lib.rg_open_mode.restype, lib.rg_open_mode.argtypes = ctypes.c_void_p, [ctypes.c_int]
            lib.rg_close.restype, lib.rg_close.argtypes = None, [ctypes.c_void_p]
            lib.rg_rx.restype, lib.rg_rx.argtypes = ctypes.c_int, [ctypes.c_void_p, P16, ctypes.c_int, P16, ctypes.c_int, PI, PF]
            lib.rg_rx_max_out.restype, lib.rg_rx_max_out.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]
            lib.rg_tx.restype, lib.rg_tx.argtypes = ctypes.c_int, [ctypes.c_void_p, P16, ctypes.c_int, P16, ctypes.c_int]
            lib.rg_tx_max_out.restype, lib.rg_tx_max_out.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]
        except (OSError, AttributeError) as e:
            _ERR = f"the RADE library could not be loaded ({e}); {INSTALL_HINT}"
            return None
        _LIB, _PATH = lib, path
        log.info("RADE library loaded from %s", path)
        return lib


def available() -> tuple[bool, str]:
    return (True, "") if _load() else (False, _ERR)


def v2_available() -> tuple[bool, str]:
    """RADE V2 needs a library built from this version of Radio Remote on (an older V1-only library still works for V1)."""
    lib = _load()
    if lib is None:
        return False, _ERR
    if not hasattr(lib, "rg_open_mode"):
        return False, "the installed RADE library has no V2: reinstall it (Admin > Config > RADE > Reinstall RADE)"
    return True, ""


class _QuietStderr:
    """The RADE library prints two status lines to stderr every time a modem is opened; keep them out of the log."""

    def __enter__(self):
        try:
            self._saved = os.dup(2)
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, 2)
            os.close(fd)
        except OSError:
            self._saved = None
        return self

    def __exit__(self, *exc):
        if self._saved is not None:
            os.dup2(self._saved, 2)
            os.close(self._saved)


class RadeCore:
    """One RADE modem context (receive and transmit state). Same shape as freedv.FreeDV: rx(), tx(), sync, snr."""

    speech_rate, modem_rate, n_speech = SPEECH_RATE, MODEM_RATE, SPEECH_BLOCK

    def __init__(self, v2: bool = False):
        lib = _load()
        if lib is None:
            raise RadeUnavailable(_ERR)
        if v2 and not v2_available()[0]:
            raise RadeUnavailable(v2_available()[1])
        self.lib = lib
        with _QuietStderr():
            self.g = lib.rg_open_mode(1) if v2 else lib.rg_open()
        if not self.g:
            raise RadeUnavailable("the RADE library could not open a modem")
        self.sync, self.snr = 0, 0.0
        self.mode = "RADE2" if v2 else "RADE"
        self._block = 640 if v2 else 1920              # speech samples (16 kHz) per modem frame: V1 12 x 10 ms = 120 ms, V2 4 x 10 ms = 40 ms
        self._fed = 0

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

    def pending(self) -> int:
        """Speech samples inside the modem that have not yet been sent (a modem frame is only produced when a whole block of speech has arrived)."""
        return self._fed % self._block

    def tx(self, speech: np.ndarray) -> np.ndarray:
        """16 kHz speech (any length) -> 8 kHz modem samples (a modem frame comes out for every 120 ms of speech, the rest waits)."""
        x = np.ascontiguousarray(speech, dtype="<i2")
        self._fed += len(x)
        out = np.empty(self.lib.rg_tx_max_out(self.g, len(x)), dtype="<i2")
        n = self.lib.rg_tx(self.g, x.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(x),
                           out.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), len(out))
        return out[:n]
