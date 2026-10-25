"""The radio's menu (menu.py + radio/menus/ft991a.json): the table built from the CAT manual, reading and changing items on the mock radio and on the real driver against the simulator, the backup and the API."""
import asyncio
import json
import re

import pytest

from radio_remote import menu
from radio_remote.radio.base import RadioError
from radio_remote.radio.drivers.ft991a import FT991ADriver
from radio_remote.radio.mock import MockDriver
from radio_remote.safety import TxGuard
from test_cat import FAST, Rig, wait_for
from test_phase8 import PW, admin_user, close_all, make_app  # noqa: F401

DEFS = menu.MenuDef(menu.load("ft991a"))


def test_the_table_has_every_menu_item_of_the_manual():
    assert sorted(DEFS.items) == list(range(1, 154))
    assert DEFS.items[1]["name"] == "AGC FAST DELAY" and DEFS.items[31]["name"] == "CAT RATE" and DEFS.items[153]["name"] == "WIRES DG-ID" and DEFS.items[36]["name"] == "TX TOT"
    assert DEFS.groups[0] == "AGC" and "Equalizers" in DEFS.groups and DEFS.groups[-1] == "Other"
    for n, it in DEFS.items.items():
        assert it["name"] and it["group"] and it["kind"] in ("enum", "int", "raw", "none"), n
        assert not re.search(r"\s\d\d\s*:$|~$", it["name"]), (n, it["name"])


def test_every_choice_and_every_limit_encodes_to_the_digits_the_manual_gives():
    for n, it in DEFS.items.items():
        if it["kind"] == "enum":
            for o in it["options"]:
                raw = menu.default_raw({**it, "options": [o]})
                assert len(raw) == it["digits"] and DEFS.decode(n, raw)["label"] == o["label"], (n, o)
        elif it["kind"] == "int":
            for v in (it["lo"], it["hi"]):
                raw = menu.format_int(it, v)
                assert len(raw) == it["digits"], (n, v, raw)
                dec = DEFS.decode(n, raw)
                assert dec["ok"] and dec["value"] == v, (n, v, raw, dec)


def test_decode_and_encode_in_the_radios_own_format():
    assert DEFS.decode(26, "050") == {"raw": "050", "ok": True, "value": 50, "label": "50"}
    assert DEFS.decode(112, "-15")["value"] == -15 and DEFS.decode(112, "-15")["label"] == "-15"
    assert DEFS.decode(112, "+10")["label"] == "+10" and DEFS.decode(112, "+00")["value"] == 0
    assert DEFS.decode(14, "35")["label"] == "3.5"                                    # the CW weight is 2.5 .. 4.5
    assert DEFS.decode(15, "000")["label"] == "OFF" and DEFS.decode(153, "00")["label"] == "AUTO" and DEFS.decode(153, "25")["label"] == "25"
    assert DEFS.decode(6, "3")["label"] == "ORANGE" and DEFS.decode(6, "9") == {"raw": "9", "ok": False, "value": None, "label": "? (9)"}
    assert DEFS.decode(26, "5x0")["ok"] is False and DEFS.decode(26, "500")["ok"] is False        # not digits / out of range
    assert DEFS.decode(41, "05")["label"] == "300 Hz" and DEFS.decode(43, "67")["label"] == "4000 Hz" and DEFS.decode(116, "07")["label"] == "1000 kHz"
    assert DEFS.decode(87, "ABC")["ok"] is False and DEFS.decode(27, "+0900")["ok"] is False
    assert DEFS.encode(26, 7) == "007" and DEFS.encode(112, -40) == "-40" and DEFS.encode(112, 0) == "+00" and DEFS.encode(6, 2) == "2" and DEFS.encode(144, 30) == "0030"
    assert DEFS.encode(64, -3000) == "-3000" and DEFS.encode(151, 14250000) == "14250000" and DEFS.encode(100, 0) == "0"
    for n, v, why in ((26, 101, "between"), (26, -1, "between"), (6, 7, "choices"), (144, 35, "steps"), (26, "5", "whole number"), (26, True, "whole number"), (999, 1, "no such")):
        with pytest.raises(ValueError, match=why):
            DEFS.encode(n, v)


def test_items_that_would_cut_the_link_or_change_the_keying_are_locked():
    for n in (28, 29, 30, 31, 32, 33, 47, 48, 60, 71, 72, 76, 77, 96, 108, 109, 87, 27, 101):
        assert DEFS.items[n].get("locked"), n
        with pytest.raises(ValueError, match="cannot be changed here"):
            DEFS.encode(n, 0)
    assert not DEFS.items[26].get("locked") and not DEFS.items[137].get("locked")
    assert "ALC" in DEFS.items[73]["caution"] and "emergency" in DEFS.items[149]["caution"]


