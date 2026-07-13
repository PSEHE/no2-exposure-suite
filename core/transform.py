"""Apply the Science Advances (Kashtan et al. 2024) modeling modifications to a
raw NIST CONTAM dwelling so our engine treats it like the paper's homes.

The 209-home CS-11 collection and the 18 "2000-and-later" homes (NIST TN 2329)
ship as *raw* NIST models: real range hoods / HVAC, no gas-stove NO2 source, no
operable windows, and no NO2 decay. The Sci. Adv. methods modified the homes by:

  * adding a first-order NO2 decay (-2.416e-4 /s),
  * replacing open interior doors with bidirectional 1000 m3/h mixing,
  * adding an operable NFRC window (1.2192 m x 1.524 m) on an exterior wall of
    each bedroom / living / dining / kitchen, and
  * adding a kitchen cooktop + oven NO2 source (handled at simulate time via
    transport.simulate(kitchen_zone=...), whose standard rates equal the
    paper's burn_NO2 / ov_pr_NO2 elements).

`apply_modifications(model)` performs the structural parts in memory and returns
the same model. It is idempotent and a no-op on homes that are already modified
(our existing 24), which it detects by the presence of NO2 decay or sources.

Values are taken verbatim from the modified .prj files (e.g. MH-1.prj):
  fan_cvf ConstantVolFlow  ->  0.277778 m3/s (= 1000 m3/h)
  std_win_open dor_door    ->  0.167804 2.62769 0.5 0.01 1.2192 1.524 1 1 1 1
"""
from __future__ import annotations

from .prj import FlowElement, FlowPath, Reaction, Macro

NO2_DECAY = -2.416e-4                       # 1/s
FAN_TYPE_CODE = 31                          # fan_cvf
FAN_ELEM_PARAMS = [0.277778, 4]             # 0.277778 m3/s = 1000 m3/h
WINDOW_TYPE_CODE = 27                       # dor_door (two-way opening)
# std_win_open params: [-, C, n, -, H, W, ...] per airflow's indexing (params[1]=C,
# params[2]=n, params[4]=H, params[5]=W). Verbatim from the modified homes.
WINDOW_ELEM_PARAMS = [0.167804, 2.62769, 0.5, 0.01, 1.2192, 1.524, 1, 1, 1, 1]
WINDOW_RELHT = 1.0                          # m, mid-wall

# Rooms that receive an operable window (Sci. Adv.: bedrooms, living, dining,
# kitchen). Matched as substrings of the zone name.
WINDOW_ROOMS = ("bed", "living", "dining", "kitchen", "master", "family",
                "great", "den", "studio")
# Zones that are never conditioned living space (no window, no mixing-fan).
NON_LIVING = ("attic", "crawl", "garage", "basement", "plenum", "shaft",
              "(ret)", "(sup)", "(rec)", "(exh)", "exh-", "exh_", "ahs",
              "duct", "soil", "ambient", "outdoor", "mech")
# Phantom HVAC/AHS zones (supply/return/exhaust nodes) — not real rooms; they
# have tiny volumes and must be dropped (a 0.1 m³ zone makes transport blow up,
# and an "exh-Kitchen(Ret)" node otherwise gets mistaken for the kitchen).
AHS_ZONE_PATTERNS = ("(ret)", "(sup)", "(rec)", "(exh)", "exh-", "exh_",
                     "ahs", "central_system", "(sys)", "_sys")
# Kitchen zone-name spellings (NIST files include the typo "kithen").
KITCHEN_NAMES = ("kitchen", "kithen", "kichen", "ktichen")
# Interior passages that get a mixing fan-pair: doors, open doorways, and
# stairwells (the vertical column that mixes floors). NOT exterior openings or
# wall/vent leakage (those are envelope/partition leakage, not passages).
PASSAGE = ("door", "stair", "open", "arch", "pass")
NON_PASSAGE = ("ext", "bsmt", "garage", "attic", "crlsp", "crawl",
               "wall", "vent", "leak")


def _norm(name):
    return (name or "").lower()


def is_living(name):
    """A conditioned, occupiable zone (gets mixing; window if also a WINDOW_ROOM)."""
    nm = _norm(name)
    return not any(x in nm for x in NON_LIVING)


def is_window_room(name):
    nm = _norm(name)
    return is_living(nm) and any(x in nm for x in WINDOW_ROOMS)


def kitchen_zone_id(model):
    """Zone id where cooking happens. NIST names it 'kitchen' ('kitchena/b' in
    multi-unit; sometimes misspelled 'kithen'). Must be a real living zone — an
    AHS exhaust node like 'exh-Kitchen(Ret)' is NOT the kitchen. Studios with no
    kitchen zone fall back to the main living zone."""
    cands = [z for z in model.zones.values()
             if is_living(z.name) and any(k in _norm(z.name) for k in KITCHEN_NAMES)]
    if cands:
        # largest kitchen (handles multi-unit 'kitchena/kitchenb')
        return max(cands, key=lambda z: z.volume).id
    # studio fallback: the main living/great/studio zone
    living = [z for z in model.zones.values()
              if any(x in _norm(z.name) for x in ("living", "great", "studio",
                                                  "efficiency", "family"))]
    if living:
        return max(living, key=lambda z: z.volume).id
    # last resort: the largest conditioned zone
    cond = [z for z in model.zones.values() if is_living(z.name)]
    return max(cond, key=lambda z: z.volume).id if cond else None


