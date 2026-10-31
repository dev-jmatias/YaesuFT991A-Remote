import asyncio
import copy
import struct

import pytest
from test_phase8 import make_app  # noqa: F401

from radio_remote import config
from radio_remote import pskreporter as psk
from radio_remote.common import K_AUDIO, K_CFG, K_PSK

T0 = 1_700_000_000.0


def cfg_with(**over):
    c = copy.deepcopy(config.DEFAULTS)
    c["reporter"].update({"psk": True, "callsign": "g4abc", "grid_square": "io91wm", **over})
    return c


def make(clock, sent, **over):
    c = cfg_with(**over)
    r = psk.PskReporter(lambda: c, "9.9.9", clock=lambda: clock["t"], send=sent.append)
    r.cfg = c
    return r


# ------------------------------------------------------------ an independent reader of the datagram, written from the public description
def parse(data: bytes) -> dict:
    ver, length, export, seq, domain = struct.unpack(">HHIII", data[:16])
    assert ver == 10 and length == len(data)
    out = {"export": export, "seq": seq, "domain": domain, "sets": []}
    pos = 16
    while pos < len(data):
        set_id, n = struct.unpack(">HH", data[pos:pos + 4])
        assert n % 4 == 0 and n >= 4 and pos + n <= len(data), "a set's length covers its header and padding and is a multiple of 4"
        out["sets"].append((set_id, data[pos + 4:pos + n]))
        pos += n
    return out


def vstr(b: bytes, i: int):
    n = b[i]
    return b[i + 1:i + 1 + n].decode(), i + 1 + n


def template_fields(body: bytes, scope: bool):
    tid, count = struct.unpack(">HH", body[:4])
    i = 4 + (2 if scope else 0)
    fields = []
    for _ in range(count):
        fid, flen = struct.unpack(">HH", body[i:i + 4])
        i += 4
        ent = 0
        if fid & 0x8000:
            ent = struct.unpack(">I", body[i:i + 4])[0]
            i += 4
        fields.append((fid & 0x7FFF, flen, ent))
    return tid, fields


def spots(parsed) -> list[tuple]:
    body = next(b for sid, b in parsed["sets"] if sid == psk.SENDER_TEMPLATE)
    rows, i = [], 0
    while i < len(body):
        if body[i:] == b"\0" * (len(body) - i) and len(body) - i < 4:
            break                                                   # the zero padding at the end of the set
        call, i = vstr(body, i)
        hz = struct.unpack(">I", body[i:i + 4])[0]
        snr = struct.unpack(">b", body[i + 4:i + 5])[0]
        mode, j = vstr(body, i + 5)
        src = body[j]
        t = struct.unpack(">I", body[j + 1:j + 5])[0]
        rows.append((call, hz, snr, mode, src, t))
        i = j + 5
    return rows


def test_the_receiver_template_is_byte_for_byte_the_one_in_the_description():
    expected = bytes.fromhex("00030024" "9992" "0003" "0000" "8002ffff0000768f" "8004ffff0000768f" "8008ffff0000768f" "0000")
    assert psk.receiver_template() == expected


def test_the_sender_template_declares_the_fields_we_send_in_order():
    tmpl = psk.sender_template()
    set_id, n = struct.unpack(">HH", tmpl[:4])
    assert set_id == 2 and n == len(tmpl) and n % 4 == 0
    tid, fields = template_fields(tmpl[4:], scope=False)
    assert tid == psk.SENDER_TEMPLATE
    assert fields == [(1, 0xFFFF, 30351), (5, 4, 30351), (6, 1, 30351), (10, 0xFFFF, 30351), (11, 1, 30351), (150, 4, 0)]       # callsign, frequency, SNR, mode, source, flowStartSeconds