class FakeGuard:
    keyed = False


async def service(mock=True):
    d = MockDriver()
    await d.start()
    audit, ev = [], []
    svc = menu.MenuService(d, FakeGuard(), ev.append, lambda e, detail: audit.append((e, detail)), DEFS)
    return d, svc, audit, ev


async def test_reading_fills_the_listing_and_defaults_are_plausible():
    d, svc, audit, ev = await service()
    assert all(i["raw"] is None for i in svc.listing()["items"])
    got = await svc.read([1, 26, 112, 87, 153])
    assert [g["n"] for g in got] == [1, 26, 112, 153]                              # 087 has no value
    assert next(g for g in got if g["n"] == 26)["raw"] == "000" and next(g for g in got if g["n"] == 112)["raw"] == "+00"
    li = {i["n"]: i for i in svc.listing()["items"]}
    assert li[26]["label"] == "0" and li[1]["raw"] == "0020" and li[2]["raw"] is None
    await d.stop()


async def test_changing_an_item_writes_reads_back_audits_and_tells_the_pages():
    d, svc, audit, ev = await service()
    item = await svc.set(26, 40, "root")
    assert item["raw"] == "040" and item["label"] == "40" and d.menu_writes == [("026", "040")]
    assert audit == [("menu_set", "026 BEEP LEVEL: 000 -> 040 (by root)")]
    assert ev[-1]["k"] == "item" and ev[-1]["item"]["n"] == 26
    again = await svc.set(26, 40, "root")                                              # the same value: nothing is written
    assert again["raw"] == "040" and len(d.menu_writes) == 1
    with pytest.raises(ValueError, match="cannot be changed here"):
        await svc.set(31, 3, "root")
    with pytest.raises(ValueError, match="between"):
        await svc.set(26, 500, "root")
    svc.guard.keyed = True
    with pytest.raises(ValueError, match="transmitting"):
        await svc.set(26, 41, "root")
    await d.stop()


async def test_a_value_the_radio_does_not_take_is_reported():
    d, svc, audit, ev = await service()

    async def refuse(address, raw):
        d.menu_store[address] = "999"                                                   # the radio keeps another value
    d.menu_write = refuse
    with pytest.raises(ValueError, match="did not take"):
        await svc.set(26, 40, "root")
    assert audit[-1][0] == "menu_set_failed"
    await d.stop()


async def test_backup_and_restore_round_trip_and_skip_what_must_not_change():
    d, svc, audit, ev = await service()
    await svc.set(26, 25, "root")
    await svc.set(112, -10, "root")
    bk = await svc.backup()
    assert bk["model"] == "ft991a" and len(bk["values"]) == 152 and bk["values"]["026"] == "025" and bk["values"]["112"] == "-10" and "087" not in bk["values"]
    d.menu_store["026"] = "100"
    d.menu_store["112"] = "+05"
    d.menu_store["031"] = "3"
    bk["values"]["031"] = "1"                                                           # CAT RATE: locked, must not be written
    bk["values"]["006"] = "9"                                                           # not a choice of that item
    bk["values"]["777"] = "1"
    rep = await svc.restore(bk, "root")
    assert {c["n"] for c in rep["changed"]} == {26, 112} and d.menu_store["026"] == "025" and d.menu_store["112"] == "-10" and d.menu_store["031"] == "3"
    assert any(s["n"] == 31 for s in rep["skipped"]) and any(s["n"] == 6 for s in rep["skipped"]) and any(s["n"] == "777" for s in rep["skipped"])
    assert rep["errors"] == [] and rep["same"] > 100
    with pytest.raises(ValueError, match="not a menu backup"):
        await svc.restore({"model": "ftdx10", "values": {}}, "root")
    await d.stop()


async def test_one_unanswered_item_does_not_stop_the_rest_but_three_in_a_row_do():
    d, svc, audit, ev = await service()
    real = d.menu_read

    async def flaky(address):
        if address in ("002", "010", "011", "012"):
            raise RadioError("no answer")
        return await real(address)
    d.menu_read = flaky
    got = await svc.read([1, 2, 3])
    assert [(g["n"], "error" in g) for g in got] == [(1, False), (2, True), (3, False)]
    got = await svc.read([10, 11, 12, 13, 14])
    assert [g["n"] for g in got] == [10, 11, 12]                                       # stopped after three failures in a row
    await d.stop()


# ---------------------------------------------------------------- the real FT-991A driver against the simulated radio
@pytest.fixture
async def rig():
    r = Rig()
    d = FT991ADriver(transport_factory=r.factory, timings=FAST)
    r.driver = d
    await d.start()
    await wait_for(lambda: d.state["connected"])
    yield r
    await d.stop()
    for s in r.sims:
        await s.stop()