def already_modified(model):
    """True if this is a paper-modified home (has NO2 decay or an NO2 source)."""
    try:
        if abs(model.decay_of("NO2")) > 1e-12:
            return True
    except Exception:
        pass
    return any(el.species == "NO2" for el in model.source_elements.values())


def _is_ahs_zone(name):
    nm = (name or "").lower()
    return any(p in nm for p in AHS_ZONE_PATTERNS)


def _drop_phantom_zones(model, vol_eps=1e-3):
    """Remove HVAC/AHS phantom zones (supply/return/exhaust nodes — tiny volumes
    that divide-by-zero in transport and get mistaken for real rooms) and any
    flow path that references a removed zone."""
    keep = {zid for zid, z in model.zones.items()
            if z.volume > vol_eps and not _is_ahs_zone(z.name)}
    dropped = set(model.zones) - keep
    if not dropped:
        return 0
    model.zones = {zid: z for zid, z in model.zones.items() if zid in keep}
    model.paths = [p for p in model.paths
                   if (p.n_from == -1 or p.n_from in keep)
                   and (p.n_to == -1 or p.n_to in keep)]
    model.sources = [s for s in model.sources if s.zone in keep]
    return len(dropped)


def _add_decay(model):
    """Add the NO2 (+CONTA outdoor tracer) first-order decay reaction."""
    if abs(model.decay_of("NO2")) > 1e-12:
        return
    model.reactions.append(
        Reaction(name="NO2->NO", decays={"NO2": NO2_DECAY, "CONTA": NO2_DECAY}))


def _exterior_walls(model):
    """Per zone id, a list of (wind_profile, wazm) from its exterior-wall paths."""
    ext = {}
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None:
            continue
        nm = _norm(el.name)
        # exterior leakage / door element on an ambient-connected path
        is_ext = ("extwall" in nm or "ext_door" in nm or "extdoor" in nm
                  or nm.startswith("ext"))
        if not is_ext:
            continue
        zone = p.n_to if p.n_from == -1 else (p.n_from if p.n_to == -1 else None)
        if zone is None:
            continue
        ext.setdefault(zone, []).append((p.wind_profile, p.wazm))
    return ext


def _next_id(d):
    return (max(d) + 1) if d else 1


def _add_windows(model):
    """Add one operable NFRC window per window-room with an exterior wall."""
    ext = _exterior_walls(model)
    win_elem_id = _next_id(model.elements)
    model.elements[win_elem_id] = FlowElement(
        win_elem_id, WINDOW_TYPE_CODE, "f_cvf_win", "std_win_open",
        list(WINDOW_ELEM_PARAMS))
    pid = _next_id({p.id: p for p in model.paths})
    n = 0
    for zid, z in model.zones.items():
        if not is_window_room(z.name) or zid not in ext:
            continue
        wprof, wazm = ext[zid][0]   # use the first exterior wall of this room
        model.paths.append(FlowPath(
            id=pid, n_from=-1, n_to=zid, element=win_elem_id,
            relHt=WINDOW_RELHT, mult=1.0, wind_profile=wprof,
            wPmod=0.0, wazm=wazm, sched=Macro("WINDOW", 1)))
        pid += 1
        n += 1
    return n


def _interior_doorways(model):
    """Unordered pairs of conditioned zones joined by an interior doorway path."""
    pairs = set()
    for p in model.paths:
        if p.n_from == -1 or p.n_to == -1 or p.n_from == p.n_to:
            continue
        za, zb = model.zones.get(p.n_from), model.zones.get(p.n_to)
        if za is None or zb is None:
            continue
        if not (is_living(za.name) and is_living(zb.name)):
            continue
        el = model.elements.get(p.element)
        if el is None:
            continue
        nm = _norm(el.name)
        is_passage = (any(x in nm for x in PASSAGE)
                      and not any(x in nm for x in NON_PASSAGE))
        if not is_passage:
            continue
        pairs.add(tuple(sorted((p.n_from, p.n_to))))
    return pairs


def _fan_element_id(model):
    """Reuse the model's fan_cvf element, or create the standard one."""
    for eid, el in model.elements.items():
        if el.type_code == FAN_TYPE_CODE and "fan_cvf" in _norm(el.name):
            return eid
    eid = _next_id(model.elements)
    model.elements[eid] = FlowElement(
        eid, FAN_TYPE_CODE, "fan_cvf", "ConstantVolFlow", list(FAN_ELEM_PARAMS))
    return eid