def test_a_datagram_has_the_header_the_templates_the_receiver_and_the_spots():
    d = psk.datagram("g4abc", "io91wm", "Radio Remote 9.9.9", [("CT2IOK", 14_236_000, 6.4, 1_700_000_000), ("EA1ABC", 7_050_000, -3.6, 1_700_000_050)], 7, 0xCAFE, True, 1_700_000_100)
    p = parse(d)
    assert p["export"] == 1_700_000_100 and p["seq"] == 7 and p["domain"] == 0xCAFE
    assert [s for s, _ in p["sets"]] == [3, 2, psk.RECEIVER_TEMPLATE, psk.SENDER_TEMPLATE]          # templates first, then the receiver record, then the spots
    rec = dict(p["sets"])[psk.RECEIVER_TEMPLATE]
    call, i = vstr(rec, 0)
    loc, i = vstr(rec, i)
    soft, i = vstr(rec, i)
    assert (call, loc, soft) == ("G4ABC", "IO91wm", "Radio Remote 9.9.9")
    assert rec[i:] == b"\0" * (len(rec) - i)
    assert spots(p) == [("CT2IOK", 14_236_000, 6, "FREEDV", 1, 1_700_000_000), ("EA1ABC", 7_050_000, -4, "FREEDV", 1, 1_700_000_050)]


def test_without_templates_and_without_spots_the_datagram_is_smaller_and_a_receiver_only_one_is_valid():
    with_t = psk.datagram("G4ABC", "IO91", "x", [("CT2IOK", 14_236_000, 6, 1)], 0, 1, True, 5)
    without = psk.datagram("G4ABC", "IO91", "x", [("CT2IOK", 14_236_000, 6, 1)], 0, 1, False, 5)
    assert len(with_t) - len(without) == len(psk.receiver_template()) + len(psk.sender_template())
    only = parse(psk.datagram("G4ABC", "IO91wm", "x", [], 0, 1, False, 5))                     # the "locator update": a receiver record alone
    assert [s for s, _ in only["sets"]] == [psk.RECEIVER_TEMPLATE]


def test_the_snr_is_a_signed_byte_that_cannot_overflow():
    d = parse(psk.datagram("G4ABC", "IO91", "x", [("A1A", 7_000_000, 300, 1), ("B1B", 7_000_000, -300, 1)], 0, 1, False, 5))
    assert [r[2] for r in spots(d)] == [127, -128]


# ------------------------------------------------------------ what is collected and when it goes out
def test_nothing_is_collected_unless_switched_on_with_a_callsign_and_a_grid_square():
    clock, sent = {"t": T0}, []
    assert "switched off" in make(clock, sent, psk=False).add("CT2IOK", 6, 14_236_000)
    assert "callsign and grid" in make(clock, sent, grid_square="").add("CT2IOK", 6, 14_236_000)
    assert "callsign and grid" in make(clock, sent, callsign="").add("CT2IOK", 6, 14_236_000)
    r = make(clock, sent)
    assert r.add("CT2IOK", 6.4, 14_236_000) == "" and list(r.pending) == ["CT2IOK"]
    for bad in (("", 6, 14_236_000), ("CT2IOK", None, 14_236_000), ("CT2IOK", float("nan"), 14_236_000), ("CT2IOK", True, 14_236_000), ("CT2IOK", 6, 0), ("AB", 6, 14_236_000),
                ("CT2 IOK", 6, 14_236_000)):
        assert r.add(*bad) != "", bad
    assert list(r.pending) == ["CT2IOK"]


async def test_a_batch_goes_out_after_five_minutes_with_the_templates_in_the_first_three_datagrams_only():
    clock, sent = {"t": T0}, []
    r = make(clock, sent)
    r.last_at = T0
    r.add("CT2IOK", 6.4, 14_236_000)
    assert await r.flush() == 0 and sent == []                      # not yet: at most one datagram every five minutes
    clock["t"] = T0 + 301
    assert await r.flush() == 1 and len(sent) == 1
    assert [s for s, _ in parse(sent[0])["sets"]] == [3, 2, psk.RECEIVER_TEMPLATE, psk.SENDER_TEMPLATE] and parse(sent[0])["seq"] == 0
    for n in range(2, 6):                                           # datagrams 2 and 3 still carry the templates, 4 and 5 do not
        clock["t"] += 301
        r.add(f"G4AB{n}", 5, 7_050_000 + n * 10_000)
        assert await r.flush() == 1
    kinds = [[s for s, _ in parse(d)["sets"]] for d in sent]
    assert [3 in k for k in kinds] == [True, True, True, False, False]
    assert [parse(d)["seq"] for d in sent] == [0, 1, 2, 3, 4]       # the sequence counts reports, not datagrams
    assert len({parse(d)["domain"] for d in sent}) == 1             # the same observation domain all session
    clock["t"] += 3700                                              # an hour later the templates are repeated
    r.add("G4ZZZ", 5, 7_050_000)
    await r.flush()
    assert 3 in [s for s, _ in parse(sent[-1])["sets"]]


