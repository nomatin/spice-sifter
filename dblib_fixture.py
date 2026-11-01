# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Minimal SPICE fixture parser for DbLib component tests.

Only the small, explicit subset needed by the SchDoc generator is supported:
V/I/R/C/L elements, the {{DUT}} marker, and OP/AC/TRAN/DC/limited OPTIONS.
Everything else is rejected with a diagnostic instead of being guessed.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

NET_RE = re.compile(r"^[A-Za-z0-9_.+\-]+$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]*$")
NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    r"(?:MEG|MIL|T|G|K|M|U|N|P|F)?$",
    re.IGNORECASE,
)
SUFFIX = {
    "T": 1e12, "G": 1e9, "MEG": 1e6, "K": 1e3, "M": 1e-3,
    "U": 1e-6, "N": 1e-9, "P": 1e-12, "F": 1e-15, "MIL": 25.4e-6,
}
ELEMENT_TYPES = {"V": "V", "I": "I", "R": "R", "C": "C", "L": "L"}
ELEMENT_NAMES = {"V": "VSRC", "I": "ISRC", "R": "Resistor",
                 "C": "Capacitor", "L": "Inductor"}
ANALYSES = ("op", "ac", "tran", "dc")
OPTION_KEYS = {"METHOD", "SPICEREFNODE", "DVCC", "DVDD"}


class FixtureError(Exception):
    def __init__(self, code, message, *, line=None, diagnostics=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = line
        self.diagnostics = diagnostics or {}


@dataclass
class Element:
    type: str
    name: str
    nodes: tuple[str, str]
    value: float | None = None
    ac_magnitude: float | None = None
    ac_phase: float | None = None
    line: str = ""

    def plan_fields(self):
        return [
            self.type,
            self.name,
            self.nodes[0],
            self.nodes[1],
            "" if self.value is None else format_number(self.value),
            "" if self.ac_magnitude is None else format_number(self.ac_magnitude),
            "" if self.ac_phase is None else format_number(self.ac_phase),
        ]


@dataclass
class FixturePlan:
    elements: list[Element] = field(default_factory=list)
    nodes: set[str] = field(default_factory=set)
    dut_marker: int = 0
    analyses: dict[str, dict] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)

    @property
    def has_analysis(self):
        return any(name in self.analyses for name in ANALYSES)


def normalize_net(value):
    text = str(value).strip()
    if not text:
        raise FixtureError("FIXTURE_NET_INVALID", "empty net name")
    if text in ("0", "GND", "gnd", "Gnd"):
        return "GND"
    if not NET_RE.fullmatch(text):
        raise FixtureError(
            "FIXTURE_NET_INVALID",
            "unsupported characters in net name: " + text,
        )
    return text


def format_number(value):
    if value is None:
        return ""
    if abs(value) >= 1e-4 and abs(value) < 1e9:
        text = f"{value:.12g}"
    else:
        text = f"{value:.12e}"
    return text


def parse_number(token, *, line=None):
    token = token.strip()
    if not NUMBER_RE.fullmatch(token):
        raise FixtureError(
            "FIXTURE_VALUE_INVALID",
            "unsupported numeric value: " + token,
            line=line,
        )
    match = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(MEG|MIL|T|G|K|M|U|N|P|F)?",
        token,
        re.IGNORECASE,
    )
    if not match:
        raise FixtureError("FIXTURE_VALUE_INVALID", "invalid value: " + token, line=line)
    number = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    if suffix:
        number *= SUFFIX[suffix]
    if not math.isfinite(number):
        raise FixtureError("FIXTURE_VALUE_INVALID", "non-finite value", line=line)
    return number


def _strip_comment(line):
    # SPICE comment lines start with '*'. Inline ';' comments are accepted.
    if line.lstrip().startswith("*"):
        return ""
    return line.split(";", 1)[0].strip()


def _tokens(line):
    return re.split(r"\s+", line.strip())