async def test_real_driver_reads_and_writes_menu_items_with_the_ex_command(rig):
    d, sim = rig.driver, rig.sim
    assert d.caps.has("menu")
    assert await d.menu_read("001") == "0020" and await d.menu_read("026") == "000"
    await d.menu_write("026", "055")
    assert sim.menu_vals["026"] == "055" and "EX026055" in sim.log and await d.menu_read("026") == "055"
    with pytest.raises(RadioError):
        await d.menu_write("26;", "1")
    with pytest.raises(RadioError):
        await d.menu_read("999")
    assert await d.menu_read("153") == "00" and await d.menu_read("106") == "1"          # the items the app already uses keep working
    svc = menu.MenuService(d, TxGuard(d, allow_ptt=True, max_tx_s=60), lambda e: None, lambda e, x: None, DEFS)
    item = await svc.set(112, -12, "root")
    assert item["raw"] == "-12" and sim.menu_vals["112"] == "-12"


# ---------------------------------------------------------------- the API
async def test_api_list_read_set_backup_restore_and_permissions(make_app, tmp_path, monkeypatch):
    from test_phase8 import add_user, login_as
    monkeypatch.chdir(tmp_path)
    client = await make_app()
    root = await admin_user(client)
    lst = await (await client.get("/api/menu")).json()
    assert len(lst["items"]) == 153 and lst["groups"][0] == "AGC" and lst["items"][25]["raw"] is None
    ws, hello = await root.ws()
    assert hello["caps"]["features"]["menu"] is True
    r = await client.post("/api/menu/read", json={"numbers": [26, 112]}, headers=root.h)
    assert r.status == 200 and [i["raw"] for i in (await r.json())["items"]] == ["000", "+00"]
    for bad in ({"numbers": []}, {"numbers": list(range(1, 62))}, {"numbers": ["a"]}, {}):
        assert (await client.post("/api/menu/read", json=bad, headers=root.h)).status == 400
    ack = await root.cmd(ws, 1, type="menu_set", n=26, value=33)
    assert ack["ok"], ack
    ack = await root.cmd(ws, 2, type="menu_set", n=31, value=0)
    assert not ack["ok"] and "cannot be changed" in json.dumps(ack)
    ack = await root.cmd(ws, 3, type="menu_set", n="26", value=1)
    assert not ack["ok"]
    assert (await (await client.get("/api/menu")).json())["items"][25]["raw"] == "033"
    r = await client.get("/api/menu/backup.json")
    assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
    bk = json.loads(await r.text())
    assert bk["values"]["026"] == "033" and len(bk["values"]) == 152
    ack = await root.cmd(ws, 4, type="menu_set", n=26, value=60)
    assert ack["ok"]
    r = await client.post("/api/menu/restore", json=bk, headers=root.h)
    rep = await r.json()
    assert r.status == 200 and [c["n"] for c in rep["changed"]] == [26]
    assert (await client.post("/api/menu/restore", json={"model": "ft991a"}, headers=root.h)).status == 400
    await add_user(root, "watcher", "viewer")
    v = await login_as(root, "watcher")
    assert (await v.c.get("/api/menu")).status == 200
    assert (await v.c.post("/api/menu/restore", json=bk, headers=v.h)).status == 403
    wsv, _ = await v.ws()
    ack = await v.cmd(wsv, 1, type="menu_set", n=26, value=1)
    assert not ack["ok"]
    await close_all(root, v)


# ---------------------------------------------------------------- the other radios' tables (FTDX10, FTDX101D/MP, FT-710): sections / groups / items, six-digit addresses
from radio_remote.radio.cat.proto import MODELS  # noqa: E402
from test_hf_radios import connect as hf_connect  # noqa: E402

ALL = {"ft991a": 153, "ftdx10": 196, "ftdx101d": 192, "ftdx101mp": 192, "ft710": 290}


