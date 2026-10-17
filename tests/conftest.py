import copy
import os

import pytest
from aiohttp.test_utils import TestClient, TestServer

# Safety: tests must never be able to open real hardware.
os.environ["RADIO_REMOTE_TESTING"] = "1"

from radio_remote import config
from radio_remote.app import create_app
from radio_remote.auth import AuthStore
from radio_remote.radio.mock import MockDriver


@pytest.fixture
def cfg():
    return copy.deepcopy(config.DEFAULTS)


@pytest.fixture
async def client(cfg):
    driver = MockDriver(seed=1)
    app = create_app(cfg, driver=driver, auth=AuthStore(":memory:"))
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c
    await c.close()


async def setup_admin(client, user="admin", pw="correct horse battery"):
    r = await client.post("/api/setup", json={"username": user, "password": pw})
    assert r.status == 200, await r.text()
    return (await r.json())["csrf"]


# ---- FT8 / FT4: build the codec library once per run (needs a C compiler; the tests are skipped without one)
import subprocess
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def ftx(tmp_path_factory):
    out = tmp_path_factory.mktemp("ftx")
    r = subprocess.run(["bash", str(_ROOT / "scripts" / "build_ftx.sh"), str(out)], capture_output=True, text=True)
    if r.returncode:
        pytest.skip("no C compiler: " + r.stderr[-200:])
    ext = "dylib" if sys.platform == "darwin" else "so"
    os.environ["RADIO_REMOTE_FTX_LIB"] = str(out / f"libftx-rr.{ext}")
    from radio_remote.audio import ftx as mod
    mod._tried, mod._lib = False, None
    ok, why = mod.available()
    assert ok, why
    return mod


def ftx_slot(mod, mode, items, snr_db=None, seed=1):
    """A slot of 12 kHz audio holding the given (text, freq) transmissions starting at 0.5 s, plus white noise."""
    rate = 12000
    n = int(mod.MODES[mode]["slot"] * rate)
    x = np.zeros(n)
    for text, f in items:
        s, _ = mod.encode(text, mode, f, rate)
        o = int(0.5 * rate)
        x[o:o + len(s)] += s
    rng = np.random.default_rng(seed)
    if snr_db is not None:
        sigma = np.sqrt(0.5 / 10 ** (snr_db / 10) * (rate / 2) / 2500)       # noise power in 2500 Hz so that a unit-amplitude (power 0.5) signal has the wanted SNR
        x += rng.normal(0, sigma, n)
    return (x / max(1.0, np.abs(x).max() / 0.9)).astype(np.float32)