def _parse_source(tokens, line):
    # V/I name n+ n- [DC value] [AC mag [phase]] | bare value
    rest = tokens[3:]
    dc = None
    ac_mag = None
    ac_phase = None
    i = 0
    if not rest:
        raise FixtureError("FIXTURE_SOURCE_INVALID",
                           "source has no value", line=line)
    while i < len(rest):
        token = rest[i].upper()
        if token == "DC":
            if i + 1 >= len(rest):
                raise FixtureError("FIXTURE_SOURCE_INVALID",
                                   "DC has no value", line=line)
            dc = parse_number(rest[i + 1], line=line)
            i += 2
        elif token == "AC":
            if i + 1 >= len(rest):
                raise FixtureError("FIXTURE_SOURCE_INVALID",
                                   "AC has no magnitude", line=line)
            ac_mag = parse_number(rest[i + 1], line=line)
            i += 2
            if i < len(rest) and not rest[i].upper() in ("DC", "AC"):
                ac_phase = parse_number(rest[i], line=line)
                i += 1
        elif token in ("SIN", "PULSE", "PWL", "EXP", "SFFM"):
            raise FixtureError(
                "FIXTURE_SOURCE_UNSUPPORTED",
                "only DC and AC sources are supported: " + token,
                line=line,
            )
        else:
            if dc is not None or ac_mag is not None:
                raise FixtureError(
                    "FIXTURE_SOURCE_UNSUPPORTED",
                    "unexpected source token: " + rest[i],
                    line=line,
                )
            dc = parse_number(rest[i], line=line)
            i += 1
    return dc, ac_mag, ac_phase


def _parse_ac(tokens, line):
    if len(tokens) != 5:
        raise FixtureError(
            "FIXTURE_ANALYSIS_INVALID",
            ".AC requires: <DEC|OCT|LIN> <points> <start> <stop>",
            line=line,
        )
    sweep = tokens[1].upper()
    if sweep not in ("DEC", "OCT", "LIN"):
        raise FixtureError("FIXTURE_ANALYSIS_INVALID",
                           "unsupported AC sweep: " + tokens[1], line=line)
    points = parse_number(tokens[2], line=line)
    start = parse_number(tokens[3], line=line)
    stop = parse_number(tokens[4], line=line)
    if points <= 0 or start <= 0 or stop <= 0 or stop <= start:
        raise FixtureError("FIXTURE_ANALYSIS_INVALID",
                           "invalid AC range or point count", line=line)
    return {"sweep": sweep, "points": int(points),
            "start": start, "stop": stop}


def _parse_tran(tokens, line):
    if len(tokens) < 3 or len(tokens) > 5:
        raise FixtureError(
            "FIXTURE_ANALYSIS_INVALID",
            ".TRAN requires: <step> <stop> [start] [maxstep]",
            line=line,
        )
    values = [parse_number(token, line=line) for token in tokens[1:]]
    step, stop = values[0], values[1]
    start = values[2] if len(values) > 2 else 0.0
    maxstep = values[3] if len(values) > 3 else step
    if step <= 0 or stop <= 0 or stop <= start:
        raise FixtureError("FIXTURE_ANALYSIS_INVALID",
                           "invalid TRAN range", line=line)
    return {"step": step, "stop": stop, "start": start, "maxstep": maxstep}


def _parse_dc(tokens, line):
    if len(tokens) != 5:
        raise FixtureError(
            "FIXTURE_ANALYSIS_INVALID",
            ".DC requires: <source> <start> <stop> <step>",
            line=line,
        )
    source = tokens[1]
    start = parse_number(tokens[2], line=line)
    stop = parse_number(tokens[3], line=line)
    step = parse_number(tokens[4], line=line)
    if step == 0 or (stop - start) / step <= 0:
        raise FixtureError("FIXTURE_ANALYSIS_INVALID",
                           "invalid DC sweep direction", line=line)
    return {"source": source, "start": start, "stop": stop, "step": step}


def _parse_options(tokens, line):
    options = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise FixtureError(
                "FIXTURE_OPTIONS_INVALID",
                "options must be KEY=VALUE: " + token,
                line=line,
            )
        key, value = token.split("=", 1)
        key = key.upper()
        if key not in OPTION_KEYS:
            raise FixtureError(
                "FIXTURE_OPTIONS_UNSUPPORTED",
                "unsupported .OPTIONS key: " + key,
                line=line,
            )
        options[key] = value.strip()
    return options