async def test_a_full_datagram_goes_at_once_and_the_rest_waits_for_the_next():
    clock, sent = {"t": T0}, []
    r = make(clock, sent)
    r.last_at = T0
    for n in range(psk.MAX_RECORDS + 5):
        clock["t"] = T0 + n
        r.add(f"G4A{n:03d}", 5, 14_236_000)
    assert r.due(clock["t"])
    assert await r.flush() == psk.MAX_RECORDS and len(r.pending) == 5
    assert len(spots(parse(sent[0]))) == psk.MAX_RECORDS
    assert not r.due(clock["t"])                                    # five spots are not a full packet: they wait for the five minutes


def test_a_station_is_reported_once_an_hour_unless_its_frequency_changed():
    clock, sent = {"t": T0}, []
    r = make(clock, sent)
    assert r.add("CT2IOK", 6, 14_236_000) == ""
    r.last_sent["CT2IOK"] = (T0, 14_236_000)
    r.pending.clear()
    clock["t"] = T0 + 600
    assert "less than an hour" in r.add("CT2IOK", 7, 14_236_500)    # same frequency (within 2 kHz): not again
    assert r.add("CT2IOK", 7, 14_242_000) == ""                     # moved band/channel: reported
    r.pending.clear()
    clock["t"] = T0 + 3601
    assert r.add("CT2IOK", 7, 14_236_000) == ""                     # an hour later: reported again
    r.add("CT2IOK", 9, 14_236_000, now=T0 + 3650)
    assert r.pending["CT2IOK"][1] == 9.0 and len(r.pending) == 1    # twice before the batch goes: the latest only


async def test_a_failed_send_keeps_the_spots_for_the_next_try():
    clock, sent = {"t": T0}, []
    r = make(clock, sent)
    r.last_at = T0
    r.add("CT2IOK", 6, 14_236_000)
    clock["t"] = T0 + 301

    def boom(data):
        raise OSError("network is unreachable")
    r._send_fn = boom
    assert await r.flush() == 0 and "unreachable" in r.status()["error"] and "CT2IOK" in r.pending and r.seq == 0
    r._send_fn = sent.append
    assert await r.flush() == 1 and r.status()["error"] == "" and r.status()["sent"] == 1 and r.status()["queued"] == 0


def test_the_status_says_whether_it_is_ready_and_why_not():
    clock, sent = {"t": T0}, []
    assert make(clock, sent, psk=False).status()["enabled"] is False
    s = make(clock, sent, grid_square="").status()
    assert s["enabled"] is True and s["ready"] is False and "grid" in s["reason"]
    assert make(clock, sent).status() == {"enabled": True, "ready": True, "reason": "", "queued": 0, "sent": 0, "last_at": 0, "error": ""}


def test_the_setting_is_off_by_default_and_must_be_a_boolean():
    c = copy.deepcopy(config.DEFAULTS)
    assert c["reporter"]["psk"] is False
    c["reporter"]["psk"] = "yes"
    with pytest.raises(config.ConfigError):
        config.validate(c)


# ------------------------------------------------------------ in the running program
async def test_a_heard_station_reaches_both_the_site_report_and_the_psk_queue(make_app):
    from radio_remote.common import K_DRIVER
    client = await make_app()
    app = client.server.app
    app[K_CFG]["reporter"].update({"psk": True, "callsign": "G4ABC", "grid_square": "IO91wm"})
    app[K_DRIVER].state["frequency"] = 14_236_000
    psk_r = app[K_PSK]
    why = app[K_AUDIO].on_freedv_heard("ct2iok", 6.4)
    assert isinstance(why, str) and why != ""                       # the site report was not sent (the link is off in this test) and says why ...
    assert psk_r.pending["CT2IOK"][0] == 14_236_000                 # ... but the PSK Reporter spot is queued, with the dial frequency
    app[K_CFG]["reporter"]["psk"] = False
    app[K_AUDIO].on_freedv_heard("EA1ABC", 3.0)
    assert "EA1ABC" not in psk_r.pending                            # switched off: nothing is collected
