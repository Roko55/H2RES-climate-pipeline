"""Bind the checked EV and existing household-cooling schedule to a native window."""
import csv
import gzip
from hashlib import sha256
import io
import json
from math import fsum, isfinite
from pathlib import Path

from future_demand_physical_gate import check_built_fixed_ev, check_demand_window


DATA = Path("data/planning_demand")
MANIFEST = DATA / "preflight.json"
HOURLY = DATA / "hourly_by_temperature_member.csv.gz"
HEAT_SPLIT = DATA / "heat_meter_preflight.json"
HEAT_OFFSETS = DATA / "shared_embedded_heat_pump_mwh.csv"
YEARS = (2025, 2030, 2035, 2040, 2045, 2050)
PERIODS = tuple(range(1, 8761))
TOL = 1e-7


def load_schedules(project, frozen, case):
    """Select only the pinned schedule associated with this case in each anchor."""
    raw = {}
    for relative in (MANIFEST, HOURLY):
        path = (project / relative).resolve()
        if project.resolve() not in path.parents:
            raise ValueError("Fixed-demand input escaped the case project")
        data = path.read_bytes()
        if sha256(data).hexdigest() != frozen.get(relative.as_posix()):
            raise ValueError("Fixed-demand input changed: " + str(relative))
        raw[relative] = data
    manifest = json.loads(raw[MANIFEST])
    if (manifest["status"] != "all_input_schedules_checked"
            or sha256(raw[HOURLY]).hexdigest() != manifest["hourly_sha256"]):
        raise ValueError("Fixed-demand archive disagrees with the scenario audit")
    cohort = manifest["checks_by_year"]
    members = {year: cohort[str(year)][case]["temperature_member"] for year in YEARS}
    selected = {year: [] for year in YEARS}
    with gzip.open(io.BytesIO(raw[HOURLY]), "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            year, member, period = (int(row.pop(key)) for key in ("year", "temperature_member", "period"))
            if members.get(year) == member:
                selected[year].append((period, {key: float(value) for key, value in row.items()}))
    schedules = {}
    for year, rows in selected.items():
        if tuple(p for p, _ in rows) != PERIODS:
            raise ValueError(f"Wrong fixed-demand calendar in {year}")
        series = {k: tuple(r[k] for _, r in rows) for k in rows[0][1]}
        limits = cohort[str(year)]["limits"]
        check_demand_window(series, limits)
        schedules[year] = (series, limits)
    return schedules


def close(a, b):
    return isfinite(a) and abs(a - b) <= TOL


def apply_heat_meter_split(project, frozen, schedules):
    """Remove electric heat and pumping references before native end-use loads are added."""
    root = project.resolve()

    def read(relative):
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Missing planning heat meter input: '+str(relative))
        data = path.read_bytes()
        if sha256(data).hexdigest() != frozen.get(relative.as_posix()):
            raise ValueError('Planning heat meter input changed: '+str(relative))
        return data

    manifest = json.loads(read(HEAT_SPLIT))
    raw = read(HEAT_OFFSETS)
    if (manifest.get('status') != 'local_gross_meter_sensitivity' or manifest.get('case_years') != 42
            or sha256(raw).hexdigest() != manifest['outputs_sha256'][HEAT_OFFSETS.name]):
        raise ValueError('Expected the physically checked common gross electricity split')
    anchors = {item['year']:item for item in manifest['anchors']}
    if set(anchors) != set(YEARS):
        raise ValueError('Wrong year range for the gross electricity split')
    rows = {year:[] for year in YEARS}
    for row in csv.DictReader(io.StringIO(raw.decode('utf8'))):
        rows[int(row['year'])].append((int(row['period']),float(row['embedded_heat_electric_mwh']),
                                      float(row['embedded_pumping_mwh'])))
    new = {}
    for year, (series,limits) in schedules.items():
        ledger = rows[year]
        anchor = anchors[year]
        heat, pump = (tuple(r[index] for r in ledger) for index in (1,2))
        if (tuple(p for p,_,_ in ledger) != PERIODS or any(not isfinite(v) or v < 0. for v in (*heat,*pump))
                or not close(fsum(heat),anchor['embedded_heat_electric_mwh'])
                or not close(fsum(pump),anchor['embedded_pump_mwh'])
                or not close(fsum(series['generic_power']),anchor['legacy_power_mwh'])):
            raise ValueError(f'Gross electricity split failed annual closure for {year}')
        residual = tuple(fsum((power,-h,-p)) for power,h,p in zip(series['residual_power'],heat,pump))
        if min(residual) < 0.:
            raise ValueError(f'Gross electricity split exceeds native power for {year}')
        new[year] = (dict(series,residual_power=residual,heat_reference=heat,pumping_reference=pump),limits)
    return new, manifest


def verify_heat_meter_input(ctx, project, frozen, manifest):
    root = project.resolve()
    source = (root/ctx['heat_demand_dat']).resolve()
    expected = manifest['outputs_sha256']['heat_demand_HR_2020_2050_sdewes.csv']
    if (not source.is_relative_to(root) or frozen.get(source.relative_to(root).as_posix()) != expected
            or sha256(source.read_bytes()).hexdigest() != expected):
        raise ValueError('The runtime heat forecast does not match the calibrated meter scope')


def install_inputs(ctx, schedules):
    """Apply after scenario thermal coupling and before Gurobi builds constraints."""
    if (tuple(ctx["periods"]) != PERIODS or not ctx["years"]
            or not set(ctx["years"]) <= set(YEARS) or ctx["ev_sto_min"] != 0
            or "Power" not in ctx["demand_sectors"] or "general_cooling" in ctx["chp_units"]):
        raise ValueError("Unexpected native multisector fixed-demand scope")
    for year in ctx["years"]:
        series, limits = schedules[year]
        if not close(ctx["ev_Grid_eff"], limits["ev_efficiency"]):
            raise ValueError("Native EV charge efficiency differs from the stated schedule")
        for i, period in enumerate(PERIODS):
            original = series["generic_power"][i]
            actual = ctx["demand"]["Power", period, year]
            if not close(actual, original):
                raise ValueError(f"Frozen generic Power differs from the native input in {year}/{period}")
            if not close(ctx["demand"]["Transport", period, year], 0.):
                raise ValueError("Unexpected separate transport electricity in the fixed EV boundary")
            if not isfinite(ctx["cooling_demand"]["general_cooling", period, year]):
                raise ValueError("Invalid native household cooling row")
    originals = {name: ctx[name] for name in (
        "demand", "cooling_demand", "ev_Demand", "ev_sto_max", "ev_Grid_P_max",
        "ev_transp_load", "ev_availability",
    )}
    ctx.update({name: values.copy() for name, values in originals.items()})
    for year in ctx["years"]:
        series, limits = schedules[year]
        ctx["ev_Demand"][year] = fsum(series["ev_driving"])
        ctx["ev_sto_max"][year] = limits["ev_usable_storage_mwh"]
        ctx["ev_Grid_P_max"][year] = limits["ev_connection_mw"]
        for i, period in enumerate(PERIODS):
            key = period, year
            ctx["demand"][("Power", *key)] = fsum((series["residual_power"][i], series["cooling_electric"][i]))
            ctx["ev_transp_load"][key] = series["ev_driving"][i] / ctx["ev_Demand"][year]
            ctx["ev_availability"][key] = series["ev_available"][i]
            ctx["cooling_demand"][("general_cooling", *key)] = 0.
    return originals


def verify_and_fix_built_demand(ctx, schedules):
    """Bind fixed vehicle charging and check the actual native electricity and cooling rows."""
    model = ctx["ucp"]
    model.update()
    audits = {}
    for year in ctx["years"]:
        series, limits = schedules[year]
        start = ctx["ev_storage_soc_0"][year]
        if not close(start.RHS, -series["ev_driving"][0]):
            raise ValueError("Unexpected native initial EV equation")
        start.RHS += limits["ev_initial_mwh"]
        for i, period in enumerate(PERIODS):
            key = period, year
            charge, out, soc = (ctx[name][key] for name in ("ev_sto_in", "ev_sto_out", "ev_sto_soc"))
            charge.LB = charge.UB = series["ev_charge"][i]
            out.LB = out.UB = 0.
            if period == PERIODS[-1]:
                soc.LB = soc.UB = limits["ev_initial_mwh"]
            row = ctx["meet_demand"][key]
            rhs = fsum(ctx["demand"][(sector, *key)] for sector in ctx["demand_sectors"])
            expected_power = fsum((series["residual_power"][i], series["cooling_electric"][i]))
            if (row.Sense != "=" or not close(row.RHS, rhs)
                    or not close(ctx["demand"][("Power", *key)], expected_power)
                    or model.getCoeff(row, charge) != -1.
                    or not close(model.getCoeff(row, out), limits["ev_efficiency"])
                    or model.getCoeff(row, ctx["ResToHeat"][key]) != -1.):
                raise ValueError(f"Fixed EV or cooling electricity differs in {year}/{period}")
            cooling = ctx["meet_cooling_demand_gen"][key]
            units = [ctx["cooling_pump_ind"][(unit, *key)] for unit in ctx["HeatPump_units"]]
            if (cooling.Sense != "=" or cooling.RHS != 0. or model.getRow(cooling).size() != len(units)
                    or any(v.LB < 0. or model.getCoeff(cooling, v) != 1. for v in units)):
                raise ValueError(f"Native household cooling is still counted in {year}/{period}")
        model.update()
        audits[year] = check_built_fixed_ev(ctx, model, year, series, limits)
        audits[year]['excluded_reference_electric_heat_mwh'] = fsum(series.get('heat_reference', ()))
        audits[year]['excluded_reference_pumping_mwh'] = fsum(series.get('pumping_reference', ()))
    return audits


def audit_solved_demand(ctx, schedules):
    """Verify realized EV energy, zero V2G and no second household cooling draw."""
    if ctx["ucp"].Status != 2:
        raise ValueError("Expected an optimal solved fixed-demand window")
    reports = {}
    for year in ctx["years"]:
        series, limits = schedules[year]
        max_error = 0.
        for i, period in enumerate(PERIODS):
            key = period, year
            charge = ctx["ev_sto_in"][key].X
            discharge = ctx["ev_sto_out"][key].X
            household_native = fsum(ctx["cooling_pump_ind"][(unit, *key)].X for unit in ctx["HeatPump_units"])
            values = (charge - series["ev_charge"][i], discharge, household_native)
            if not all(close(v, 0.) for v in values):
                raise ValueError(f"Unexpected solved fixed-demand dispatch in {year}/{period}")
            max_error = max(max_error, *map(abs, values))
        terminal = ctx["ev_sto_soc"][PERIODS[-1], year].X
        if not close(terminal, limits["ev_initial_mwh"]):
            raise ValueError(f"EV energy did not close in {year}")
        reports[year] = dict(hours=len(PERIODS), ev_grid_mwh=fsum(series["ev_charge"]),
                             household_cooling_grid_mwh=fsum(series["cooling_electric"]),
                             ev_terminal_mwh=terminal, max_native_error_mwh=max_error)
    return reports
