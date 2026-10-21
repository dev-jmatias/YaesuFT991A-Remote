"""SSTV engine (receive into saved PNG files, transmit through the guard) with a fake audio service and guard, and the HTTP / WebSocket API on a mock radio."""
import asyncio
import copy
import time

import numpy as np
import pytest

from radio_remote import config
from radio_remote.audio import sstv
from radio_remote.sstv_engine import SstvEngine, NAME_RE
from test_phase8 import PW, admin_user, close_all, make_app  # noqa: F401
from test_sstv import FS, picture, psnr


class FakeAudio:
    def __init__(self):
        self.sstv_feed, self.clip, self.owner, self.holds = None, None, None, set()
        self._fd_tx = None

    async def hold_capture(self, on, who="ftx"):
        (self.holds.add if on else self.holds.discard)(who)

    def arm_playback(self, samples, owner=None):
        self.clip, self.owner = samples, owner


class FakeGuard:
    def __init__(self, max_tx_s=300.0):
        self.keyed, self.owner, self.calls, self.beats, self.max_tx_s = False, None, [], 0, max_tx_s

    async def request(self, owner, on):
        self.calls.append((owner, on))
        self.keyed, self.owner = on, (owner if on else None)

    async def heartbeat(self, owner):
        self.beats += 1


def make(tmp_path, max_tx_s=300.0):
    cfg = copy.deepcopy(config.DEFAULTS)
    ev = []
    audio, guard = FakeAudio(), FakeGuard(max_tx_s)
    eng = SstvEngine(lambda: cfg, audio, ev.append, lambda: 14230000, guard, lambda cid: cid == "c1", tmp_path)
    return eng, audio, guard, ev, cfg


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.05)


async def feed_all(eng, pcm):
    """Feed audio faster than real time, but never more than the receiver's backlog allows (it would drop frames, as it should when it cannot keep up)."""
    for i in range(0, len(pcm) - 960, 960):
        eng.feed(pcm[i:i + 960].tobytes())
        if eng._waiting > 150:
            await until(lambda: eng._waiting < 20)


