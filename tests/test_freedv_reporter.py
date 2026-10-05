"""FreeDV Reporter link, against a small fake Socket.IO server that plays qso.freedv.org."""
import asyncio
import copy
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from test_audio import until
from test_freedv import fake_modem  # noqa: F401  (stand-in modem fixture)
from test_phase8 import make_app  # noqa: F401

from radio_remote import config, freedv_reporter
from radio_remote.common import K_AUDIO, K_CFG, K_REPORTER
from radio_remote.freedv_reporter import FreeDVReporter

STATIONS = ('42["bulk_update",['
            '["new_connection",{"sid":"s1","callsign":"VK5DGR","grid_square":"PF95","version":"FreeDV 2.2","rx_only":false}],'
            '["freq_change",{"sid":"s1","freq":14236000}],["tx_report",{"sid":"s1","mode":"RADEV1","transmitting":true}],'
            '["message_update",{"sid":"s1","message":"CQ FreeDV"}],'
            '["new_connection",{"sid":"s2","callsign":"W1AW","grid_square":"FN31","version":"FreeDV 2.2","rx_only":true}],'
            '["freq_change",{"sid":"s2","freq":7177000}],["tx_report",{"sid":"s2","mode":"700D","transmitting":false}],'
            '["new_connection",{"sid":"s3","callsign":"JA1XYZ","grid_square":"PM95","version":"x","rx_only":false}],'
            '["freq_change",{"sid":"s3","freq":14237500}],["tx_report",{"sid":"s3","mode":"RADEV2","transmitting":false}],'
            '["new_connection",{"sid":"me123","callsign":"G4ABC","grid_square":"IO91","version":"Radio Remote","rx_only":false}]]]')


@pytest.fixture
async def site():
    """A fake FreeDV Reporter. state: auth (what we sent), received (events from us), connections, refuse, pongs."""
    state = {"auth": None, "received": [], "connections": 0, "refuse": False, "pongs": 0, "ws": None}

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state["connections"] += 1
        await ws.send_str('0{"sid":"eio","upgrades":[],"pingInterval":25000,"pingTimeout":20000}')
        state["auth"] = json.loads((await ws.receive_str())[2:])
        if state["refuse"]:
            await ws.send_str('44{"message":"callsign not allowed"}')
            await ws.close()
            return ws
        await ws.send_str('40{"sid":"me123"}')
        await ws.send_str("2")
        await ws.send_str(STATIONS)
        state["ws"] = ws
        async for msg in ws:
            if msg.data == "3":
                state["pongs"] += 1
            elif msg.data.startswith("42"):
                state["received"].append(json.loads(msg.data[2:]))
        return ws

    app = web.Application()
    app.router.add_get("/socket.io/", handler)
    srv = TestServer(app)
    await srv.start_server()
    state["host"] = f"127.0.0.1:{srv.port}"
    yield state
    await srv.close()


def make_reporter(site, **over):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["reporter"].update({"enabled": True, "announce": True, "watch": True, "callsign": "g4abc", "grid_square": "io91wm", "message": "hello",
                            "host": site["host"], "tls": False, **over})
    freq = {"hz": 14_236_000}
    r = FreeDVReporter(lambda: cfg, "9.9.9", lambda: freq["hz"])
    r.cfg, r.freq_box = cfg, freq
    return r


async def test_it_stays_silent_until_enabled_and_freedv_is_on(site):
    r = make_reporter(site, enabled=False)
    r.start()
    r.set_active(True, "700D")
    await asyncio.sleep(0.4)
    assert site["connections"] == 0 and r.status()["wanted"] == ""
    r.cfg["reporter"]["enabled"] = True
    r.set_active(False)
    await asyncio.sleep(0.3)
    assert site["connections"] == 0                                                  # FreeDV off: not connected
    r.set_active(True, "700D")
    await until(lambda: r.connected)
    assert site["connections"] == 1
    await r.stop()


async def test_announce_and_watch(site):
    r = make_reporter(site)
    r.start()
    r.set_active(True, "700E")
    await until(lambda: r.connected and len(r.stations) == 4)
    a = site["auth"]
    assert a == {"protocol_version": 2, "role": "report", "callsign": "G4ABC", "grid_square": "IO91wm", "version": "Radio Remote 9.9.9", "rx_only": False, "os": "Linux"}
    await until(lambda: len(site["received"]) >= 3)
    assert ["freq_change", {"freq": 14236000}] in site["received"]                   # where we are
    assert ["tx_report", {"mode": "700E", "transmitting": False}] in site["received"]
    assert ["message_update", {"message": "hello"}] in site["received"]
    await until(lambda: site["pongs"] >= 1)                                          # the server's ping was answered
    st = r.status(near_hz=14_236_000)
    calls = [s["callsign"] for s in st["stations"]]
    assert "G4ABC" not in calls                                                      # we are not listed to ourselves
    assert calls[:2] == ["VK5DGR", "JA1XYZ"] and st["near"] == 2 and st["total"] == 3         # on our frequency (+-5 kHz) first, transmitting first
    vk = st["stations"][0]
    assert vk["tx"] is True and vk["mode"] == "RADE" and vk["tunable"] is True and vk["message"] == "CQ FreeDV" and vk["freq"] == 14236000
    ja = next(s for s in st["stations"] if s["callsign"] == "JA1XYZ")
    assert ja["mode"] == "RADEV2" and ja["tunable"] is False                          # a mode we cannot decode
    assert next(s for s in st["stations"] if s["callsign"] == "W1AW")["listening"] is True
    await r.stop()