def parse_spice_fixture(text):
    plan = FixturePlan()
    seen_names = set()
    for raw_line in str(text).splitlines():
        line = _strip_comment(raw_line)
        if not line:
            continue
        if "{{DUT}}" in line:
            if line != "{{DUT}}":
                raise FixtureError(
                    "FIXTURE_DUT_MARKER",
                    "{{DUT}} must be on its own line",
                    line=line,
                )
            plan.dut_marker += 1
            continue
        if line.startswith("."):
            tokens = _tokens(line)
            directive = tokens[0].upper()
            if directive == ".OP":
                plan.analyses["op"] = {}
            elif directive == ".AC":
                plan.analyses["ac"] = _parse_ac(tokens, line)
            elif directive == ".TRAN":
                plan.analyses["tran"] = _parse_tran(tokens, line)
            elif directive == ".DC":
                plan.analyses["dc"] = _parse_dc(tokens, line)
            elif directive == ".OPTIONS":
                plan.options.update(_parse_options(tokens, line))
            elif directive == ".END":
                continue
            elif directive in (".PLOT", ".PROBE", ".SAVE"):
                plan.ignored.append(directive)
            else:
                raise FixtureError(
                    "FIXTURE_DIRECTIVE_UNSUPPORTED",
                    "unsupported SPICE directive: " + directive,
                    line=line,
                )
            continue
        tokens = _tokens(line)
        letter = tokens[0][0].upper() if tokens else ""
        if letter in ELEMENT_TYPES:
            if len(tokens) < 4:
                raise FixtureError(
                    "FIXTURE_ELEMENT_INVALID",
                    "element requires name and two nodes",
                    line=line,
                )
            name = tokens[0]
            if not NAME_RE.fullmatch(name):
                raise FixtureError(
                    "FIXTURE_ELEMENT_INVALID",
                    "invalid element name: " + name,
                    line=line,
                )
            if name.casefold() in seen_names:
                raise FixtureError(
                    "FIXTURE_ELEMENT_DUPLICATE",
                    "duplicate element name: " + name,
                    line=line,
                )
            seen_names.add(name.casefold())
            nodes = (normalize_net(tokens[1]), normalize_net(tokens[2]))
            if letter in ("V", "I"):
                value, ac_mag, ac_phase = _parse_source(tokens, line)
            else:
                if len(tokens) != 4:
                    raise FixtureError(
                        "FIXTURE_ELEMENT_UNSUPPORTED",
                        "only a single value is supported for " + letter,
                        line=line,
                    )
                value = parse_number(tokens[3], line=line)
                ac_mag = ac_phase = None
            plan.elements.append(Element(
                letter, name, nodes, value, ac_mag, ac_phase, line
            ))
            plan.nodes.update(nodes)
            continue
        raise FixtureError(
            "FIXTURE_ELEMENT_UNSUPPORTED",
            "unsupported SPICE construct: " + tokens[0],
            line=line,
        )
    if plan.dut_marker != 1:
        raise FixtureError(
            "FIXTURE_DUT_MARKER",
            "exactly one {{DUT}} line is required",
            diagnostics={"found": plan.dut_marker},
        )
    if not plan.elements:
        raise FixtureError("FIXTURE_EMPTY", "fixture has no V/I/R/C/L elements")
    if not plan.has_analysis:
        raise FixtureError(
            "FIXTURE_ANALYSIS_MISSING",
            "fixture requires .OP, .AC, .TRAN or .DC",
        )
    if len(plan.analyses) != 1:
        raise FixtureError(
            "FIXTURE_ANALYSIS_MULTIPLE",
            "exactly one analysis directive is supported per fixture",
            diagnostics={"analyses": sorted(plan.analyses)},
        )
    return plan


def analysis_name(plan):
    return next(iter(plan.analyses))


