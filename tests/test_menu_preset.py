"""The preset for the app (menu.py APP_PRESET): every radio's list matches its own menu table, and it is applied, checked and undone safely."""
import types

import pytest

from radio_remote import menu
from radio_remote.radio.base import RadioError

MODELS = ["ft991a", "ftdx10", "ftdx101d", "ftdx101mp", "ft710"]


@pytest.mark.parametrize("model", MODELS)
def test_each_entry_is_a_usb_choice_of_an_item_that_exists(model):
    defs = menu.MenuDef(menu.load(model))
    by_addr = {defs.address(n): n for n in defs.items}
    for addr, want, why in menu.APP_PRESET[model]:
        it = defs.items[by_addr[addr]]
        assert it["kind"] == "enum" and ("REAR SELECT" in it["name"] or "PORT SELECT" in it["name"]), (addr, it["name"])
        assert next(o["label"] for o in it["options"] if o["v"] == want) == "USB", (model, addr, it["name"])
        assert why


class FakeDriver:
    def __init__(self, values, fail=()):
        self.values, self.fail, self.writes = dict(values), set(fail), []

    async def menu_read(self, addr):
        if addr in self.fail:
            raise RadioError("no answer")
        return self.values[addr]

    async def menu_write(self, addr, raw):
        self.writes.append((addr, raw))
        if addr not in self.fail:
            self.values[addr] = raw


def service(model, values, keyed=False, fail=()):
    d = FakeDriver(values, fail)
    sent, audit = [], []
    svc = menu.MenuService(d, types.SimpleNamespace(keyed=keyed), sent.append, lambda ev, detail: audit.append((ev, detail)), menu.MenuDef(menu.load(model)))
    return svc, d, sent, audit


FTDX10_DATA = {"010114": "0", "010215": "1", "010314": "0", "010416": "0"}          # SSB, FM and DATA still on the rear DATA jack, AM already on USB


async def test_the_plan_lists_only_what_differs_and_writes_nothing():
    svc, d, _, _ = service("ftdx10", FTDX10_DATA)
    plan = await svc.preset_plan()
    assert [r["change"] for r in plan["items"]] == [True, False, True, True] and plan["pending"] == 3
    assert plan["items"][0]["now_label"] == "DATA" and plan["items"][0]["want_label"] == "USB" and d.writes == []


async def test_apply_writes_only_the_differences_proves_each_and_can_be_undone():
    svc, d, sent, audit = service("ftdx10", FTDX10_DATA)
    res = await svc.preset_apply("jorge")
    assert [w[0] for w in d.writes] == ["010114", "010314", "010416"] and all(w[1] == "1" for w in d.writes)
    assert len(res["changed"]) == 3 and res["same"] == 1 and not res["failed"]
    assert {e for e, _ in audit} == {"menu_preset"} and any(m.get("k") == "preset" for m in sent)
    assert (await svc.preset_apply("jorge"))["changed"] == []                       # nothing left to do
    back = await svc.preset_undo("jorge")
    assert len(back["changed"]) == 3 and d.values == FTDX10_DATA and not svc.preset_prev


async def test_a_radio_that_does_not_take_a_value_is_reported_not_hidden():
    svc, d, _, _ = service("ftdx10", FTDX10_DATA, fail={"010314"})
    res = await svc.preset_apply("jorge")
    assert len(res["changed"]) == 2 and len(res["failed"]) == 1
    assert "no answer" in res["failed"][0]["why"]


async def test_never_while_transmitting():
    svc, d, _, _ = service("ftdx10", FTDX10_DATA, keyed=True)
    with pytest.raises(ValueError, match="transmitting"):
        await svc.preset_apply("jorge")
    assert d.writes == []


async def test_the_ft710_needs_no_menu_change():
    svc, d, _, _ = service("ft710", {})
    plan = await svc.preset_plan()
    assert plan["items"] == [] and plan["pending"] == 0


def test_ftdx10_has_its_own_alc_full_scale(tmp_path):
    from radio_remote import config
    p = tmp_path / "c.toml"
    p.write_text('[radio]\nmodel = "ftdx10"\n')
    assert config.load(p)["ui"]["meter_alc_full"] == 48
    p.write_text('[radio]\nmodel = "ftdx10"\n[ui]\nmeter_alc_full = 100\n')
    assert config.load(p)["ui"]["meter_alc_full"] == 100
    p.write_text('[radio]\nmodel = "ft991a"\n')
    assert config.load(p)["ui"]["meter_alc_full"] == 160