async def test_changes_are_reported(site):
    r = make_reporter(site)
    r.start()
    r.set_active(True, "RADE")
    await until(lambda: r.connected and len(site["received"]) >= 3)
    site["received"].clear()
    r.set_frequency(14_237_000)
    r.set_transmitting(True)
    r.set_transmitting(True)                                                         # no change: not repeated
    r.set_transmitting(False)
    r.cfg["reporter"]["message"] = "CQ CQ"
    r.set_message()
    await until(lambda: len(site["received"]) >= 4)
    assert site["received"] == [["freq_change", {"freq": 14237000}], ["tx_report", {"mode": "RADEV1", "transmitting": True}],
                                ["tx_report", {"mode": "RADEV1", "transmitting": False}], ["message_update", {"message": "CQ CQ"}]]
    await r.stop()


@pytest.mark.parametrize("over,role", [({"announce": False}, "view"), ({"callsign": ""}, "view"), ({"watch": False}, "report_wo"),
                                       ({"announce": False, "watch": False}, "")])
async def test_roles(site, over, role):
    r = make_reporter(site, **over)
    r.start()
    r.set_active(True, "700D")
    if not role:
        await asyncio.sleep(0.4)
        assert site["connections"] == 0
    else:
        await until(lambda: r.connected)
        assert site["auth"]["role"] == role and r.role == role
        if role == "view":
            assert "callsign" not in site["auth"]                                    # a viewer says nothing about itself
            r.set_frequency(7_000_000)
            r.set_transmitting(True)
            await asyncio.sleep(0.3)
            assert site["received"] == []                                            # and reports nothing
    st = r.status()
    assert st["needs_callsign"] is (over == {"callsign": ""})
    await r.stop()


async def test_switching_freedv_off_or_changing_the_identity_reconnects(site):
    r = make_reporter(site)
    r.start()
    r.set_active(True, "700D")
    await until(lambda: r.connected and r.stations)
    r.set_active(False)
    await until(lambda: not r.connected)
    assert r.stations == {}
    r.set_active(True, "700D")
    await until(lambda: r.connected)
    assert site["connections"] == 2
    r.cfg["reporter"]["callsign"] = "M0XYZ"
    r.apply()
    await until(lambda: site["connections"] == 3 and r.connected)
    assert site["auth"]["callsign"] == "M0XYZ"
    await r.stop()


async def test_removals_and_a_refusal(site, monkeypatch):
    r = make_reporter(site)
    r.start()
    r.set_active(True, "700D")
    await until(lambda: len(r.stations) == 4)
    await site["ws"].send_str('42["remove_connection",{"sid":"s2"}]')
    await site["ws"].send_str('42["unknown_event",{"sid":"s2"}]')
    await site["ws"].send_str("42not json")
    await until(lambda: "s2" not in r.stations)
    await r.stop()
    monkeypatch.setattr(freedv_reporter, "BACKOFF", (0.2,))
    site["refuse"] = True
    r2 = make_reporter(site)
    r2.start()
    r2.set_active(True, "700D")
    await until(lambda: "refused" in r2.error and site["connections"] >= 3)             # it reports the reason and keeps trying
    assert r2.connected is False and r2.status()["error"].startswith("the server refused")
    await r2.stop()


async def test_an_unreachable_site_is_just_an_error(site):
    r = make_reporter(site, host="127.0.0.1:1")
    r.start()
    r.set_active(True, "700D")
    await until(lambda: r.error != "")
    assert r.connected is False
    await r.stop()


def test_config_validation():
    cfg = config.load(None)
    assert cfg["reporter"]["enabled"] is False and cfg["reporter"]["host"] == "qso.freedv.org" and cfg["reporter"]["tls"] is True
    for key, bad in (("callsign", "a b"), ("callsign", "AB"), ("grid_square", "ZZ99"), ("grid_square", "IO9"), ("message", "x" * 101),
                     ("message", "line\nbreak"), ("host", "bad host!"), ("host", "a.b:99999999")):
        c = copy.deepcopy(cfg)
        c["reporter"][key] = bad
        with pytest.raises(config.ConfigError):
            config.validate(c)
    c = copy.deepcopy(cfg)
    c["reporter"].update({"callsign": "G4ABC/P", "grid_square": "IO91wm", "message": "FreeDV on 20m"})
    config.validate(c)


async def test_wiring_into_the_app(make_app, fake_modem):
    from test_phase8 import admin_user
    client = await make_app()
    await admin_user(client)
    rep, audio = client.app[K_REPORTER], client.app[K_AUDIO]
    audio.freedv_ok = True
    audio.set_freedv(True, "700E")
    assert rep.active and rep.mode == "700E"
    audio._fd_tx_reported = False
    audio._tx_gate_open = True
    audio._check_fd_tx()
    assert rep.transmitting is True                                                   # a transmission with FreeDV on
    audio._tx_gate_open = False
    audio._check_fd_tx()
    assert rep.transmitting is False
    audio.set_freedv(False)
    assert rep.active is False
    st = await (await client.get("/api/freedv/reporter")).json()
    assert st["enabled"] is False and st["connected"] is False and st["stations"] == []
