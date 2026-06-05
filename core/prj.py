"""Parser for NIST CONTAM project (.prj) files.

Extracts the pieces needed to re-implement the multizone physics: zones,
airflow elements + paths, contaminant species, first-order decay (kinetic
reactions), wind-pressure profiles, and contaminant source/sinks. Scenario
macros such as $(TEMP 1) / $(WIND 1) / $(WINDOW 1) / $(USE 1) / $(HOOD 1) are
preserved symbolically as Macro objects so a driver can substitute physical
values per scenario.

This is a structural parser only; element equations live in the solver.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# --- symbolic scenario macro, e.g. $(TEMP 1) ---
@dataclass(frozen=True)
class Macro:
    name: str
    index: int

    def __str__(self):
        return f"$({self.name} {self.index})"


_MACRO_RE = re.compile(r"\$\(\s*([A-Za-z_]+)\s+(\d+)\s*\)")
# Tokenize a PRJ line keeping $(NAME idx) macros (which contain a space) whole.
_TOKEN_RE = re.compile(r"\$\([^)]*\)|\S+")


def _tokens(line):
    return _TOKEN_RE.findall(line)


def parse_token(tok):
    """Return float, Macro, or the raw string for a single PRJ token."""
    m = _MACRO_RE.fullmatch(tok)
    if m:
        return Macro(m.group(1), int(m.group(2)))
    try:
        return float(tok)
    except ValueError:
        return tok


# --- data classes -----------------------------------------------------------
@dataclass
class Species:
    id: int
    name: str
    molwt: float
    decay: float          # 1/s (from the species table; usually 0 here)
    ccdef: float          # default/ambient mass fraction (kg/kg)


@dataclass
class Level:
    id: int
    refHt: float          # m, elevation of the level floor
    delHt: float          # m, level (storey) height
    name: str


@dataclass
class Zone:
    id: int
    volume: float         # m^3
    T0: float             # K
    name: str
    level: int = 0        # level/storey id (l# field)


@dataclass
class FlowElement:
    id: int
    type_code: int        # CONTAM element type (23 leak, 25 orifice, 27 door, 31 fan…)
    type_name: str        # plr_orfc, plr_leak1/2/3, dor_door, fan_cvf, …
    name: str
    params: list          # raw numeric params (floats / Macros)


@dataclass
class FlowPath:
    id: int
    n_from: int           # zone id, or -1 = ambient
    n_to: int             # zone id, or -1 = ambient
    element: int          # FlowElement id
    relHt: float          # m, height of the path
    mult: float           # element multiplier
    wind_profile: int     # wind-pressure-profile id (w#), 0 = none
    wPmod: float          # wind pressure modifier
    wazm: float           # wall azimuth (deg)
    sched: object         # s# field: schedule/control (often a Macro: WINDOW/TEMP)
    flag: int = 0         # path flag (f); 8 = simple-AHS supply/return path
    ahs: int = 0          # a# field: simple-AHS id this path belongs to (0 = none)
    fahs: float = 0.0     # Fahs field: AHS supply/return airflow on this path (kg/s)


@dataclass
class AHS:
    id: int
    zr: int               # return-node zone id
    zs: int               # supply-node zone id
    name: str


@dataclass
class SourceElement:
    id: int
    species: str
    type_name: str        # ccf = constant coefficient, …
    name: str
    rate: object          # generation rate (kg/s) or Macro


@dataclass
class Source:
    id: int
    zone: int
    element: int          # SourceElement id
    sched: object         # s# control schedule (often $(USE n))
    mult: object          # multiplier (often $(HOOD n) capture factor)


@dataclass
class WindProfile:
    id: int
    name: str
    points: list          # [(angle_deg, Cp), …]


@dataclass
class Reaction:
    name: str
    decays: dict          # species_name -> first-order rate (1/s, negative)


@dataclass
class PrjModel:
    path: str
    ambient_T: object              # K or Macro  ($(TEMP 1))
    ambient_wind: object           # m/s or Macro ($(WIND 1))
    ambient_P: float               # Pa
    species: dict = field(default_factory=dict)        # name -> Species
    zones: dict = field(default_factory=dict)          # id -> Zone
    levels: dict = field(default_factory=dict)         # id -> Level
    elements: dict = field(default_factory=dict)       # id -> FlowElement
    paths: list = field(default_factory=list)          # [FlowPath]
    source_elements: dict = field(default_factory=dict)  # id -> SourceElement
    sources: list = field(default_factory=list)        # [Source]
    wind_profiles: dict = field(default_factory=dict)  # id -> WindProfile
    reactions: list = field(default_factory=list)      # [Reaction]
    ahs: dict = field(default_factory=dict)            # id -> AHS (simple air handlers)
    mech_extract: dict = field(default_factory=dict)   # zone id -> mechanical exhaust (kg/s)

    def decay_of(self, species_name):
        """First-order decay rate (1/s) for a species, from reactions + table."""
        rate = self.species[species_name].decay if species_name in self.species else 0.0
        for rxn in self.reactions:
            rate += rxn.decays.get(species_name, 0.0)
        return rate

    def zone_height(self, zone_id):
        """Storey height (m) for a zone, from its level (default 2.5 m)."""
        z = self.zones.get(zone_id)
        lv = self.levels.get(z.level) if z else None
        return lv.delHt if (lv and lv.delHt > 0) else 2.5

    def floor_area_m2(self, exclude=("attic", "crawl", "garage", "basement",
                                     "plenum", "shaft", "(ret)", "(sup)",
                                     "(rec)", "(exh)", "ahs", "duct", "soil")):
        """Conditioned living floor area (m^2): sum of zone volume / storey
        height over occupiable zones (excludes attics, crawlspaces, garages,
        and HVAC/AHS phantom zones)."""
        total = 0.0
        for zid, z in self.zones.items():
            nm = (z.name or "").lower()
            if z.volume <= 0 or any(x in nm for x in exclude):
                continue
            total += z.volume / self.zone_height(zid)
        return total


# --- section helpers --------------------------------------------------------
_SECTION_RE = re.compile(r"^\s*(\d+)\s*!\s*(.+?):")


def _split_sections(lines):
    """Yield (name, count, body_lines) for each 'N ! name:' ... '-999' block."""
    i, n = 0, len(lines)
    while i < n:
        m = _SECTION_RE.match(lines[i])
        if not m:
            i += 1
            continue
        count, name = int(m.group(1)), m.group(2).strip()
        body, i = [], i + 1
        # Body runs until -999 OR the next section header (some sections, e.g.
        # "contaminants:", are a count + inline list with no -999 terminator).
        while i < n and lines[i].strip() != "-999" and not _SECTION_RE.match(lines[i]):
            body.append(lines[i])
            i += 1
        if i < n and lines[i].strip() == "-999":
            i += 1  # consume the terminator; leave a header for the next iteration
        yield name, count, body


def _data_lines(body):
    """Non-comment, non-empty lines from a section body."""
    return [ln for ln in body if ln.strip() and not ln.lstrip().startswith("!")]


# --- main parse -------------------------------------------------------------
def parse_prj(path):
    """Parse a .prj file from disk."""
    with open(path, errors="ignore") as f:
        return parse_prj_text(f.read(), label=str(path))


def parse_prj_text(text, label="<uploaded>"):
    """Parse a .prj from its text content (e.g. an uploaded file)."""
    lines = text.splitlines()
    model = PrjModel(path=label, ambient_T=None, ambient_wind=None, ambient_P=101325.0)

    # Ambient: the data line following the "! Ta Pb Ws Wd ..." comment.
    for idx, ln in enumerate(lines):
        if ln.lstrip().startswith("! Ta") and idx + 1 < len(lines):
            f = _tokens(lines[idx + 1])
            model.ambient_T = parse_token(f[0])
            model.ambient_P = parse_token(f[1]) if isinstance(parse_token(f[1]), float) else 101325.0
            model.ambient_wind = parse_token(f[2])
            break

    for name, count, body in _split_sections(lines):
        if name == "species":
            _parse_species(model, body)
        elif name == "kinetic reactions":
            _parse_reactions(model, body)
        elif name == "wind pressure profiles":
            _parse_wind_profiles(model, body)
        elif name == "source/sink elements":
            _parse_source_elements(model, body)
        elif name == "flow elements":
            _parse_flow_elements(model, body)
        elif name.startswith("levels"):
            _parse_levels(model, body)
        elif name == "zones":
            _parse_zones(model, body)
        elif name == "flow paths":
            _parse_paths(model, body)
        elif name == "simple AHS":
            _parse_ahs(model, body)
        elif name == "source/sinks":
            _parse_sources(model, body)
    return model


def _parse_species(model, body):
    for ln in _data_lines(body):
        f = ln.split()
        # # s t molwt mdiam edens decay Dm CCdef Cp Kuv u[5] name
        sp = Species(id=int(f[0]), name=f[-1], molwt=float(f[3]),
                     decay=float(f[6]), ccdef=float(f[8]))
        model.species[sp.name] = sp


def _parse_reactions(model, body):
    # Each reaction: header line "id type name", then "SPECIES SPECIES rate" lines.
    cur = None
    for ln in body:
        s = ln.strip()
        if not s:
            continue
        f = s.split()
        # a decay line looks like: <species> <species> <float>
        if len(f) == 3 and f[0] in model.species and f[1] in model.species:
            try:
                rate = float(f[2])
                cur.decays[f[0]] = rate
                continue
            except ValueError:
                pass
        # otherwise it's a reaction header
        cur = Reaction(name=" ".join(f[2:]) if len(f) > 2 else s, decays={})
        model.reactions.append(cur)


def _parse_wind_profiles(model, body):
    i = 0
    while i < len(body):
        s = body[i].strip()
        if not s:
            i += 1
            continue
        f = s.split()
        pid, npts = int(f[0]), int(f[1])
        wname = " ".join(f[3:]) if len(f) > 3 else f"wpp{pid}"
        i += 2  # header + description line
        pts = []
        for _ in range(npts):
            while i < len(body) and not body[i].strip():
                i += 1
            pf = body[i].split()
            pts.append((float(pf[0]), float(pf[1])))
            i += 1
        model.wind_profiles[pid] = WindProfile(pid, wname, pts)


def _parse_source_elements(model, body):
    # Per element: "id species type name" / description / params line.
    i = 0
    while i < len(body):
        s = body[i].strip()
        if not s:
            i += 1
            continue
        f = s.split()
        if not f[0].isdigit():
            i += 1
            continue
        eid, species, type_name = int(f[0]), f[1], f[2]
        name = " ".join(f[3:])
        i += 2  # header + description
        while i < len(body) and not body[i].strip():
            i += 1
        params = [parse_token(t) for t in _tokens(body[i])]
        model.source_elements[eid] = SourceElement(eid, species, type_name, name, params[0])
        i += 1


def _parse_flow_elements(model, body):
    i = 0
    while i < len(body):
        s = body[i].strip()
        if not s:
            i += 1
            continue
        f = s.split()
        if not f[0].isdigit():
            i += 1
            continue
        eid, type_code, type_name = int(f[0]), int(f[1]), f[2]
        name = " ".join(f[3:])
        i += 2  # header + description
        while i < len(body) and not body[i].strip():
            i += 1
        params = [parse_token(t) for t in _tokens(body[i])]
        model.elements[eid] = FlowElement(eid, type_code, type_name, name, params)
        i += 1


def _parse_levels(model, body):
    # "idx refHt delHt ni u showGeom name" headers, interleaved with all-numeric
    # icon-data lines (which we skip: they have no non-numeric name field).
    for ln in body:
        s = ln.strip()
        if not s or s.startswith("!"):
            continue
        f = s.split()
        if len(f) < 7 or not _isfloat(f[1]) or not _isfloat(f[2]) or _isfloat(f[6]):
            continue
        try:
            model.levels[int(f[0])] = Level(int(f[0]), float(f[1]), float(f[2]),
                                            " ".join(f[6:]))
        except ValueError:
            continue


def _parse_zones(model, body):
    for ln in _data_lines(body):
        f = ln.split()
        # Z# f s# c# k# l# relHt Vol T0 P0 name ...
        z = Zone(id=int(f[0]), volume=float(f[7]), T0=float(f[8]), name=f[10],
                 level=int(f[5]))
        model.zones[z.id] = z


def _parse_paths(model, body):
    for ln in _data_lines(body):
        f = _tokens(ln)
        # P# f n# m# e# f# w# a# s# c# l# X Y relHt mult wPset wPmod wazm Fahs ...
        model.paths.append(FlowPath(
            id=int(f[0]), n_from=int(f[2]), n_to=int(f[3]), element=int(f[4]),
            wind_profile=int(f[6]), sched=parse_token(f[8]),
            relHt=float(f[13]), mult=float(f[14]),
            wPmod=float(f[16]) if _isfloat(f[16]) else 0.0,
            wazm=float(f[17]) if _isfloat(f[17]) else 0.0,
            flag=int(f[1]) if _isfloat(f[1]) else 0,
            ahs=int(f[7]) if _isfloat(f[7]) else 0,
            fahs=float(f[18]) if len(f) > 18 and _isfloat(f[18]) else 0.0,
        ))


def _parse_ahs(model, body):
    # "# zr# zs# pr# ps# px# u name" — zr/zs are the return/supply node zone ids.
    for ln in _data_lines(body):
        f = ln.split()
        if not f[0].isdigit() or len(f) < 4:
            continue
        model.ahs[int(f[0])] = AHS(id=int(f[0]), zr=int(f[1]), zs=int(f[2]),
                                   name=f[-1])


def _parse_sources(model, body):
    for ln in _data_lines(body):
        f = _tokens(ln)
        # # z# e# s# c# mult CC0 ...
        model.sources.append(Source(
            id=int(f[0]), zone=int(f[1]), element=int(f[2]),
            sched=parse_token(f[3]), mult=parse_token(f[5]),
        ))


def _isfloat(t):
    try:
        float(t)
        return True
    except ValueError:
        return False
