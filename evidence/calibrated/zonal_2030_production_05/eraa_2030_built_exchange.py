"""Apply the independently published Croatian exchange envelope to the zonal LP."""
import csv
from hashlib import sha256
import io
import json
from math import fsum, isfinite


HOURS = tuple(range(1, 8761))
TOL = 1e-7
BOUNDARIES = {"SI": "slovenia", "BA": "bosnia", "outside": "outside_hungary_serbia"}
DIRECTIONS = ("in", "out")


def load_envelope(raw, manifest):
    meta = json.loads(manifest)
    if sha256(raw).hexdigest() != meta["candidate_sha256"] or meta["hours"] != len(HOURS):
        raise ValueError("Wrong 2030 Croatian exchange input")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if tuple(int(row["period"]) for row in rows) != HOURS:
        raise ValueError("Missing or misordered 2030 exchange hours")
    columns = {f"{prefix}_{d}_mw" for prefix in (*BOUNDARIES.values(), "croatia_gross", "croatia_net")
               for d in DIRECTIONS}
    return {p: {name: float(row[name]) for name in columns} for p, row in zip(HOURS, rows)}


def apply_fixed_native_transfer_inputs(ctx, hourly):
    """Set the common 2030 link infrastructure before native row construction."""
    if (tuple(ctx["years"]) != (2030,) or hourly.keys() != set(HOURS)
            or ctx["include_secc_links"]):
        raise ValueError("Expected a fresh 2030 electricity portfolio or frozen stress build")
    updates = {}
    primary = ctx["transmission_capx"]
    new_primary = ctx["transmission_max_cap_periodx"]
    for system in ("SI", "BA"):
        matches = [key for key in ctx["primary_links"]
                   if key[0] in ctx["transmission_units_el"] and key[2] == "HR" and key[4] == system]
        if len(matches) != 1:
            raise ValueError(f"Expected one mapped Croatian corridor to {system}")
        unit, z, hr, other_z, other = matches[0]
        mirror = unit, other_z, other, z, hr
        values = {row[f"{BOUNDARIES[system]}_{d}_mw"] for row in hourly.values() for d in DIRECTIONS}
        if (len(values) != 1 or mirror not in ctx["primary_links"]
                or any(key not in primary or key not in new_primary for key in (matches[0], mirror))):
            raise ValueError(f"The native symmetric corridor cannot represent {system}")
        capacity, = values
        updates[matches[0]] = capacity
        updates[mirror] = capacity

    external = [key for key in ctx["outside_links"]
                if key[0] in ctx["transmission_outside_units_el"] and key[2] == "HR"]
    if len(external) != 1:
        raise ValueError("Expected one common Croatia outside corridor")
    key = external[0]
    outside = {
        name: ctx[name] for name in ("transmission_outside_cap", "transmission_outside_max_cap",
                                     "transmission_outside_max_cap_period")
    }
    if any(key not in mapping for mapping in outside.values()):
        raise ValueError("The Croatia outside corridor has incomplete native capacity inputs")
    capacity = max(row[f"outside_hungary_serbia_{d}_mw"] for row in hourly.values() for d in DIRECTIONS)
    if any(not isfinite(value) or value <= 0 for value in (*updates.values(), capacity)):
        raise ValueError("Invalid published Croatia corridor rating")
    if ctx["Myopic"]:
        for link in updates:
            if any(ctx["transfer_cap_prev"][(*link, y)] != 0. for y in ctx["previous_years_list"] if y <= 2030):
                raise ValueError(f"Croatian corridor has an unexpected prior expansion: {link}")
        if any(ctx["transfer_cap_outside_prev"][(*key, y)] != 0. for y in ctx["previous_years_list"] if y <= 2030):
            raise ValueError("Croatia outside corridor has an unexpected prior expansion")

    for link, value in updates.items():
        primary[link] = value
        new_primary[link] = 0.
    outside["transmission_outside_cap"][key] = capacity
    outside["transmission_outside_max_cap"][key] = capacity
    outside["transmission_outside_max_cap_period"][key] = 0.
    ramp = dict(ctx.get("transmission_outside_ramp") or {})
    ramp[key] = 1.
    ctx["transmission_outside_ramp"] = ramp
    return updates, {key: capacity}


def audit_built_native_transfer_inputs(ctx, fixed):
    """Check the actual equations so an older native cap cannot undercut the envelope."""
    model = ctx["ucp"]
    model.update()
    fallback = 0
    for inventory, caps, additions, existing_rows, new_rows in (
        (fixed[0], "transfer_cap", "transfer_cap_new", "max_transfercap", "max_transfercap_emaxperiod"),
        (fixed[1], "transfer_cap_outside", "transfer_cap_outside_new",
         "max_transfercap_outside", "max_transfercap_outsidemaxperiod"),
    ):
        for link, amount in inventory.items():
            unit, *location = link
            idx = (unit, *location, 2030)
            key = (unit, 2030, *location)
            existing = ctx[existing_rows][idx]
            cap = ctx[caps][key]
            added = ctx[additions][key]
            candidates = ctx[new_rows]
            new = candidates.get(idx)
            if new is None:
                # addConstrs can return fewer keys than the constraints it installed.
                fallback += 1
                column = model.getCol(added)
                rows = [column.getConstr(i) for i in range(column.size())]
                matches = [r for r in rows if r.Sense == "<" and abs(r.RHS) <= TOL
                           and model.getRow(r).size() == 1 and model.getCoeff(r, added) == 1.]
                if not matches:
                    evidence = [(r.ConstrName, r.Sense, r.RHS, model.getRow(r).size(),
                                 model.getCoeff(r, added)) for r in rows[:12]]
                    raise ValueError(f"No zero investment-limit row for {link}: {evidence}")
                new = matches[0]
            if (existing.Sense != "=" or abs(existing.RHS - amount) > TOL
                    or model.getRow(existing).size() != 2 or model.getCoeff(existing, cap) != 1.
                    or model.getCoeff(existing, added) != -1. or new.Sense != "<"
                    or added.LB != 0. or abs(new.RHS) > TOL or model.getRow(new).size() != 1
                    or model.getCoeff(new, added) != 1.):
                raise ValueError(f"Native transfer capacity differs for {link}")
    return dict(primary_ratings=len(fixed[0]), outside_ratings=len(fixed[1]),
                rows_verified_from_native_columns=fallback)


