"""The radio's own menu on the Menu tab: every item the CAT manual lists (the FT-991A has 153, read and set with the EX command).

The table of items (number, name, kind of value, allowed values, digits) is data in radio/menus/<model>.json, built from the CAT manual by tools/gen_menu_ft991a.py.
Reading is done a group at a time in the background (low priority, so the normal controls are not slowed); a change is written, read back and checked, and every change goes in the audit log.
Items that would cut the app's link to the radio or change how it is keyed (CAT speed, PTT and port selection, ...) are shown but cannot be changed here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from .radio.base import RadioError

log = logging.getLogger("menu")
MENU_DIR = Path(__file__).parent / "radio" / "menus"
READ_GAP_S = 0.01


def available(model: str) -> bool:
    return (MENU_DIR / f"{model}.json").is_file()


def load(model: str) -> dict:
    return json.loads((MENU_DIR / f"{model}.json").read_text())


def default_raw(item: dict) -> str:
    """A plausible value for a simulated radio: the first choice, or 0 (or the smallest value when 0 is out of range)."""
    k = item["kind"]
    if k == "enum":
        return str(item["options"][0]["v"]).zfill(item["digits"])
    if k == "int":
        v = 0 if item["lo"] <= 0 <= item["hi"] else item["lo"]
        return format_int(item, v)
    if k == "raw":
        return "0" * (item["digits"] or 1)
    return ""


def format_int(item: dict, v: int) -> str:
    d = item["digits"]
    if item.get("signed"):
        return ("+" if v >= 0 else "-") + str(abs(v)).zfill(d - 1)
    return str(v).zfill(d)


class MenuDef:
    def __init__(self, data: dict):
        self.model = data["model"]
        self.items: dict[int, dict] = {i["n"]: i for i in data["items"]}
        self.groups: list[str] = list(dict.fromkeys(i["group"] for i in data["items"]))

    def address(self, n: int) -> str:
        """What follows EX in the command: the three digits of the FT-991A's menu number, or the six digits P1 P2 P3 of the other radios' table."""
        return self.items[n].get("addr") or f"{n:03d}"

    # --------------------------------------------------------------- values
    def decode(self, n: int, raw: str | None) -> dict:
        """The raw text the radio answered as {"raw", "ok", "value", "label"}; ok is False when the text is not what the table says (the item is then shown but not edited)."""
        it = self.items[n]
        out = {"raw": raw, "ok": False, "value": None, "label": raw if raw else ""}
        if raw is None or it["kind"] == "none":
            return out
        k = it["kind"]
        if k == "raw":
            return out
        if k == "enum":
            if re.fullmatch(r"\d{%d}" % it["digits"], raw):
                v = int(raw)
                for o in it["options"]:
                    if o["v"] == v:
                        return {"raw": raw, "ok": True, "value": v, "label": o["label"]}
            out["label"] = f"? ({raw})"
            return out
        if k == "int":
            d = it["digits"]
            pat = r"[+-]\d{%d}" % (d - 1) if it.get("signed") else r"\d{%d}" % d
            if not re.fullmatch(pat, raw):
                out["label"] = f"? ({raw})"
                return out
            v = int(raw)
            ok = it["lo"] <= v <= it["hi"]
            return {"raw": raw, "ok": ok, "value": v, "label": self.label_int(it, v) if ok else f"? ({raw})"}
        return out

    @staticmethod
    def label_int(it: dict, v: int) -> str:
        if v == 0 and it.get("zero"):
            return it["zero"]
        s = f"{v * it['scale']:.1f}" if it.get("scale") else str(v)
        if it.get("signed") and v > 0:
            s = "+" + s
        return f"{s} {it['unit']}".strip()

    def encode(self, n: int, value) -> str:
        """The raw text to write for a value; ValueError says what is wrong."""
        it = self.items.get(n)
        if it is None:
            raise ValueError("there is no such menu item")
        if it.get("locked"):
            raise ValueError(f"menu {n:03d} {it['name']} cannot be changed here: {it['locked']}")
        k = it["kind"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("the value must be a whole number")
        if k == "enum":
            if not any(o["v"] == value for o in it["options"]):
                raise ValueError("that is not one of the choices")
            return str(value).zfill(it["digits"])
        if k == "int":
            if not it["lo"] <= value <= it["hi"]:
                raise ValueError(f"the value must be between {it['lo']} and {it['hi']}")
            if (value - it["lo"]) % it["step"]:
                raise ValueError(f"the value must be in steps of {it['step']}")
            return format_int(it, value)
        raise ValueError("this item cannot be changed here")

    def public(self, n: int, raw: str | None, t: float | None = None) -> dict:
        it = dict(self.items[n])
        it.setdefault("num", f"{n:03d}")                                        # the number as the radio's menu shows it
        it.update(self.decode(n, raw))
        it["t"] = t
        return it


class MenuService:
    def __init__(self, driver, guard, broadcast: Callable[[dict], None], audit: Callable[[str, str], None], defs: MenuDef):
        self.driver, self.guard, self.broadcast, self.audit, self.defs = driver, guard, broadcast, audit, defs
        self.cache: dict[int, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    def listing(self) -> dict:
        items = [self.defs.public(n, *(self.cache.get(n) or (None, None))) for n in sorted(self.defs.items)]
        return {"model": self.defs.model, "groups": self.defs.groups, "items": items, "now": time.time()}

    async def _read_one(self, n: int) -> str:
        raw = await self.driver.menu_read(self.defs.address(n))
        self.cache[n] = (raw, time.time())
        return raw

    async def read(self, numbers: list[int]) -> list[dict]:
        """Read some items from the radio (one after the other, at low priority). An item the radio does not answer is returned with an error; three failures in a row stop the reading."""
        out, fails = [], 0
        async with self._lock:
            for n in numbers:
                it = self.defs.items.get(n)
                if it is None or it["kind"] == "none":
                    continue
                try:
                    await self._read_one(n)
                    fails = 0
                except RadioError as e:
                    fails += 1
                    d = self.defs.public(n, None)
                    d["error"] = str(e)
                    out.append(d)
                    if fails >= 3:
                        break
                    continue
                out.append(self.defs.public(n, *self.cache[n]))
                await asyncio.sleep(READ_GAP_S)
        return out

    async def set(self, n: int, value, who: str) -> dict:
        if self.guard.keyed:
            raise ValueError("not while transmitting")
        raw = self.defs.encode(n, value)
        async with self._lock:
            old = self.cache.get(n, (None,))[0]
            if old is None:
                old = await self._read_one(n)
            if old == raw:
                return self.defs.public(n, raw, time.time())
            await self.driver.menu_write(self.defs.address(n), raw)
            back = await self._read_one(n)
            if back != raw:
                self.audit("menu_set_failed", f"{n:03d} {self.defs.items[n]['name']}: wrote {raw}, the radio answered {back} (by {who})")
                raise ValueError(f"the radio did not take the value (it says {back})")
        self.audit("menu_set", f"{n:03d} {self.defs.items[n]['name']}: {old} -> {raw} (by {who})")
        item = self.defs.public(n, raw, time.time())
        self.broadcast({"t": "menu", "k": "item", "item": item})
        return item

    async def backup(self) -> dict:
        """Every item that can be read, as the radio's own text: a file the operator can keep and load back later."""
        numbers = [n for n, it in sorted(self.defs.items.items()) if it["kind"] != "none"]
        got = await self.read(numbers)
        values = {f"{d['n']:03d}": d["raw"] for d in got if d.get("raw") is not None and not d.get("error")}
        return {"program": "Radio Remote menu backup", "model": self.defs.model, "taken": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "values": values}

    async def restore(self, data: dict, who: str) -> dict:
        """Write the values of a backup back, one item at a time: only items that differ, can be changed here and whose value is one the table allows."""
        if self.guard.keyed:
            raise ValueError("not while transmitting")
        if not isinstance(data, dict) or data.get("model") != self.defs.model or not isinstance(data.get("values"), dict):
            raise ValueError(f"that is not a menu backup of this radio ({self.defs.model})")
        report: dict = {"changed": [], "same": 0, "skipped": [], "errors": []}
        for key in sorted(data["values"]):
            raw = data["values"][key]
            if not (isinstance(key, str) and key.isdigit() and isinstance(raw, str)) or int(key) not in self.defs.items:
                report["skipped"].append({"n": key, "why": "unknown item"})
                continue
            n = int(key)
            it = self.defs.items[n]
            if it.get("locked") or it["kind"] in ("none", "raw"):
                report["skipped"].append({"n": n, "name": it["name"], "why": "cannot be changed here"})
                continue
            dec = self.defs.decode(n, raw)
            if not dec["ok"]:
                report["skipped"].append({"n": n, "name": it["name"], "why": f"the value {raw!r} is not one the table allows"})
                continue
            try:
                async with self._lock:
                    cur = await self._read_one(n)
                if cur == raw:
                    report["same"] += 1
                    continue
                await self.set(n, dec["value"], who)
                report["changed"].append({"n": n, "name": it["name"], "from": cur, "to": raw})
            except (ValueError, RadioError) as e:
                report["errors"].append({"n": n, "name": it["name"], "why": str(e)})
                if len(report["errors"]) >= 5:
                    report["errors"].append({"n": 0, "name": "", "why": "stopped after 5 errors"})
                    break
        self.audit("menu_restore", f"{len(report['changed'])} changed, {report['same']} the same, {len(report['skipped'])} skipped, {len(report['errors'])} errors (by {who})")
        return report