async def test_receive_saves_the_picture_and_tells_the_pages(tmp_path):
    eng, audio, guard, ev, cfg = make(tmp_path)
    await eng.set_on(True)
    assert audio.sstv_feed is not None and audio.holds == {"sstv"}
    m = sstv.MODES["robot36"]
    img = picture(m.w, m.h)
    a = np.concatenate([np.zeros(24000, np.float32), sstv.encode(img, m, FS, 0.5), np.zeros(72000, np.float32)])
    pcm = (a * 30000).astype("<i2")
    await feed_all(eng, pcm)
    await until(lambda: any(e.get("k") == "done" for e in ev))
    kinds = [e["k"] for e in ev if e.get("t") == "sstv" and e["k"] != "state"]
    assert kinds[0] == "start" and kinds.count("done") == 1 and "rows" in kinds
    done = next(e for e in ev if e.get("k") == "done")
    assert done["complete"] and done["rows"] == 240 and done["image"]["mode"] == "robot36" and done["image"]["khz"] == 14230
    assert NAME_RE.fullmatch(done["image"]["name"])
    listed = eng.images()
    assert [x["name"] for x in listed] == [done["image"]["name"]]
    png = eng.image_path(done["image"]["name"]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and abs(listed[0]["t"] - time.time()) < 300
    assert eng.cur is None and eng.current_png() is None
    assert eng.image_path("../../etc/passwd") is None and not eng.delete_image("nonsense.png")
    assert eng.delete_image(done["image"]["name"]) and eng.images() == []
    await eng.set_on(False)
    assert audio.sstv_feed is None and audio.holds == set()
    del cfg


async def test_a_picture_cut_off_early_is_not_kept_but_a_longer_partial_one_is(tmp_path):
    eng, audio, guard, ev, cfg = make(tmp_path)
    await eng.set_on(True)
    m = sstv.MODES["pd50"]
    a = sstv.encode(picture(m.w, m.h), m, FS, 0.5)

    async def play(part):
        pcm = (np.concatenate([np.zeros(24000, np.float32), part]) * 30000).astype("<i2")
        await feed_all(eng, pcm)
        await asyncio.sleep(0.5)
        await eng.set_on(False)                                                       # switching off ends the picture where it is
        await eng.set_on(True)

    await play(a[: int(len(a) * 0.04)])
    assert eng.images() == []
    await play(a[: len(a) // 2])
    imgs = eng.images()
    assert len(imgs) == 1 and imgs[0]["partial"] and imgs[0]["name"].endswith("-partial.png")
    await eng.set_on(False)


async def test_transmit_encodes_the_picture_keys_ptt_and_plays_the_clip(tmp_path):
    eng, audio, guard, ev, cfg = make(tmp_path)
    m = sstv.MODES["robot36"]
    img = picture(m.w, m.h)
    with pytest.raises(ValueError, match="switch SSTV on"):
        await eng.transmit("c1", img.tobytes(), "robot36")
    await eng.set_on(True)
    with pytest.raises(ValueError, match="unknown"):
        await eng.transmit("c1", img.tobytes(), "nope")
    with pytest.raises(ValueError, match="320 x 240"):
        await eng.transmit("c1", img.tobytes()[:-3], "robot36")
    audio._fd_tx = object()
    with pytest.raises(ValueError, match="FreeDV is on"):
        await eng.transmit("c1", img.tobytes(), "robot36")
    audio._fd_tx = None
    r = await eng.transmit("c1", img.tobytes(), "robot36")
    assert guard.calls == [("c1", True)] and audio.owner == "c1" and eng.transmitting and r["mode"] == "Robot 36"
    assert abs(len(audio.clip) / 16000 - (m.seconds + 0.7)) < 0.2 and audio.clip.dtype == np.dtype("<i2")
    peak = int(np.abs(audio.clip).max())
    assert 12000 < peak < 18000                                                       # -6 dB: about half of full scale
    d = [e for e in receive_clip(audio.clip) if e["k"] == "done"]
    assert d and d[0]["complete"] and psnr(img, d[0]["rgb"]) > 22                      # what goes out is a picture the receiver can read
    await asyncio.sleep(0.6)
    assert guard.beats >= 1                                                           # the server keeps the PTT heartbeat going
    with pytest.raises(ValueError, match="already"):
        await eng.transmit("c1", img.tobytes(), "robot36")
    await eng.halt("test")
    assert not guard.keyed and audio.clip is None
    await until(lambda: not eng.transmitting)
    assert any(e.get("k") == "txdone" for e in ev)
    await eng.set_on(False)


def receive_clip(clip16):
    """What the receiver makes of a transmit clip (16 kHz, as the audio service plays it, here brought to 48 kHz)."""
    a = clip16.astype(np.float32) / 32768.0
    t = np.arange(len(a) * 3) / 3.0
    return __import__("test_sstv").receive(np.interp(t, np.arange(len(a)), a))


async def test_transmit_is_refused_before_keying_when_it_would_pass_the_time_limit(tmp_path):
    eng, audio, guard, ev, cfg = make(tmp_path, max_tx_s=120.0)
    await eng.set_on(True)
    m = sstv.MODES["pd180"]
    with pytest.raises(ValueError, match="time limit is 120 s"):
        await eng.transmit("c1", picture(m.w, m.h).tobytes(), "pd180")
    assert guard.calls == [] and audio.clip is None
    m = sstv.MODES["martin2"]
    await eng.transmit("c1", picture(m.w, m.h).tobytes(), "martin2")                   # 58 s fits
    await eng.halt("test")
    await eng.set_on(False)


async def test_the_operator_leaving_stops_the_transmission(tmp_path):
    eng, audio, guard, ev, cfg = make(tmp_path)
    eng.has_client = lambda cid: False
    await eng.set_on(True)
    m = sstv.MODES["martin2"]
    await eng.transmit("c1", picture(m.w, m.h).tobytes(), "martin2")
    await until(lambda: not eng.transmitting)
    assert guard.calls[-1] == ("c1", False) and audio.clip is None
    await eng.set_on(False)


# ---------------------------------------------------------------- the HTTP / WebSocket API
async def test_api_state_commands_and_the_transmit_upload(make_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = await make_app()
    root = await admin_user(client)
    st = await (await client.get("/api/sstv")).json()
    assert st["on"] is False and st["images"] == [] and {m["key"] for m in st["modes"]} >= {"robot36", "martin1", "pd120"} and st["bands"][2] == {"name": "20m", "hz": 14230000, "mode": "USB"} \
        and st["bands"][-1] == {"name": "70cm ISS", "hz": 437550000, "mode": "FM"}
    ws, hello = await root.ws()
    assert hello["audio"]["sstv"]["available"] is True and hello["audio"]["sstv"]["on"] is False
    conn = hello["conn"]
    m = sstv.MODES["martin2"]
    body = picture(m.w, m.h).tobytes()
    r = await client.post(f"/api/sstv/tx?mode=martin2&conn={conn}", data=body, headers=root.h)
    assert r.status == 409 and "switch SSTV on" in await r.text()                      # a body well over the 16 kB the other requests are limited to is read
    ack = await root.cmd(ws, 1, type="sstv", on=True)
    assert ack["ok"], ack
    assert (await (await client.get("/api/sstv")).json())["on"] is True
    r = await client.post(f"/api/sstv/tx?mode=martin2&conn={conn}", data=body[:-3], headers=root.h)
    assert r.status == 409 and "320 x 256" in await r.text()
    ws2, hello2 = await root.ws()                                                       # a second page of the same user has no control
    r = await client.post(f"/api/sstv/tx?mode=martin2&conn={hello2['conn']}", data=body, headers=root.h)
    assert r.status == 403
    r = await client.post("/api/sstv/tx?mode=martin2&conn=nonsense", data=body, headers=root.h)
    assert r.status == 403
    r = await client.post(f"/api/sstv/tx?mode=martin2&conn={conn}", data=b"x" * 2_000_000, headers=root.h)
    assert r.status == 413
    r = await client.post(f"/api/sstv/tx?mode=martin2&conn={conn}", data=body, headers=root.h)
    assert r.status == 200, await r.text()
    assert (await r.json())["mode"] == "Martin M2"
    await asyncio.sleep(0.5)
    st = await (await client.get("/api/sstv")).json()
    assert st["transmitting"] is True and st["tx"]["mode"] == "martin2"
    ack = await root.cmd(ws, 2, type="sstv_halt")
    assert ack["ok"]
    await until_api(client, lambda s: not s["transmitting"])
    ack = await root.cmd(ws, 3, type="sstv", on=False)
    assert ack["ok"] and (await (await client.get("/api/sstv")).json())["on"] is False
    ack = await root.cmd(ws, 4, type="sstv", on="yes")
    assert not ack["ok"]
    assert (await client.get("/api/sstv/current.png")).status == 404
    assert (await client.get("/api/sstv/images/rx-20260101-000000-robot36-14230.png")).status == 404
    assert (await client.post("/api/sstv/images/rx-20260101-000000-robot36-14230.png/delete", json={}, headers=root.h)).status == 404
    await close_all(root)


async def until_api(client, cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond(await (await client.get("/api/sstv")).json()):
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not met")


async def test_viewers_cannot_transmit_or_delete_pictures(make_app, tmp_path, monkeypatch):
    from test_phase8 import add_user, login_as
    monkeypatch.chdir(tmp_path)
    client = await make_app()
    root = await admin_user(client)
    await add_user(root, "watcher", "viewer")
    v = await login_as(root, "watcher")
    assert (await v.c.get("/api/sstv")).status == 200
    assert (await v.c.post("/api/sstv/tx?mode=martin2&conn=x", data=b"x", headers=v.h)).status == 403
    assert (await v.c.post("/api/sstv/images/rx-20260101-000000-robot36-14230.png/delete", json={}, headers=v.h)).status == 403
    await close_all(root, v)


async def test_sstv_settings_are_editable_and_validated(make_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = await make_app()
    root = await admin_user(client)
    r = await client.put("/api/config", json={"sstv": {"tx_level_db": -10, "keep": 20}}, headers=root.h)
    assert r.status == 200, await r.text()
    assert (await (await client.get("/api/sstv")).json())["settings"]["tx_level_db"] == -10
    for body in ({"sstv": {"tx_level_db": 3}}, {"sstv": {"keep": 1}}, {"sstv": {"keep": 9999}}):
        assert (await client.put("/api/config", json=body, headers=root.h)).status == 400, body
    await close_all(root)