def validate_pin_map(pins, plan):
    if not isinstance(pins, dict) or not pins:
        raise FixtureError(
            "COMPONENT_PIN_MAP_INVALID",
            "component.pins must be a nonempty object",
        )
    normalized = {}
    seen = set()
    for key, value in pins.items():
        if not isinstance(key, str) or not key.strip():
            raise FixtureError(
                "COMPONENT_PIN_MAP_INVALID",
                "pin designators must be nonempty strings",
            )
        designator = key.strip()
        folded = designator.casefold()
        if folded in seen:
            raise FixtureError(
                "COMPONENT_PIN_AMBIGUOUS",
                "duplicate pin designator in pins: " + designator,
            )
        seen.add(folded)
        if value is None:
            normalized[designator] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise FixtureError(
                "COMPONENT_PIN_MAP_INVALID",
                "pin net must be a nonempty string or null",
                diagnostics={"pin": designator},
            )
        net = normalize_net(value)
        if net not in plan.nodes:
            raise FixtureError(
                "COMPONENT_PIN_NET_UNKNOWN",
                "pin net is not present in the spice fixture: " + net,
                diagnostics={"pin": designator, "net": net,
                             "fixture_nodes": sorted(plan.nodes)},
            )
        normalized[designator] = net
    return normalized


def pin_map_lines(pins):
    """Serialize the pin map for the DelphiScript side (cp1251-safe)."""
    lines = []
    for designator, net in pins.items():
        if "|" in designator or "\n" in designator:
            raise FixtureError("COMPONENT_PIN_MAP_INVALID",
                               "pin designator contains a forbidden character")
        text = "<NULL>" if net is None else net
        if "|" in text or "\n" in text:
            raise FixtureError("COMPONENT_PIN_MAP_INVALID",
                               "net contains a forbidden character")
        lines.append(designator + "|" + text)
    return lines


def element_plan_lines(plan):
    lines = []
    for element in plan.elements:
        fields = element.plan_fields()
        if any("|" in field or "\n" in field for field in fields):
            raise FixtureError("FIXTURE_VALUE_INVALID",
                               "fixture field contains a forbidden character",
                               line=element.line)
        lines.append("|".join(fields))
    return lines


def sim_config_updates(plan):
    """Return the MixedSim config keys implied by the fixture directives."""
    updates = {
        "OP_Analysis": "True" if "op" in plan.analyses else "False",
        "AC_Analysis": "True" if plan.analyses.get("ac") else "False",
        "TRAN_Analysis": "True" if plan.analyses.get("tran") else "False",
        "DC_Analysis": "True" if plan.analyses.get("dc") else "False",
        "TF_Analysis": "False",
        "PZ_Analysis": "False",
        "Noise_Analysis": "False",
        "SP_Analysis": "False",
        "TempSweep_Analysis": "False",
        "ParamSweep_Analysis": "False",
        "MonteCarlo_Analysis": "False",
        "SimViewSetup": "ShowAllSignals",
    }
    ac = plan.analyses.get("ac")
    if ac:
        updates.update({
            "AC_SweepType": ac["sweep"],
            "AC_TestPoints": str(ac["points"]),
            "AC_StartFrequency": format_number(ac["start"]),
            "AC_StopFrequency": format_number(ac["stop"]),
        })
    tran = plan.analyses.get("tran")
    if tran:
        updates.update({
            "TRAN_StartTime": format_number(tran["start"]),
            "TRAN_StopTime": format_number(tran["stop"]),
            "TRAN_StepTime": format_number(tran["step"]),
            "TRAN_MaxStepTime": format_number(tran["maxstep"]),
        })
    dc = plan.analyses.get("dc")
    if dc:
        source = dc["source"]
        element = next(
            (e for e in plan.elements
             if e.name.casefold() == source.casefold() and e.type in ("V", "I")),
            None,
        )
        if element is None:
            raise FixtureError(
                "FIXTURE_DC_SOURCE_INVALID",
                ".DC source must be a V or I element from the fixture: " + source,
            )
        updates.update({
            # Altium's simulated source instance is <SPICE prefix><designator>,
            # e.g. V1 -> VV1 in the generated NSX.
            "DC_PrimarySource": element.type + element.name,
            "DC_PrimaryStart": format_number(dc["start"]),
            "DC_PrimaryStop": format_number(dc["stop"]),
            "DC_PrimaryStep": format_number(dc["step"]),
        })
    if "METHOD" in plan.options:
        updates["OPTION_Method"] = plan.options["METHOD"]
    if "SPICEREFNODE" in plan.options:
        updates["OPTION_SpiceRefNode"] = plan.options["SPICEREFNODE"]
    if "DVCC" in plan.options:
        updates["OPTION_DVCC"] = plan.options["DVCC"]
    if "DVDD" in plan.options:
        updates["OPTION_DVDD"] = plan.options["DVDD"]
    return updates