@pytest.mark.parametrize("model", sorted(ALL))
def test_every_radio_has_a_menu_table_that_is_consistent(model):
    assert menu.available(model)
    defs = menu.MenuDef(menu.load(model))
    assert len(defs.items) == ALL[model] and sorted(defs.items) == list(range(1, ALL[model] + 1))
    addrs = [defs.address(n) for n in defs.items]
    assert len(set(addrs)) == len(addrs)
    assert all(len(a) == (3 if model == "ft991a" else 6) and a.isdigit() for a in addrs)
    for n, it in defs.items.items():
        assert it["name"] and it["group"] and it["kind"] in ("enum", "int", "raw", "none"), (model, n)
        assert it["kind"] != "raw" or it.get("locked"), (model, n, "an item that is not understood must be read only")
        if it["kind"] == "enum":
            assert len({o["v"] for o in it["options"]}) == len(it["options"]) >= 2, (model, n)
            for o in it["options"]:
                raw = str(o["v"]).zfill(it["digits"])
                assert len(raw) == it["digits"] and defs.decode(n, raw)["label"] == o["label"], (model, n, o)
        if it["kind"] == "int":
            for v in (it["lo"], it["hi"]):
                assert defs.decode(n, menu.format_int(it, v))["ok"], (model, n, v)
    # the items the app drives by itself have to be where the app thinks they are
    if model != "ft991a":
        mic = next(it for it in defs.items.values() if it["addr"] == MODELS[model].mic_menu)
        assert "SOURCE" in mic["name"] and mic["kind"] == "enum" and mic.get("caution")
    # whatever sets the CAT link or the keying is locked
    for it in defs.items.values():
        if re.search(r"\bCAT\b.*(RATE|RTS|TIME OUT)|\bPTT SELECT\b|\bRPTT SELECT\b|\bPC KEYING\b|\b232C RATE\b", it["name"]):
            assert it.get("locked"), (model, it["name"])


def test_the_tables_agree_with_what_was_read_on_a_real_ftdx101d():
    defs = menu.MenuDef(menu.load("ftdx101d"))
    by = {it["addr"]: it for it in defs.items.values()}
    assert by["010112"]["name"] == "REAR SELECT" and by["010109"]["name"] == "SSB OUT LEVEL" and by["010209"]["name"] == "AM OUT LEVEL" and by["010113"]["name"] == "RPORT GAIN"
    assert by["010111"]["name"] == "SSB MOD SOURCE"
    ft = menu.MenuDef(menu.load("ft710"))
    assert {i["addr"]: i for i in ft.items.values()}["010114"]["name"] == "MOD SOURCE"


def test_the_signed_and_scaled_items_of_the_new_tables_are_read_in_the_radios_format():
    defs = menu.MenuDef(menu.load("ftdx10"))
    by = {it["num"]: it for it in defs.items.values()}
    treble = by["01-01-01"]
    assert (treble["lo"], treble["hi"], treble["signed"]) == (-10, 10, True) and defs.decode(treble["n"], "-05")["value"] == -5 and defs.encode(treble["n"], 0) == "+00"
    weight = by["02-02-03"]
    assert weight["scale"] == 0.1 and defs.decode(weight["n"], "35")["label"] == "3.5"
    tot = by["03-01-16"]
    assert tot["name"] == "TX TIME OUT TIMER" and tot["zero"] == "OFF" and defs.decode(tot["n"], "00")["label"] == "OFF"
    ft = menu.MenuDef(menu.load("ft710"))
    ctcss = next(i for i in ft.items.values() if i["name"] == "TONE FREQ")
    assert len(ctcss["options"]) == 50 and ctcss["options"][0]["label"] == "67.0 Hz" and ctcss["options"][-1]["label"] == "254.1 Hz"
    assert ft.groups[0] == "Radio Setting: Mode SSB" and "Extension Setting: Preset1" in ft.groups and "Extension Setting: Preset5" in ft.groups


@pytest.mark.parametrize("model", ["ftdx10", "ftdx101d", "ftdx101mp", "ft710"])
async def test_the_menu_works_over_the_real_driver_for_every_other_radio(model):
    r, d = await hf_connect(model)
    try:
        defs = menu.MenuDef(menu.load(model))
        assert d.caps.has("menu")
        svc = menu.MenuService(d, FakeGuard(), lambda e: None, lambda e, x: None, defs)
        by = {it["num"]: it for it in defs.items.values()}
        item = by["01-01-01"] if model != "ftdx101d" and model != "ftdx101mp" else by["01-01-01"]
        got = await svc.read([item["n"]])
        assert got[0]["raw"] and not got[0].get("error")
        beep = next(i for i in defs.items.values() if i["name"] == "BEEP LEVEL")
        out = await svc.set(beep["n"], 33, "root")
        assert out["raw"] == "033" and r.sim.menu_vals[beep["addr"]] == "033" and f"EX{beep['addr']}033" in r.sim.log
        cat = next(i for i in defs.items.values() if re.search(r"\bCAT.*RATE", i["name"]))
        with pytest.raises(ValueError, match="cannot be changed here"):
            await svc.set(cat["n"], 1, "root")
        many = await svc.read(list(range(1, 61)))
        assert len(many) >= 50
    finally:
        await d.stop()
        await r.sim.stop()