def _add_fan_pairs(model, pairs):
    """Add a bidirectional mixing fan-pair (two opposed fan paths, mirroring
    how the modified homes encode them) for each given zone pair."""
    pairs = sorted(pairs)
    if not pairs:
        return 0
    fan_elem_id = _fan_element_id(model)
    pid = _next_id({p.id: p for p in model.paths})
    for a, b in pairs:
        for (nf, nt) in ((a, b), (b, a)):
            model.paths.append(FlowPath(
                id=pid, n_from=nf, n_to=nt, element=fan_elem_id,
                relHt=1.219, mult=1.0, wind_profile=0, wPmod=0.0, wazm=0.0,
                sched=0.0))
            pid += 1
    return len(pairs)


def _add_mixing_fans(model):
    """Add mixing fan-pairs on every interior doorway of a raw home."""
    return _add_fan_pairs(model, _interior_doorways(model))


def existing_fan_pairs(model):
    """Unordered interior zone pairs already joined by a constant-flow fan."""
    pairs = set()
    for p in model.paths:
        el = model.elements.get(p.element)
        if (el is not None and el.type_code in (29, FAN_TYPE_CODE)
                and p.n_from != -1 and p.n_to != -1):
            pairs.add(tuple(sorted((p.n_from, p.n_to))))
    return pairs


def ensure_standard_mixing(model, extra_pairs=None):
    """Add standard doorway mixing wherever it is missing.

    Doorway candidates = the model's own passage geometry plus `extra_pairs`
    (e.g. recovered from a home's raw NIST twin when the modified file deleted
    the door paths). Makes every home carry the fan mixing — several shipped
    Sci. Adv. files lack it (AH-21/DH-7/APT-4 entirely; APT-3 mixed only the
    one modeled unit). The airflow solver additionally normalizes every pair's
    flow to the standard value, so this only needs to create the CONNECTIONS."""
    want = set(_interior_doorways(model)) | set(extra_pairs or ())
    missing = {(a, b) for (a, b) in want
               if a in model.zones and b in model.zones} - existing_fan_pairs(model)
    return _add_fan_pairs(model, missing)


MECH_VENT_MIN = 0.005   # kg/s; below this net flow an AHS is treated as recirculating


def _mechanical_ventilation(model):
    """Per-room net mechanical exhaust to outdoor (kg/s), from exhaust AHS.

    Each simple AHS pulls room air into its return node (zr) and pushes supply
    air from its supply node (zs) to rooms (`Fahs` on the f=8 paths). A
    recirculating system has return≈supply (net ~0 outdoor air — pure mixing,
    already covered by the interzone doorway fans). An exhaust-only system
    (kitchen/bath fans: return >> supply) nets a mechanical exhaust to outdoor,
    with makeup drawn through the envelope. We return that net exhaust per room
    so the airflow solver can add it as mechanical ventilation. Must be called
    BEFORE _drop_phantom_zones (which deletes the AHS nodes + their paths)."""
    if not model.ahs:
        return {}
    nodes = set()
    for a in model.ahs.values():
        nodes.update((a.zr, a.zs))
    extract = {}
    for aid, a in model.ahs.items():
        ret, sup = {}, 0.0
        for p in model.paths:
            if p.flag != 8 or p.ahs != aid:
                continue
            if a.zr in (p.n_from, p.n_to):                 # return: room -> zr
                room = p.n_to if p.n_from == a.zr else p.n_from
                if room != -1 and room not in nodes:
                    ret[room] = ret.get(room, 0.0) + p.fahs
            elif a.zs in (p.n_from, p.n_to):               # supply: zs -> room
                sup += p.fahs
        ret_total = sum(ret.values())
        net = ret_total - sup
        if net <= MECH_VENT_MIN or ret_total <= 0:
            continue                                       # recirculating / negligible
        for room, r in ret.items():
            extract[room] = extract.get(room, 0.0) + net * (r / ret_total)
    return extract


def apply_modifications(model, *, verbose=False, extra_doorways=None):
    """Mutate `model` in place to the Sci. Adv. modified form; return it.

    On already-modified homes: phantom-zone cleanup + doorway-mixing top-up
    (uniform mixing policy — every home gets the standard fan mixing even
    where the shipped file lacks it; `extra_doorways` supplies pairs recovered
    from a raw twin). The existing 24 were calibrated with the AHS ignored, so
    we do NOT add mechanical ventilation to them — only to the raw NIST homes."""
    if already_modified(model):
        dropped = _drop_phantom_zones(model)
        model.mixing_added = ensure_standard_mixing(model, extra_doorways)
        if verbose:
            print(f"  already modified; dropped {dropped} phantom zone(s), "
                  f"added {model.mixing_added} missing doorway pair(s)")
        return model
    model.mech_extract = _mechanical_ventilation(model)   # before dropping AHS nodes
    dropped = _drop_phantom_zones(model)
    _add_decay(model)
    nwin = _add_windows(model)
    nfan = _add_mixing_fans(model)
    if verbose:
        mv = sum(model.mech_extract.values())
        print(f"  +decay  +{nwin} window(s)  +{nfan} mixing-doorway(s)  "
              f"-{dropped} phantom zone(s)  mech-vent {mv:.3f} kg/s")
    return model
