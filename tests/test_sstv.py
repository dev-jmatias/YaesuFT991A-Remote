"""SSTV codec (audio/sstv.py): the mode tables, encoder -> receiver round trips, and the receiver's behaviour on noise, clock error, junk, back-to-back pictures and cut-off signals."""
import struct
import zlib

import numpy as np
import pytest

from radio_remote.audio import sstv

FS = 48000


def picture(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(float)
    img = np.zeros((h, w, 3))
    img[..., 0] = 255 * (x / w)
    img[..., 1] = 255 * (y / h)
    img[..., 2] = 128 + 100 * np.sin(x / 23.0) * np.cos(y / 17.0)
    r = np.hypot(x - w * 0.5, y - h * 0.5)
    img[r < h * 0.25] = [230, 220, 40]
    img[(r >= h * 0.25) & (r < h * 0.29)] = [20, 20, 200]
    return np.clip(img, 0, 255).astype(np.uint8)


def psnr(a, b):
    mse = np.mean((a.astype(float) - b.astype(float)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def receive(audio, rx=None, pad_before=24000, pad_after=72000, step=960):
    rx = rx or sstv.SstvRx()
    a = np.concatenate([np.zeros(pad_before, np.float32), np.asarray(audio, np.float32), np.zeros(pad_after, np.float32)])
    pcm = (np.clip(a, -1, 1) * 30000).astype("<i2")
    events, t = [], 0.0
    for i in range(0, len(pcm) - step, step):
        t += step / FS
        events += rx.feed(pcm[i:i + step].tobytes(), t)
    return events


def done(events):
    return [e for e in events if e["k"] == "done"]


def test_mode_tables_match_the_published_transmission_times():
    published = {"robot36": 36, "robot72": 72, "martin1": 114, "martin2": 58, "scottie1": 110, "scottie2": 71, "scottiedx": 269, "pd50": 50, "pd90": 90, "pd120": 126, "pd160": 161, "pd180": 187, "pd240": 248}
    for key, secs in published.items():
        assert abs(sstv.MODES[key].seconds - secs) < 2.0, (key, sstv.MODES[key].seconds)
    assert len({m.vis for m in sstv.MODES.values()}) == len(sstv.MODES)
    assert {m.key: m.vis for m in sstv.MODES.values()}["martin1"] == 44 and sstv.BY_VIS[96].key == "pd180"
    assert all(m["seconds"] > 0 for m in sstv.mode_list())


def test_encoder_refuses_a_picture_of_the_wrong_size():
    with pytest.raises(ValueError, match="320 x 240"):
        sstv.encode(np.zeros((100, 100, 3), np.uint8), "robot36", 16000)


@pytest.mark.parametrize("key,minimum", [("robot36", 24), ("robot72", 28), ("martin2", 30), ("scottie2", 30), ("pd50", 28), ("pd120", 30)])
def test_round_trip_through_the_receiver(key, minimum):
    m = sstv.MODES[key]
    img = picture(m.w, m.h)
    ev = receive(sstv.encode(img, m, FS, 0.5))
    assert [e["mode"] for e in ev if e["k"] == "start"] == [key]
    d = done(ev)
    assert len(d) == 1 and d[0]["complete"] and d[0]["rows"] == m.h
    assert psnr(img, d[0]["rgb"]) > minimum
    rows = [e for e in ev if e["k"] == "rows"]
    assert sum(len(e["rgb"]) // (m.w * 3) for e in rows) >= m.h - 2                       # the rows were also handed out as they arrived


def test_a_low_sample_rate_encoding_is_what_the_transmitter_uses():
    """The transmit clip is made at 16 kHz and the audio service upsamples it to 48 kHz: the receiver must still read such a signal."""
    m = sstv.MODES["martin2"]
    img = picture(m.w, m.h)
    a16 = sstv.encode(img, m, 16000, 0.5)
    t48 = np.arange(len(a16) * 3) / 3.0
    a48 = np.interp(t48, np.arange(len(a16)), a16)
    d = done(receive(a48))
    assert d and d[0]["complete"] and psnr(img, d[0]["rgb"]) > 28


def test_noise_clock_error_and_junk_before_the_picture():
    rng = np.random.default_rng(3)
    m = sstv.MODES["martin2"]
    img = picture(m.w, m.h)
    a = sstv.encode(img, m, FS, 0.5)
    sigma = np.sqrt(np.mean(a ** 2) / 10 ** (20 / 10) * (24000 / 3000))                   # 20 dB signal to noise in 3 kHz
    d = done(receive(a + rng.normal(0, sigma, len(a))))
    assert d and d[0]["complete"] and psnr(img, d[0]["rgb"]) > 17
    for ppm in (-600, 600):                                                                # a sound card clock that is slow or fast: every line is realigned
        n = len(a)
        t = np.arange(int(n / (1 + ppm * 1e-6))) * (1 + ppm * 1e-6)
        d = done(receive(np.interp(t, np.arange(n), a)))
        assert d and d[0]["complete"] and psnr(img, d[0]["rgb"]) > 28, ppm
    junk = np.concatenate([rng.normal(0, 0.05, 96000), 0.3 * np.sin(2 * np.pi * 1000 * np.arange(48000) / FS)])
    d = done(receive(np.concatenate([junk, a])))
    assert d and d[0]["complete"]


def test_two_pictures_in_a_row_and_a_picture_that_is_cut_off():
    m1, m2 = sstv.MODES["robot36"], sstv.MODES["martin2"]
    i1, i2 = picture(m1.w, m1.h), picture(m2.w, m2.h)
    a1, a2 = sstv.encode(i1, m1, FS, 0.5), sstv.encode(i2, m2, FS, 0.5)
    ev = receive(np.concatenate([a1, np.zeros(20000), a2]))
    assert [(e["mode"], e["complete"]) for e in done(ev)] == [("robot36", True), ("martin2", True)]
    ev = receive(a1[: len(a1) // 2], pad_after=150000)                                     # the signal stops half way, then silence: the picture ends where it is
    d = done(ev)
    assert len(d) == 1 and not d[0]["complete"] and 100 < d[0]["rows"] < 140


def test_a_header_for_a_mode_this_program_does_not_know_is_reported_and_ignored():
    m = sstv.MODES["martin2"]
    odd = sstv.Mode("odd", "Odd", 94, m.w, m.h, m.family, m.segs)                           # VIS 94 is PD290: a real mode that is not offered here
    ev = receive(sstv.encode(picture(m.w, m.h), odd, FS, 0.5))
    assert [e["code"] for e in ev if e["k"] == "vis"] == [94] and not [e for e in ev if e["k"] == "start"]


def test_flush_ends_a_picture_in_progress():
    m = sstv.MODES["pd50"]
    a = sstv.encode(picture(m.w, m.h), m, FS, 0.5)
    rx = sstv.SstvRx()
    ev = receive(a[: len(a) // 3], rx=rx, pad_after=0)
    assert [e for e in ev if e["k"] == "start"] and not done(ev)
    f = rx.flush()
    assert len(f) == 1 and f[0]["k"] == "done" and not f[0]["complete"] and f[0]["rows"] > 20
    assert rx.flush() == []


def test_png_bytes_is_a_valid_png():
    img = picture(40, 30)
    data = sstv.png_bytes(img)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        assert zlib.crc32(tag + body) & 0xFFFFFFFF == struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        if tag == b"IDAT":
            idat += body
        pos += 12 + n
    assert ihdr == (40, 30, 8, 2, 0, 0, 0)
    raw = zlib.decompress(idat)
    rows = [np.frombuffer(raw[y * 121 + 1:(y + 1) * 121], np.uint8).reshape(40, 3) for y in range(30)]
    assert np.array_equal(np.stack(rows), img)


def _shifted(a, hz):
    """The audio as a radio tuned `hz` off would deliver it: every tone moved by that many Hz."""
    n = len(a)
    h = np.zeros(n)
    h[0], h[1:n // 2], h[n // 2] = 1, 2, 1
    return (np.fft.ifft(np.fft.fft(a) * h) * np.exp(2j * np.pi * hz * np.arange(n) / FS)).real.astype(np.float32)


@pytest.mark.parametrize("key", ["martin2", "robot36"])
def test_a_radio_tuned_a_little_off_gives_the_same_picture(key):
    """SSB is never tuned exactly: the leader of the header (1900 Hz) shows how far off it is and every tone is corrected by that."""
    m = sstv.MODES[key]
    img = picture(m.w, m.h)
    a = sstv.encode(img, m, FS, 0.5)
    base = psnr(img, done(receive(a))[0]["rgb"])
    for hz in (-120, -45, 30, 80):
        d = done(receive(_shifted(a, hz)))
        assert d and d[0]["complete"], hz
        assert psnr(img, d[0]["rgb"]) > base - 2.0, (hz, psnr(img, d[0]["rgb"]), base)
