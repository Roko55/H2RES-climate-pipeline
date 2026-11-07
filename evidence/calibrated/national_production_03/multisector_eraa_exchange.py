"""Install a finite, signed national exchange boundary for future multisector windows."""
import csv
from hashlib import sha256
import io
import json
from math import isfinite

from eraa_2030_built_exchange import BOUNDARIES, DIRECTIONS
from r6_trade import render_signed


HOURS = tuple(range(1, 8761))
KRSKO = "NEKrskoHR"
TOL = 1e-7
YEARS = (2025, 2030, 2035, 2040, 2045, 2050)


def read_multisector_envelope(raw, manifest):
    meta = json.loads(manifest)
    expected = [(y, p) for y in YEARS for p in HOURS]
    if (meta["hourly_rows"] != len(expected) or sha256(raw).hexdigest() != meta["candidate_sha256"]
            or tuple(map(int, meta["year_map"])) != YEARS):
        raise ValueError("Wrong common ERAA multisector envelope")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if [(int(row["year"]), int(row["period"])) for row in rows] != expected:
        raise ValueError("Common exchange profile differs from the complete native chronology")
    columns = {f"{prefix}_{d}_mw" for prefix in (*BOUNDARIES.values(), "croatia_gross", "croatia_net")
               for d in DIRECTIONS}
    result = {y: {} for y in YEARS}
    for (year, period), row in zip(expected, rows):
        values = {name: float(row[name]) for name in columns}
        if any(not isfinite(x) or x < 0 for x in values.values()):
            raise ValueError(f"Invalid common exchange limit in {year}/{period}")
        result[year][period] = values
    return result


def render_future_exchange(source):
    """Remove the old positive-import ratio ramp from the isolated future source."""
    return render_signed(source)


def country_limits(row):
    inward = sum(row[key] for key in ("slovenia_in_mw", "bosnia_in_mw", "outside_hungary_serbia_in_mw"))
    outward = sum(row[key] for key in ("slovenia_out_mw", "bosnia_out_mw", "outside_hungary_serbia_out_mw"))
    gross_in = min(inward, row["croatia_gross_in_mw"])
    gross_out = min(outward, row["croatia_gross_out_mw"])
    return gross_in, gross_out, min(gross_in, row["croatia_net_in_mw"]), min(gross_out, row["croatia_net_out_mw"])


def install_country_exchange(ctx, envelope):
    """Allocate Krško to the Croatian resource stock and to the physical import cut once."""
    years = tuple(ctx["years"])
    if (not years or min(years) < 2025 or tuple(ctx["periods"]) != HOURS
            or set(envelope) != set(years) or ctx["rps_inv"] or KRSKO not in ctx["units"]):
        raise ValueError("Expected the full future national power balance and Croatian Krško share")
    model = ctx["ucp"]
    model.update()
    prepared = []
    for year in years:
        if envelope[year].keys() != set(HOURS):
            raise ValueError(f"Missing exchange hour in {year}")
        for period in HOURS:
            key = period, year
            flow = ctx["imports"][key]
            krsko = ctx["generation"][KRSKO, period, year]
            balance = ctx["meet_demand"][key]
            old = ctx["import_lim"][key]
            source = envelope[year][period]
            gross_in, gross_out, net_in, net_out = country_limits(source)
            slovenia = min(gross_in, source["slovenia_in_mw"])
            if (not all(isfinite(x) and x >= 0 for x in (gross_in, gross_out, net_in, net_out, slovenia))
                    or old.Sense != "<" or model.getRow(old).size() != 1
                    or model.getCoeff(old, flow) != 1.
                    or model.getCoeff(balance, flow) != 1.
                    or model.getCoeff(balance, krsko) != 1.):
                raise ValueError(f"Unexpected native or published exchange at {key}")
            prepared.append((key, flow, krsko, old, gross_in, gross_out, net_in, net_out, slovenia))

    for (period, year), flow, krsko, old, gross_in, gross_out, net_in, net_out, slovenia in prepared:
        flow.LB, flow.UB = -gross_out, gross_in
        old.RHS = gross_in
        model.addConstr(krsko <= slovenia, name=f"hr_gross_krsko_in[{period},{year}]")
        model.addConstr(flow + krsko <= net_in, name=f"hr_net_power_in[{period},{year}]")
        model.addConstr(flow + krsko >= -net_out, name=f"hr_net_power_out[{period},{year}]")
    model.update()
    return prepared


def audit_solved_country_exchange(prepared):
    """Verify solved electricity flows, including the embedded Slovenian import."""
    peak_net_in = peak_net_out = max_error = 0.
    for key, flow, krsko, _, gross_in, gross_out, net_in, net_out, slovenia in prepared:
        own_share = krsko.X
        other = flow.X
        physical_net = other + own_share
        errors = (-own_share, own_share - slovenia, max(other, 0.) + own_share - gross_in, -other - gross_out,
                  physical_net - net_in, -physical_net - net_out)
        if not all(isfinite(x) and x <= TOL for x in errors):
            raise ValueError(f"Solved Croatian exchange exceeds its physical envelope at {key}")
        max_error = max(max_error, *errors)
        peak_net_in = max(peak_net_in, physical_net)
        peak_net_out = max(peak_net_out, -physical_net)
    return dict(hours=len(prepared), peak_physical_net_import_mw=peak_net_in,
                peak_physical_net_export_mw=peak_net_out, max_excess_mw=max_error)
