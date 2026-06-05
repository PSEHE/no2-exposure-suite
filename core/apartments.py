"""Apartment buildings for CONTAM-Lite — full-building stack modeling.

A "which floor do you live on?" feature: the WHOLE building is solved as one
multizone network, so the stack effect (buoyancy over the building height, acting
through the stairwell that connects the floors) emerges naturally from the airflow
solve — no special-casing. The occupant's unit on the chosen floor is then
reported. Per the Sci. Adv. approach the lite/Explorer side keeps two-story unit
slices; here the full building is modeled.

Covers tractable buildings (<= ZONE_CAP zones, >= 2 floors). Taller high-rises
(11–21 storeys, >ZONE_CAP zones) are deferred to a reduced-order stack model.

Buildings are the raw CS-11 APTS, transformed at load by core.transform (which
also derives each unit's exhaust-fan mechanical ventilation).
"""
from __future__ import annotations

import glob
import os
import re
from collections import defaultdict

from . import config, prj, transform

ZONE_CAP = 200                       # full-building solve stays interactive below this
_TAG_RE = re.compile(r"([A-Z]+)$")   # trailing unit tag, e.g. kitchenA -> A
# Shared circulation/common zones — they belong to no dwelling unit.
SHARED_ZONES = ("stair", "corridor", "hall", "lobby", "elev", "shaft",
                "vestibule", "foyer", "common", "mech", "trash", "chute")


def unit_tag(name):
    """Unit identifier = trailing capital letters of a zone name ('' if none)."""
    m = _TAG_RE.search(name or "")
    return m.group(1) if m else ""


def is_unit_zone(name):
    """A zone that belongs to a dwelling unit (not a shared circulation zone)."""
    nm = (name or "").lower()
    return transform.is_living(name) and not any(s in nm for s in SHARED_ZONES)


def building_floors(model):
    """Occupiable floors, ordered bottom→top: [(level_id, floor_number)]."""
    heights = {}
    for z in model.zones.values():
        if transform.is_living(z.name) and z.level in model.levels:
            heights.setdefault(z.level, model.levels[z.level].refHt)
    ordered = sorted(heights.items(), key=lambda kv: kv[1])
    return [(lid, i + 1) for i, (lid, _) in enumerate(ordered)]


def units_on_floor(model, level_id):
    """{unit_tag: [zone_ids]} — dwelling-unit zones on a floor grouped by tag
    (shared circulation zones excluded)."""
    units = defaultdict(list)
    for z in model.zones.values():
        if z.level == level_id and is_unit_zone(z.name):
            units[unit_tag(z.name)].append(z.id)
    return dict(units)


def occupant_unit(model, level_id, tag):
    """(kitchen_zone_id, [unit_zone_ids]) for unit `tag` on floor `level_id`."""
    zone_ids = [z.id for z in model.zones.values()
                if z.level == level_id and is_unit_zone(z.name)
                and unit_tag(z.name) == tag]
    kitchens = [zid for zid in zone_ids
                if "kitchen" in model.zones[zid].name.lower()]
    kid = kitchens[0] if kitchens else (zone_ids[0] if zone_ids else None)
    return kid, zone_ids


def load_building(rel_path):
    """Parse + transform a raw CS-11 apartment building."""
    return transform.apply_modifications(prj.parse_prj(str(config.PERSILY_DIR / rel_path)))


def list_buildings():
    """Metadata for the tractable apartment buildings (UI selector)."""
    out = []
    for p in sorted(glob.glob(str(config.PERSILY_DIR / "cs11" / "APTS" / "*.prj"))):
        model = prj.parse_prj(p)
        nz = len(model.zones)
        floors = building_floors(model)
        if len(floors) < 2 or nz > ZONE_CAP:
            continue
        tags = units_on_floor(model, floors[len(floors) // 2][0])
        out.append({
            "id": os.path.splitext(os.path.basename(p))[0],
            "rel_path": os.path.relpath(p, config.PERSILY_DIR),
            "n_floors": len(floors),
            "units_per_floor": len(tags),
            "n_zones": nz,
        })
    return sorted(out, key=lambda b: (b["n_floors"], b["units_per_floor"], b["id"]))