def install_exchange_envelope(ctx, hourly, fraction):
    """Use each HR-side directional variable once, without changing other borders."""
    if (isinstance(fraction, bool) or fraction not in (0., .5, 1.)
            or tuple(ctx["years"]) != (2030,) or tuple(ctx["periods"]) != HOURS
            or hourly.keys() != set(HOURS) or ctx["include_secc_links"]
            or ctx["model_efuel"] or ctx["model_H2_transmission"]):
        raise ValueError("Expected the fixed 2030 electricity-only Croatian cut")
    groups = {(system, d): [] for system in BOUNDARIES for d in DIRECTIONS}
    model, gp = ctx["ucp"], ctx["gp"]
    model.update()
    for direction, link_name, flow_name in (
        ("in", "primary_links", "import_zone_system"),
        ("out", "primary_links_export", "export_zone_system"),
    ):
        for unit, zone, system, other_zone, other_system in ctx[link_name]:
            if unit in ctx["transmission_units_el"] and system == "HR" and other_system != "HR":
                key = (other_system, direction)
                if key not in groups:
                    raise ValueError(f"Unexpected primary Croatian connection to {other_system}")
                groups[key].append((ctx[flow_name], unit, zone, system, (other_zone, other_system)))
    for unit, zone, system in ctx["outside_links"]:
        if unit in ctx["transmission_outside_units_el"] and system == "HR":
            groups["outside", "in"].append((ctx["import_outside"], unit, zone, system, ()))
            groups["outside", "out"].append((ctx["export_outside"], unit, zone, system, ()))
    if not all(groups.values()):
        raise ValueError("An expected Croatian border direction is absent")

    prepared = []
    for period in HOURS:
        member_flows = {"in": [], "out": []}
        unique = set()
        limits = hourly[period]
        for (neighbor, direction), specs in groups.items():
            variables = []
            for lookup, unit, zone, system, tail in specs:
                var = lookup[(unit, 2030, period, zone, system, *tail)]
                balance = ctx["energy_balance"][zone, system, period, 2030]
                coeff = model.getCoeff(balance, var)
                correct_sign = 0. < coeff <= 1. + TOL if direction == "in" else abs(coeff + 1.) <= TOL
                if var.index in unique or var.LB < -TOL or not correct_sign:
                    raise ValueError(f"Invalid or repeated Croatian flow for {period}/{neighbor}/{direction}")
                unique.add(var.index)
                variables.append(var)
            member_flows[direction].extend(variables)
            name = f"{BOUNDARIES[neighbor]}_{direction}"
            prepared.append((period, name, variables, (), limits[f"{name}_mw"]))
        for direction, opposite in (("in", "out"), ("out", "in")):
            gross = f"croatia_gross_{direction}"
            net = f"croatia_net_{direction}"
            prepared.append((period, gross, member_flows[direction], (), limits[f"{gross}_mw"]))
            prepared.append((period, net, member_flows[direction], member_flows[opposite], limits[f"{net}_mw"]))
    if any(not isfinite(limit) or limit < 0 for _, _, _, _, limit in prepared):
        raise ValueError("Nonfinite or negative Croatian exchange limit")

    checked = []
    for period, name, positive, negative, limit in prepared:
        row = model.addConstr(gp.quicksum(positive) - gp.quicksum(negative) <= fraction * limit,
                              name=f"eraa2030_{name}[{period}]")
        checked.append((row, positive, negative, fraction * limit))
    model.update()
    return checked


def audit_exchange_envelope(model, installed, *, solved=False):
    if len(installed) != 10 * len(HOURS):
        raise ValueError("Incomplete Croatian exchange rows")
    worst = 0.
    for row, positive, negative, limit in installed:
        actual = model.getRow(row)
        expected = {var.index: 1. for var in positive}
        expected.update({var.index: -1. for var in negative})
        if (row.Sense != "<" or abs(row.RHS - limit) > TOL
                or {actual.getVar(i).index: actual.getCoeff(i) for i in range(actual.size())} != expected):
            raise ValueError(f"Croatian envelope differs from source in {row.ConstrName}")
        if solved:
            violation = fsum(v.X for v in positive) - fsum(v.X for v in negative) - limit
            if not isfinite(violation) or violation > TOL:
                raise ValueError(f"Solved exchange exceeds the approved bound in {row.ConstrName}")
            worst = max(worst, violation)
    return dict(rows=len(installed), solved=solved, worst_solved_excess_mw=worst if solved else None)
