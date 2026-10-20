"""gamedata.py -- relic and potion descriptions read from the player's own game files.

The text comes from localization/eng/*.json inside Slay the Spire's desktop-1.0.jar
and is cached in evolve/_cache/gamedata.json (git-ignored, so the game's text is
never committed). The numbers are filled in by game code at runtime, so they show
as "N" ("Start each combat with N Block."). Everything degrades to "" without the game.
"""

from __future__ import annotations

import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / "evolve" / "_cache" / "gamedata.json"


def find_jar() -> Path | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore"))]
    for lib in libs:
        jar = lib / "steamapps" / "common" / "SlayTheSpire" / "desktop-1.0.jar"
        if jar.exists():
            return jar
    return None


def _clean(parts: list[str]) -> str:
    parts = [p for p in parts if p.strip() and "has been opened" not in p and "I can only" not in p]
    # energy relics store "Gain [E]" after the sentence it starts; put it first
    if len(parts) > 1 and parts[0].strip()[:1].islower():
        k = next((i for i, p in enumerate(parts) if p.strip()[:1].isupper()), 0)
        parts = parts[k:] + parts[:k]
    if any("[E]" in p for p in parts):
        text = " ".join(p.strip() for p in parts)
        text = text.replace("[E]", "1 Energy")
        return re.sub(r"\s+", " ", re.sub(r"#[a-z]", "", text).replace("NL", " ")).strip()
    text = " N ".join(p.strip() for p in parts) if len(parts) > 1 else (parts[0] if parts else "")
    text = re.sub(r"#[a-z]", "", text)
    text = text.replace(" NL ", " ").replace("NL", " ")
    return re.sub(r"\s+", " ", text).replace(" .", ".").replace(" ,", ",").strip()


def _extract(jar: Path) -> dict:
    out: dict[str, dict[str, str]] = {"relics": {}, "potions": {}, "relic_ids": {}, "potion_ids": {}}
    with zipfile.ZipFile(jar) as z:
        for kind in ("relics", "potions"):
            data = json.loads(z.read(f"localization/eng/{kind}.json").decode("utf-8"))
            for rid, entry in data.items():
                name = entry.get("NAME")
                if name:
                    out[kind][name] = _clean(entry.get("DESCRIPTIONS") or [])
                    out[kind[:-1] + "_ids"][rid] = name   # run history files use ids ("SlaversCollar")
    return out


@lru_cache(maxsize=1)
def data() -> dict:
    try:
        if CACHE.exists():
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if "relic_ids" in cached:
                return cached
        jar = find_jar()
        if jar is None:
            return {"relics": {}, "potions": {}, "relic_ids": {}, "potion_ids": {}}
        out = _extract(jar)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(out, indent=0), encoding="utf-8")
        return out
    except Exception:
        return {"relics": {}, "potions": {}, "relic_ids": {}, "potion_ids": {}}


def relic(name: str) -> str:
    return data()["relics"].get(name, "")


def relic_name(rid: str) -> str | None:
    """Display name for a relic id from run history files; None when it is not a relic."""
    d = data()
    if rid in d.get("relic_ids", {}):
        return d["relic_ids"][rid]
    return rid if rid in d["relics"] else None


def potion_name(pid: str) -> str | None:
    d = data()
    if pid in d.get("potion_ids", {}):
        return d["potion_ids"][pid]
    return pid if pid in d["potions"] else None


def potion(name: str) -> str:
    return data()["potions"].get(name, "")


def with_relic(name: str) -> str:
    d = relic(name)
    return f"{name} ({d})" if d else name


def with_potion(name: str) -> str:
    d = potion(name)
    return f"{name} ({d})" if d else name
