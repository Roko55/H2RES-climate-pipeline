#!/usr/bin/env python
"""Single entry point for the H2RES climate/profile workflow.

Active flow:
1. optional preprocessing: fetch climate and asset-layer artifacts
2. optional scenario generation: train/generate XGBoost climate profiles
3. validation and official-interface export
4. optional Gurobi sensitivity run

This module intentionally replaces the previous split between separate runner,
validation and sensitivity scripts. Keep this as the single climate-pipeline
entry point.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "SC"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "climate_pipeline_config.json"

CLIMATE_CHANNELS = ["solar", "wind", "temperature"]
DIAGONAL_SCENARIOS = ["low_yield__cold", "avg_yield__normal", "high_yield__hot"]
RESOURCE_SCENARIOS = ["low_yield__normal", "avg_yield__normal", "high_yield__normal"]
COMBINED_SCENARIOS = [
    "low_yield__cold",
    "low_yield__normal",
    "low_yield__hot",
    "avg_yield__cold",
    "avg_yield__normal",
    "avg_yield__hot",
    "high_yield__cold",
    "high_yield__normal",
    "high_yield__hot",
]


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return _rel(value)
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")


def _check(ok: bool, name: str, message: str, details: dict[str, Any] | None = None, warn: bool = False) -> CheckResult:
    if ok:
        status = "pass"
    else:
        status = "warn" if warn else "fail"
    return CheckResult(name=name, status=status, message=message, details=details or {})


def _check_range(values: np.ndarray, low: float, high: float, name: str, tolerance: float = 1e-9) -> CheckResult:
    arr = np.asarray(values, dtype=float)
    if not np.isfinite(arr).all():
        return CheckResult(name, "fail", "contains NaN or infinite values")
    min_v = float(arr.min())
    max_v = float(arr.max())
    ok = min_v >= low - tolerance and max_v <= high + tolerance
    return _check(
        ok,
        name,
        f"range [{min_v:.6g}, {max_v:.6g}] expected within [{low}, {high}]",
        {"min": min_v, "max": max_v, "expected_min": low, "expected_max": high},
    )


def _artifact_row(name: str, path: Path, role: str, required: bool = True) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "artifact": name,
        "role": role,
        "path": _rel(path),
        "required": bool(required),
        "exists": bool(exists),
        "size_bytes": int(stat.st_size) if stat else None,
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds") if stat else None,
    }


def build_input_manifest(country_code: str) -> pd.DataFrame:
    cc = country_code.upper()
    rows = [
        _artifact_row("synthetic_scenarios", DATA_DIR / "xgb_synthetic_scenarios.npy", "synthetic scenario ensemble"),
        _artifact_row("historical_climate_data", DATA_DIR / f"{cc}_data.parquet", "historical solar/wind/temperature table"),
        _artifact_row("genco_data", DATA_DIR / f"genco_data_{cc}_sdewes.csv", "solver unit and capacity table"),
        _artifact_row("ncre_aval_factor", DATA_DIR / f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv", "legacy availability source"),
        _artifact_row("wind_nuts_profiles", DATA_DIR / f"{cc}_wind_nuts_profiles.parquet", "NUTS3 wind source profiles", False),
        _artifact_row("wind_nuts_transfer", DATA_DIR / f"{cc}_wind_transfer_nuts_to_units.csv", "NUTS3 wind transfer matrix", False),
        _artifact_row("wind_macro_profiles", DATA_DIR / f"{cc}_wind_macro_profiles.parquet", "macro wind source profiles", False),
        _artifact_row("wind_macro_transfer", DATA_DIR / f"{cc}_wind_transfer_macro_to_units.csv", "macro wind transfer matrix", False),
        _artifact_row("solar_macro_profiles", DATA_DIR / f"{cc}_solar_macro_profiles.parquet", "macro solar source profiles", False),
        _artifact_row("solar_macro_transfer", DATA_DIR / f"{cc}_solar_transfer_macro_to_units.csv", "macro solar transfer matrix", False),
        _artifact_row("asset_metadata", DATA_DIR / f"{cc}_asset_layer_metadata.json", "asset consistency metadata", False),
    ]
    return pd.DataFrame(rows)


def _load_scenario_manager():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts import scenario_manager

    return scenario_manager


def _validate_historical_data(country_code: str, config: dict[str, Any]) -> list[CheckResult]:
    path = DATA_DIR / f"{country_code}_data.parquet"
    if not path.exists():
        return [CheckResult("historical_data_load", "fail", f"missing {_rel(path)}")]

    checks: list[CheckResult] = []
    df = pd.read_parquet(path)
    missing = [col for col in CLIMATE_CHANNELS if col not in df.columns]
    checks.append(_check(not missing, "historical_data_columns", "required climate columns are present", {"missing": missing, "columns": list(df.columns)}))

    if not missing:
        cfg = config.get("validation", {})
        cf_min = float(cfg.get("capacity_factor_min", 0.0))
        cf_max = float(cfg.get("capacity_factor_max", 1.0))
        temp_min = float(cfg.get("temperature_min_c", -50.0))
        temp_max = float(cfg.get("temperature_max_c", 60.0))
        checks.append(_check_range(df["solar"].to_numpy(), cf_min, cf_max, "historical_solar_cf_range"))
        checks.append(_check_range(df["wind"].to_numpy(), cf_min, cf_max, "historical_wind_cf_range"))
        checks.append(_check_range(df["temperature"].to_numpy(), temp_min, temp_max, "historical_temperature_range"))

    checks.append(_check(len(df) >= 8760, "historical_data_hour_count", f"{len(df)} rows available", {"rows": int(len(df))}, warn=True))
    return checks


def _validate_genco(country_code: str) -> list[CheckResult]:
    path = DATA_DIR / f"genco_data_{country_code}_sdewes.csv"
    if not path.exists():
        return [CheckResult("genco_data_load", "fail", f"missing {_rel(path)}")]

    checks: list[CheckResult] = []
    df = pd.read_csv(path)
    required = ["unit_name", "cap_mw"]
    missing = [col for col in required if col not in df.columns]
    checks.append(_check(not missing, "genco_data_columns", "required genco columns are present", {"missing": missing, "columns": list(df.columns)}))

    if "cap_mw" in df.columns:
        caps = pd.to_numeric(df["cap_mw"], errors="coerce")
        ok = caps.notna().all() and (caps >= 0).all()
        checks.append(_check(ok, "genco_capacity_values", f"total capacity={float(caps.fillna(0).sum()):.3f} MW", {"total_cap_mw": float(caps.fillna(0).sum())}))
    return checks


def _validate_transfer_matrix(path: Path, name: str) -> list[CheckResult]:
    if not path.exists():
        return [CheckResult(name, "warn", f"optional transfer matrix missing: {_rel(path)}")]
    df = pd.read_csv(path)
    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        return [CheckResult(name, "fail", "no numeric transfer weights found")]
    weights = numeric.to_numpy(dtype=float)
    ok = np.isfinite(weights).all() and (weights >= -1e-12).all()
    return [_check(ok, name, f"{numeric.shape[0]} rows x {numeric.shape[1]} numeric weight columns", {"rows": int(numeric.shape[0]), "columns": int(numeric.shape[1])})]


def _validate_synthetic_scenarios(config: dict[str, Any]) -> list[CheckResult]:
    path = DATA_DIR / "xgb_synthetic_scenarios.npy"
    if not path.exists():
        return [CheckResult("synthetic_scenarios_load", "fail", f"missing {_rel(path)}")]

    scenarios = np.load(path)
    checks = [
        _check(
            scenarios.ndim == 3 and scenarios.shape[1:] == (8760, 3),
            "synthetic_scenarios_shape",
            f"shape={tuple(scenarios.shape)} expected (N, 8760, 3)",
            {"shape": list(scenarios.shape)},
        )
    ]
    if scenarios.ndim == 3 and scenarios.shape[-1] >= 3:
        cfg = config.get("validation", {})
        cf_min = float(cfg.get("capacity_factor_min", 0.0))
        cf_max = float(cfg.get("capacity_factor_max", 1.0))
        temp_min = float(cfg.get("temperature_min_c", -50.0))
        temp_max = float(cfg.get("temperature_max_c", 60.0))
        checks.append(_check_range(scenarios[:, :, 0], cf_min, cf_max, "synthetic_solar_cf_range"))
        checks.append(_check_range(scenarios[:, :, 1], cf_min, cf_max, "synthetic_wind_cf_range"))
        checks.append(_check_range(scenarios[:, :, 2], temp_min, temp_max, "synthetic_temperature_range"))
    return checks


def _export_interface_package(
    output_dir: Path,
    config: dict[str, Any],
    aggregate_profile: np.ndarray,
    unit_profiles: dict[str, np.ndarray],
    diagnostics: dict[str, Any],
    scenario_record: dict[str, Any],
) -> dict[str, Path]:
    interface_dir = output_dir / "h2res_inputs"
    interface_dir.mkdir(parents=True, exist_ok=True)

    cc = str(config.get("country_code", "HR")).upper()
    year = int(config.get("planning_year", 2020))
    scenario = str(config.get("scenario", "avg_yield__normal"))
    periods = np.arange(1, 8761)

    aggregate_path = interface_dir / "aggregate_profile.csv"
    unit_path = interface_dir / "unit_availability.csv"
    temp_path = interface_dir / "temperature_profile.csv"
    manifest_path = interface_dir / "input_manifest.csv"
    json_path = interface_dir / "interface.json"
    note_path = interface_dir / "README.md"
    for path in [aggregate_path, unit_path, temp_path, manifest_path, json_path, note_path]:
        path.parent.mkdir(parents=True, exist_ok=True)

    aggregate_df = pd.DataFrame(
        {
            "period": periods,
            "solar": aggregate_profile[:, 0],
            "wind": aggregate_profile[:, 1],
            "temperature_c": aggregate_profile[:, 2],
        }
    )
    aggregate_df.to_csv(aggregate_path, index=False)
    aggregate_df[["period", "temperature_c"]].to_csv(temp_path, index=False)

    unit_df = pd.DataFrame({"period": periods})
    for unit, values in sorted(unit_profiles.items()):
        unit_df[unit] = np.asarray(values, dtype=float)
    unit_df.to_csv(unit_path, index=False)

    manifest = pd.DataFrame(
        [
            {"official_input": "genco_data", "path": _rel(DATA_DIR / f"genco_data_{cc}_sdewes.csv"), "how_to_use": "Use unit names and capacities as the solver unit contract."},
            {"official_input": "unit_availability", "path": _rel(unit_path), "how_to_use": "Use columns by unit_name when building aval_factor_plant."},
            {"official_input": "temperature_profile", "path": _rel(temp_path), "how_to_use": "Apply through heat/cooling/COP coupling before model construction."},
            {"official_input": "aggregate_profile", "path": _rel(aggregate_path), "how_to_use": "Use for diagnostics and scenario traceability."},
        ]
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path, index=False)

    interface = {
        "country_code": cc,
        "planning_year": year,
        "scenario": scenario,
        "scenario_probability": float(scenario_record.get("probability", math.nan)),
        "profile_periods": 8760,
        "files": dict(zip(manifest["official_input"], manifest["path"])),
        "diagnostics": diagnostics,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(json_path, interface)

    note_path.write_text(
        "\n".join(
            [
                "# Official Model Interface",
                "",
                f"- country: `{cc}`",
                f"- planning year: `{year}`",
                f"- scenario: `{scenario}`",
                f"- probability: `{interface['scenario_probability']:.4f}`",
                f"- wind resolution: `{diagnostics.get('wind_resolution_selected', 'n/a')}`",
                f"- solar resolution: `{diagnostics.get('solar_resolution_selected', 'n/a')}`",
                "",
                "## Files",
                "",
                "- `aggregate_profile.csv`: aggregate solar, wind and temperature profile.",
                "- `unit_availability.csv`: 8760-hour solver-unit availability profiles.",
                "- `temperature_profile.csv`: temperature profile for heat/cooling/COP coupling.",
        "- `input_manifest.csv`: short file map for integration.",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "interface_dir": interface_dir,
        "aggregate_profile_csv": aggregate_path,
        "unit_availability_csv": unit_path,
        "temperature_profile_csv": temp_path,
        "manifest_csv": manifest_path,
        "interface_json": json_path,
        "interface_note": note_path,
    }


def run_validations(config: dict[str, Any], output_dir: Path, prepare_interface: bool = True) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    cc = str(config.get("country_code", "HR")).upper()
    year = int(config.get("planning_year", 2020))
    scenario = str(config.get("scenario", "avg_yield__normal"))
    wind_mode = str(config.get("wind_resolution_mode", "auto"))
    tolerance = float(config.get("validation", {}).get("mass_error_tolerance", 1e-4))

    manifest = build_input_manifest(cc)
    manifest_path = output_dir / "input_manifest.csv"
    manifest.to_csv(manifest_path, index=False)

    checks: list[CheckResult] = []
    missing_required = manifest[(manifest["required"]) & (~manifest["exists"])]
    checks.append(_check(missing_required.empty, "required_artifacts", "all required artifacts exist", {"missing": missing_required["artifact"].tolist()}))
    checks.extend(_validate_historical_data(cc, config))
    checks.extend(_validate_genco(cc))
    checks.extend(_validate_transfer_matrix(DATA_DIR / f"{cc}_wind_transfer_nuts_to_units.csv", "wind_nuts_transfer_matrix"))
    checks.extend(_validate_transfer_matrix(DATA_DIR / f"{cc}_solar_transfer_macro_to_units.csv", "solar_macro_transfer_matrix"))
    checks.extend(_validate_synthetic_scenarios(config))

    scenario_summary: dict[str, Any] = {}
    interface_paths: dict[str, str] = {}
    try:
        scenario_manager = _load_scenario_manager()
        scenarios = scenario_manager.get_representative_scenarios(year=year)
        if scenario not in scenarios:
            checks.append(_check(False, "selected_scenario_exists", f"scenario '{scenario}' not found", {"available": sorted(k for k in scenarios if k != "metadata")}))
        else:
            record = scenarios[scenario]
            profile = record["profile"]
            unit_profiles, diagnostics = scenario_manager.decompose_to_h2res_units(
                profile,
                country_code=cc,
                wind_resolution_mode=wind_mode,
                return_diagnostics=True,
            )
            scenario_summary = {
                "scenario": scenario,
                "planning_year": year,
                "probability": float(record.get("probability", math.nan)),
                "total_energy": float(record.get("total_energy", math.nan)),
                "temperature_mean": float(record.get("temperature_mean", math.nan)),
                "diagnostics": diagnostics,
                "unit_count": int(len(unit_profiles)),
            }

            for name in ["wind_mass_mean_abs_error", "solar_mass_mean_abs_error"]:
                value = diagnostics.get(name)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    checks.append(CheckResult(name, "warn", "mass-balance diagnostic not available"))
                else:
                    checks.append(_check(float(value) <= tolerance, name, f"{float(value):.8f} <= tolerance {tolerance}", {"value": float(value), "tolerance": tolerance}))

            for unit, values in unit_profiles.items():
                arr = np.asarray(values, dtype=float)
                ok = arr.shape == (8760,) and np.isfinite(arr).all() and arr.min() >= -1e-9 and arr.max() <= 1 + 1e-9
                checks.append(_check(ok, f"unit_profile_{unit}", f"shape={arr.shape}, range=[{float(np.nanmin(arr)):.6g}, {float(np.nanmax(arr)):.6g}]"))

            if prepare_interface:
                interface_paths = {key: _rel(value) for key, value in _export_interface_package(output_dir, config, profile, unit_profiles, diagnostics, record).items()}
    except Exception as exc:
        checks.append(CheckResult("scenario_decomposition", "fail", f"{type(exc).__name__}: {exc}"))

    check_rows = [check.__dict__ for check in checks]
    failed = [row for row in check_rows if row["status"] == "fail"]
    warned = [row for row in check_rows if row["status"] == "warn"]
    report = {
        "status": "fail" if failed else "pass",
        "country_code": cc,
        "planning_year": year,
        "scenario": scenario,
        "wind_resolution_mode": wind_mode,
        "counts": {"checks": len(check_rows), "failed": len(failed), "warnings": len(warned)},
        "scenario_summary": scenario_summary,
        "interface_outputs": interface_paths,
        "checks": check_rows,
        "input_manifest": _rel(manifest_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    _write_json(output_dir / "validation_report.json", report)
    pd.DataFrame(
        [
            {"name": check.name, "status": check.status, "message": check.message, "details": json.dumps(check.details, default=_json_default)}
            for check in checks
        ]
    ).to_csv(output_dir / "validation_checks.csv", index=False)

    md_lines = [
        "# Climate Pipeline Validation Report",
        "",
        f"- status: `{report['status']}`",
        f"- country: `{cc}`",
        f"- planning_year: `{year}`",
        f"- scenario: `{scenario}`",
        f"- checks: `{len(check_rows)}`",
        f"- failures: `{len(failed)}`",
        f"- warnings: `{len(warned)}`",
        "",
        "## Failed checks",
        "",
        *([f"- `{row['name']}`: {row['message']}" for row in failed] or ["_None._"]),
        "",
        "## Warnings",
        "",
        *([f"- `{row['name']}`: {row['message']}" for row in warned] or ["_None._"]),
        "",
        "## Interface outputs",
        "",
        *([f"- `{key}`: `{value}`" for key, value in interface_paths.items()] or ["_Not generated._"]),
    ]
    (output_dir / "validation_report.md").write_text("\n".join(md_lines), encoding="utf-8")
    return report


def scenarios_for_mode(mode: str, scenario: str = "avg_yield__normal") -> list[str]:
    if mode == "single":
        return [scenario]
    if mode == "resource":
        return RESOURCE_SCENARIOS
    if mode == "combined":
        return COMBINED_SCENARIOS
    return DIAGONAL_SCENARIOS


def _format_cell(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No data available._"
    columns = list(df.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_format_cell(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def export_scenario_input_batch(config: dict[str, Any], output_dir: Path, mode: str = "resource") -> pd.DataFrame:
    """Export solver-ready climate input packages for multiple scenarios.

    This is the first integration stage requested by the H2RES team: run the
    climate preprocessing/decomposition layer once and write scenario-specific
    input files that can be inspected or fed into the official model.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cc = str(config.get("country_code", "HR")).upper()
    year = int(config.get("planning_year", 2020))
    scenario = str(config.get("scenario", "avg_yield__normal"))
    wind_mode = str(config.get("wind_resolution_mode", "auto"))

    scenario_manager = _load_scenario_manager()
    scenarios = scenario_manager.get_representative_scenarios(year=year)
    requested = scenarios_for_mode(mode, scenario=scenario)
    scenario_names = [name for name in requested if name in scenarios]
    missing = [name for name in requested if name not in scenarios]
    if not scenario_names:
        raise ValueError(f"No requested scenarios are available. Requested={requested}")

    rows: list[dict[str, Any]] = []
    for scenario_name in scenario_names:
        scenario_config = dict(config)
        scenario_config["scenario"] = scenario_name
        record = scenarios[scenario_name]
        profile = record["profile"]
        unit_profiles, diagnostics = scenario_manager.decompose_to_h2res_units(
            profile,
            country_code=cc,
            wind_resolution_mode=wind_mode,
            return_diagnostics=True,
        )

        scenario_dir = output_dir / scenario_name
        paths = _export_interface_package(
            scenario_dir,
            scenario_config,
            profile,
            unit_profiles,
            diagnostics,
            record,
        )

        rows.append(
            {
                "scenario": scenario_name,
                "planning_year": year,
                "probability": float(record.get("probability", math.nan)),
                "cluster_size": int(record.get("cluster_size", 0)),
                "aggregate_solar_cf_mean": float(np.mean(profile[:, 0])),
                "aggregate_wind_cf_mean": float(np.mean(profile[:, 1])),
                "temperature_mean_c": float(np.mean(profile[:, 2])),
                "wind_resolution": diagnostics.get("wind_resolution_selected"),
                "solar_resolution": diagnostics.get("solar_resolution_selected"),
                "wind_mass_mean_abs_error": diagnostics.get("wind_mass_mean_abs_error"),
                "solar_mass_mean_abs_error": diagnostics.get("solar_mass_mean_abs_error"),
                "unit_count": int(len(unit_profiles)),
                "interface_dir": _rel(paths["interface_dir"]),
                "unit_availability_csv": _rel(paths["unit_availability_csv"]),
                "temperature_profile_csv": _rel(paths["temperature_profile_csv"]),
                "aggregate_profile_csv": _rel(paths["aggregate_profile_csv"]),
            }
        )

    summary = pd.DataFrame(rows)
    summary_path = output_dir / "scenario_input_summary.csv"
    summary.to_csv(summary_path, index=False)

    manifest = {
        "country_code": cc,
        "planning_year": year,
        "mode": mode,
        "requested_scenarios": requested,
        "exported_scenarios": scenario_names,
        "missing_scenarios": missing,
        "summary_csv": _rel(summary_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(output_dir / "scenario_input_manifest.json", manifest)

    report_lines = [
        "# Scenario Input Export",
        "",
        f"- country: `{cc}`",
        f"- planning year: `{year}`",
        f"- mode: `{mode}`",
        f"- exported scenarios: `{len(scenario_names)}`",
        f"- missing scenarios: `{len(missing)}`",
        "",
        "## Exported Scenario Inputs",
        "",
        _df_to_markdown(summary),
        "",
        "## How To Use",
        "",
        "Each scenario folder contains an `h2res_inputs` directory.",
        "The key files are:",
        "",
        "- `unit_availability.csv`: 8760-hour solar and wind availability by H2RES solver unit.",
        "- `temperature_profile.csv`: 8760-hour temperature profile for heat/cooling/COP coupling.",
        "- `aggregate_profile.csv`: aggregate solar, wind and temperature profile for diagnostics.",
        "- `input_manifest.csv`: file map for model integration.",
    ]
    if missing:
        report_lines.extend(["", "## Missing Scenarios", "", *[f"- `{name}`" for name in missing]])
    report_path = output_dir / "scenario_input_export.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"[EXPORT] Wrote scenario input package to {output_dir}")
    return summary


def run_one_gurobi_scenario(project_root: Path, scenario: str, output_root: Path, python_executable: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    scenario_dir = output_root / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)
    log_path = scenario_dir / "solver_run.log"
    summary_path = scenario_dir / "solver_subproblem_summary.csv"
    capacity_path = scenario_dir / "capacity_expansion_outputs.csv"
    trade_path = scenario_dir / "trade_flow_outputs.csv"
    objective_breakdown_path = scenario_dir / "objective_breakdown_outputs.csv"
    dispatch_path = scenario_dir / "dispatch_summary_outputs.csv"

    env = os.environ.copy()
    env["H2RES_CLIMATE_SCENARIO"] = scenario
    env["H2RES_RUN_LABEL"] = scenario
    env["H2RES_WRITE_SOLVER_SUMMARY"] = "1"
    env["H2RES_SOLVER_SUMMARY_DIR"] = str(output_root)
    env["H2RES_CAPACITY_EXPORT_DIR"] = str(scenario_dir)
    env["PYTHONUNBUFFERED"] = "1"
    if config:
        env["H2RES_CLIMATE_COUNTRY"] = str(config.get("country_code", "HR")).upper()
        env["H2RES_WIND_RESOLUTION_MODE"] = str(config.get("wind_resolution_mode", "auto"))
        combined_scenario_method = str(config.get("combined_scenario_method", "") or "").strip()
        if combined_scenario_method:
            env["H2RES_COMBINED_SCENARIO_METHOD"] = combined_scenario_method
        max_start_year = str(config.get("max_start_year", "") or "").strip()
        if max_start_year:
            env["H2RES_MAX_START_YEAR"] = max_start_year
        base_climate_scenario = str(config.get("base_climate_scenario", "") or "").strip()
        if base_climate_scenario:
            env["H2RES_BASE_CLIMATE_SCENARIO"] = base_climate_scenario
        profile_overrides = str(config.get("profile_overrides", "") or "").strip()
        if profile_overrides:
            env["H2RES_PROFILE_OVERRIDES"] = profile_overrides
        if "temperature_coupling" in config:
            env["H2RES_TEMPERATURE_COUPLING"] = "1" if bool(config.get("temperature_coupling")) else "0"
        fixed_capacity_input = str(config.get("fixed_capacity_input", "") or "").strip()
        if fixed_capacity_input:
            env["H2RES_FIXED_CAPACITY_INPUT"] = fixed_capacity_input

    cmd = [python_executable, str(project_root / "main_rolling_horizon_v3.py")]
    print(f"[RUN] Starting {scenario}")
    start = time.time()
    with open(log_path, "w", encoding="utf-8") as log_file:
        completed = subprocess.run(cmd, cwd=str(project_root), env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True, check=False)
    duration_sec = time.time() - start

    result = {
        "scenario": scenario,
        "return_code": int(completed.returncode),
        "success": int(completed.returncode == 0),
        "duration_sec": duration_sec,
        "duration_min": duration_sec / 60.0,
        "log_path": str(log_path),
        "summary_path": str(summary_path),
        "summary_exists": int(summary_path.exists()),
        "capacity_path": str(capacity_path),
        "capacity_exists": int(capacity_path.exists()),
        "trade_path": str(trade_path),
        "trade_exists": int(trade_path.exists()),
        "objective_breakdown_path": str(objective_breakdown_path),
        "objective_breakdown_exists": int(objective_breakdown_path.exists()),
        "dispatch_path": str(dispatch_path),
        "dispatch_exists": int(dispatch_path.exists()),
    }
    print(f"[RUN] Finished {scenario} (return_code={completed.returncode}, duration={duration_sec / 60.0:.1f} min)")
    return result


def _classify_capacity_row(row: pd.Series, genco_lookup: dict[str, dict[str, str]]) -> tuple[str, str]:
    variable = str(row.get("variable", ""))
    unit = str(row.get("unit", ""))
    meta = genco_lookup.get(unit, {})
    fuel = str(meta.get("fuel_type", "")).upper()
    technology = str(meta.get("technology", "")).upper()

    if variable == "cap_inv":
        if fuel == "WIND":
            return "generation capacities", "wind_generation_mw"
        if fuel == "SOLAR" or technology == "PHOT" or "SOLAR" in unit.upper():
            return "generation capacities", "solar_generation_mw"
        if fuel == "HYDRO":
            return "generation capacities", "hydro_generation_mw"
        if fuel:
            return "generation capacities", f"{fuel.lower()}_generation_mw"
        return "generation capacities", "other_generation_mw"
    if variable == "stat_sto_cap_inv":
        return "storage and EV flexibility", "stationary_storage_power_mw"
    if variable in {"h2_sto_max_cap", "h2_electrolysis_max_cap", "cap_inv_FC"}:
        return "hydrogen technologies", variable
    if variable in {
        "heat_pump_ind_cap",
        "heat_pump_dh_cap",
        "trad_boiler_ind_cap_inv",
        "trad_boiler_dh_cap_inv",
    }:
        return "heat-sector flexibility", variable
    return "other capacity variables", variable


def _combine_capacity_outputs(output_root: Path, run_manifest: pd.DataFrame, country_code: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "capacity_path" not in run_manifest.columns:
        return pd.DataFrame(), pd.DataFrame()

    genco = _load_solver_capacities(country_code)
    genco_lookup = (
        genco.set_index("unit_name")[["fuel_type", "technology"]].fillna("").to_dict("index")
        if not genco.empty and "unit_name" in genco.columns
        else {}
    )

    frames = []
    for row in run_manifest.itertuples(index=False):
        path = Path(getattr(row, "capacity_path", ""))
        if path.exists():
            df = pd.read_csv(path)
            df["scenario"] = getattr(row, "scenario")
            frames.append(df)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["year"] = pd.to_numeric(combined["year"], errors="coerce").astype("Int64")
    combined["value"] = pd.to_numeric(combined["value"], errors="coerce").fillna(0.0)
    classifications = combined.apply(lambda row: _classify_capacity_row(row, genco_lookup), axis=1)
    combined["category"] = [item[0] for item in classifications]
    combined["subcategory"] = [item[1] for item in classifications]
    combined_path = output_root / "capacity_expansion_comparison.csv"
    combined.to_csv(combined_path, index=False)

    summary = (
        combined.groupby(["scenario", "year", "category", "subcategory"], as_index=False)
        .agg(capacity_value=("value", "sum"))
        .sort_values(["scenario", "year", "category", "subcategory"])
    )
    summary_path = output_root / "capacity_investment_summary.csv"
    summary.to_csv(summary_path, index=False)
    return combined, summary


def _combine_trade_outputs(output_root: Path, run_manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "trade_path" not in run_manifest.columns:
        return pd.DataFrame(), pd.DataFrame()

    frames = []
    for row in run_manifest.itertuples(index=False):
        path = Path(getattr(row, "trade_path", ""))
        if path.exists():
            df = pd.read_csv(path)
            df["scenario"] = getattr(row, "scenario")
            frames.append(df)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    for col in [
        "optimized_import_mwh",
        "import_capacity_mw",
        "input_total_imports_mwh",
        "input_total_exports_mwh",
        "input_export_capacity_mw",
    ]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce").fillna(0.0)
    combined["year"] = pd.to_numeric(combined["year"], errors="coerce").astype("Int64")
    combined_path = output_root / "trade_flow_comparison.csv"
    combined.to_csv(combined_path, index=False)

    summary = (
        combined.groupby(["scenario", "year"], as_index=False)
        .agg(
            optimized_import_gwh=("optimized_import_mwh", lambda x: float(x.sum()) / 1000.0),
            input_total_imports_gwh=("input_total_imports_mwh", lambda x: float(x.sum()) / 1000.0),
            input_total_exports_gwh=("input_total_exports_mwh", lambda x: float(x.sum()) / 1000.0),
            avg_import_capacity_mw=("import_capacity_mw", "mean"),
            avg_input_export_capacity_mw=("input_export_capacity_mw", "mean"),
        )
        .sort_values(["scenario", "year"])
    )
    summary["optimized_import_less_input_export_gwh"] = summary["optimized_import_gwh"] - summary["input_total_exports_gwh"]
    summary_path = output_root / "trade_flow_summary.csv"
    summary.to_csv(summary_path, index=False)
    return combined, summary


def _combine_objective_breakdown_outputs(output_root: Path, run_manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "objective_breakdown_path" not in run_manifest.columns:
        return pd.DataFrame(), pd.DataFrame()

    frames = []
    for row in run_manifest.itertuples(index=False):
        path = Path(getattr(row, "objective_breakdown_path", ""))
        if path.exists():
            df = pd.read_csv(path)
            df["scenario"] = getattr(row, "scenario")
            frames.append(df)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    for col in ["year", "value", "active_in_objective", "objective_total", "active_component_sum", "gap_to_objective"]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")
    combined_path = output_root / "objective_breakdown_comparison.csv"
    combined.to_csv(combined_path, index=False)

    component_rows = combined.loc[combined["component"].ne("objective_total")].copy()
    summary = (
        component_rows.groupby(
            ["scenario", "year", "component_group", "component", "active_in_objective"],
            as_index=False,
        )
        .agg(
            value=("value", "sum"),
            objective_total=("objective_total", "first"),
            active_component_sum=("active_component_sum", "first"),
            gap_to_objective=("gap_to_objective", "first"),
        )
        .sort_values(["scenario", "year", "component_group", "component"])
    )
    summary_path = output_root / "objective_breakdown_summary.csv"
    summary.to_csv(summary_path, index=False)
    return combined, summary


def _combine_dispatch_outputs(output_root: Path, run_manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "dispatch_path" not in run_manifest.columns:
        return pd.DataFrame(), pd.DataFrame()

    frames = []
    for row in run_manifest.itertuples(index=False):
        path = Path(getattr(row, "dispatch_path", ""))
        if path.exists():
            df = pd.read_csv(path)
            df["scenario"] = getattr(row, "scenario")
            frames.append(df)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["year"] = pd.to_numeric(combined["year"], errors="coerce").astype("Int64")
    combined["value"] = pd.to_numeric(combined["value"], errors="coerce").fillna(0.0)
    combined_path = output_root / "dispatch_summary_comparison.csv"
    combined.to_csv(combined_path, index=False)

    summary = (
        combined.groupby(["scenario", "year", "category", "subcategory", "unit"], as_index=False)
        .agg(value=("value", "sum"))
        .sort_values(["scenario", "year", "category", "subcategory"])
    )
    summary_path = output_root / "dispatch_summary.csv"
    summary.to_csv(summary_path, index=False)
    return combined, summary


def _delta_lookup(df: pd.DataFrame, value_col: str, key_cols: list[str]) -> pd.DataFrame:
    if df.empty or value_col not in df.columns:
        return pd.DataFrame()
    baseline = df.loc[df["scenario"].eq("avg_yield__normal"), key_cols + [value_col]].copy()
    if baseline.empty:
        return pd.DataFrame()
    baseline = baseline.rename(columns={value_col: f"{value_col}_avg_normal"})
    merged = df.merge(baseline, on=key_cols, how="left")
    merged[f"{value_col}_delta_vs_avg_normal"] = merged[value_col] - merged[f"{value_col}_avg_normal"]
    return merged


def _safe_label(value: Any) -> str:
    label = str(value).strip().lower()
    for char in [" ", "-", "/", "\\", "(", ")", "&", "+"]:
        label = label.replace(char, "_")
    while "__" in label:
        label = label.replace("__", "_")
    return label.strip("_")


def _high_vs_avg_delta(df: pd.DataFrame, value_col: str, key_cols: list[str]) -> pd.DataFrame:
    delta = _delta_lookup(df, value_col, key_cols)
    if delta.empty:
        return pd.DataFrame()
    return delta.loc[delta["scenario"].eq("high_yield__normal")].copy()


def _write_high_yield_driver_summary(
    output_root: Path,
    solver_deltas: pd.DataFrame,
    capacity_combined: pd.DataFrame,
    capacity_summary: pd.DataFrame,
    trade_summary: pd.DataFrame,
    objective_breakdown_summary: pd.DataFrame,
    dispatch_summary: pd.DataFrame,
) -> pd.DataFrame:
    if solver_deltas.empty:
        return pd.DataFrame()

    drivers = solver_deltas.loc[solver_deltas["scenario"].eq("high_yield__normal")].copy()
    if drivers.empty:
        return pd.DataFrame()
    drivers = drivers[
        [
            "scenario",
            "planning_year",
            "objective_delta_vs_avg_normal",
            "wind_cf_delta_vs_avg_normal",
            "solar_cf_delta_vs_avg_normal",
            "temp_mean_delta_vs_avg_normal",
        ]
    ].copy()
    drivers["high_yield_cost_above_avg"] = drivers["objective_delta_vs_avg_normal"] > 0
    drivers["objective_delta_m"] = drivers["objective_delta_vs_avg_normal"] / 1e6

    if not capacity_summary.empty:
        cap = capacity_summary.groupby(["scenario", "year", "category"], as_index=False).agg(capacity_value=("capacity_value", "sum"))
        cap_delta = _delta_lookup(cap, "capacity_value", ["year", "category"])
        if not cap_delta.empty:
            cap_high = cap_delta.loc[cap_delta["scenario"].eq("high_yield__normal")]
            cap_pivot = cap_high.pivot(index="year", columns="category", values="capacity_value_delta_vs_avg_normal").reset_index()
            cap_pivot = cap_pivot.rename(columns={"year": "planning_year"})
            drivers = drivers.merge(cap_pivot, on="planning_year", how="left")

    if not capacity_combined.empty:
        cap_detail = (
            capacity_combined.groupby(["scenario", "year", "variable", "unit"], as_index=False)
            .agg(capacity_value=("value", "sum"))
        )
        cap_detail_delta = _high_vs_avg_delta(cap_detail, "capacity_value", ["year", "variable", "unit"])
        if not cap_detail_delta.empty:
            selected_caps = {
                ("trad_boiler_ind_cap_inv", "gas_boilers"): "trad_boiler_ind_gas_delta",
                ("trad_boiler_dh_cap_inv", "gas_boilers"): "trad_boiler_dh_gas_delta",
                ("heat_pump_ind_cap", "ATW_HP"): "heat_pump_ind_atw_delta",
                ("h2_electrolysis_max_cap", "Electrolyzer"): "electrolyzer_delta",
                ("h2_sto_max_cap", "H2_storage"): "h2_storage_delta",
            }
            detail_cols = []
            for (variable, unit), out_col in selected_caps.items():
                part = cap_detail_delta.loc[
                    cap_detail_delta["variable"].eq(variable) & cap_detail_delta["unit"].eq(unit),
                    ["year", "capacity_value_delta_vs_avg_normal"],
                ].rename(columns={"year": "planning_year", "capacity_value_delta_vs_avg_normal": out_col})
                if not part.empty:
                    drivers = drivers.merge(part, on="planning_year", how="left")
                    detail_cols.append(out_col)
            if detail_cols:
                drivers["selected_capacity_delta_sum"] = drivers[detail_cols].fillna(0.0).sum(axis=1)

    if not trade_summary.empty:
        trade_delta = _delta_lookup(trade_summary, "optimized_import_gwh", ["year"])
        if not trade_delta.empty:
            trade_high = trade_delta.loc[trade_delta["scenario"].eq("high_yield__normal"), ["year", "optimized_import_gwh_delta_vs_avg_normal"]]
            trade_high = trade_high.rename(columns={"year": "planning_year"})
            drivers = drivers.merge(trade_high, on="planning_year", how="left")

        export_delta = _delta_lookup(trade_summary, "input_total_exports_gwh", ["year"])
        if not export_delta.empty:
            export_high = export_delta.loc[export_delta["scenario"].eq("high_yield__normal"), ["year", "input_total_exports_gwh_delta_vs_avg_normal"]]
            export_high = export_high.rename(columns={"year": "planning_year"})
            drivers = drivers.merge(export_high, on="planning_year", how="left")

    if not objective_breakdown_summary.empty:
        active = objective_breakdown_summary.loc[objective_breakdown_summary["active_in_objective"].eq(1)].copy()
        cost_groups = active.groupby(["scenario", "year", "component_group"], as_index=False).agg(value=("value", "sum"))
        group_delta = _high_vs_avg_delta(cost_groups, "value", ["year", "component_group"])
        if not group_delta.empty:
            group_high = group_delta.pivot(index="year", columns="component_group", values="value_delta_vs_avg_normal").reset_index()
            group_high = group_high.rename(columns={"year": "planning_year"})
            rename_map = {col: f"cost_delta_m__{_safe_label(col)}" for col in group_high.columns if col != "planning_year"}
            group_high = group_high.rename(columns=rename_map)
            for col in rename_map.values():
                group_high[col] = group_high[col] / 1e6
            drivers = drivers.merge(group_high, on="planning_year", how="left")

    if not dispatch_summary.empty:
        dispatch = dispatch_summary.groupby(["scenario", "year", "category", "subcategory"], as_index=False).agg(value=("value", "sum"))
        dispatch_delta = _high_vs_avg_delta(dispatch, "value", ["year", "category", "subcategory"])
        if not dispatch_delta.empty:
            selected_dispatch = {
                ("trade", "optimized_import"): "dispatch_optimized_import_delta_gwh",
                ("system_balance", "ceep"): "dispatch_ceep_delta_gwh",
                ("system_balance", "no_load_electricity"): "dispatch_no_load_electricity_delta_gwh",
                ("heat_sector", "industrial_boiler_generation"): "dispatch_industrial_boiler_generation_delta_gwh",
                ("heat_sector", "district_boiler_generation"): "dispatch_district_boiler_generation_delta_gwh",
                ("heat_sector", "industrial_heat_pump_output"): "dispatch_industrial_heat_pump_delta_gwh",
                ("heat_sector", "district_heat_pump_output"): "dispatch_district_heat_pump_delta_gwh",
                ("electricity_generation", "wind"): "dispatch_wind_generation_delta_gwh",
                ("electricity_generation", "solar"): "dispatch_solar_generation_delta_gwh",
                ("electricity_generation", "fossil"): "dispatch_fossil_generation_delta_gwh",
                ("storage_and_flexibility", "stationary_storage_charge"): "dispatch_storage_charge_delta_gwh",
                ("storage_and_flexibility", "stationary_storage_discharge"): "dispatch_storage_discharge_delta_gwh",
                ("hydrogen", "power_to_hydrogen"): "dispatch_power_to_hydrogen_delta_gwh",
            }
            for (category, subcategory), out_col in selected_dispatch.items():
                part = dispatch_delta.loc[
                    dispatch_delta["category"].eq(category) & dispatch_delta["subcategory"].eq(subcategory),
                    ["year", "value_delta_vs_avg_normal"],
                ].rename(columns={"year": "planning_year", "value_delta_vs_avg_normal": out_col})
                if not part.empty:
                    part[out_col] = part[out_col] / 1000.0
                    drivers = drivers.merge(part, on="planning_year", how="left")

    cost_cols = [col for col in drivers.columns if col.startswith("cost_delta_m__")]
    if cost_cols:
        def _largest_positive(row: pd.Series) -> str:
            values = row[cost_cols].dropna()
            values = values[values > 0]
            if values.empty:
                return ""
            col = values.idxmax()
            return f"{col.replace('cost_delta_m__', '')}: {values[col]:.3f} m"

        drivers["largest_positive_cost_driver"] = drivers.apply(_largest_positive, axis=1)

    drivers["transmission_result_note"] = "Transmission expansion is not represented as an active decision in the current single-zone setup."
    drivers["export_result_note"] = "Optimized imports are model outputs; export columns are input-side values, not endogenous export decisions."

    driver_path = output_root / "high_yield_driver_summary.csv"
    drivers.to_csv(driver_path, index=False)

    focus_years = [2025, 2030]
    explanation = drivers.loc[drivers["planning_year"].isin(focus_years)].copy()
    if not explanation.empty:
        explanation_path = output_root / "high_yield_explanation.csv"
        preferred = [
            "planning_year",
            "objective_delta_vs_avg_normal",
            "objective_delta_m",
            "wind_cf_delta_vs_avg_normal",
            "solar_cf_delta_vs_avg_normal",
            "temp_mean_delta_vs_avg_normal",
            "optimized_import_gwh_delta_vs_avg_normal",
            "input_total_exports_gwh_delta_vs_avg_normal",
            "cost_delta_m__optimized_import_cost",
            "cost_delta_m__electricity_generation_cost",
            "cost_delta_m__generation_investment_cost",
            "cost_delta_m__heat_sector_investment_cost",
            "cost_delta_m__heat_sector_operation_cost",
            "cost_delta_m__storage_and_flexibility_cost",
            "cost_delta_m__hydrogen_and_fuel_cell_cost",
            "cost_delta_m__ramping_cost",
            "cost_delta_m__spill_and_ceep_cost",
            "cost_delta_m__no_load_cost",
            "trad_boiler_ind_gas_delta",
            "trad_boiler_dh_gas_delta",
            "heat_pump_ind_atw_delta",
            "dispatch_optimized_import_delta_gwh",
            "dispatch_industrial_boiler_generation_delta_gwh",
            "dispatch_district_boiler_generation_delta_gwh",
            "dispatch_industrial_heat_pump_delta_gwh",
            "dispatch_district_heat_pump_delta_gwh",
            "dispatch_wind_generation_delta_gwh",
            "dispatch_solar_generation_delta_gwh",
            "dispatch_fossil_generation_delta_gwh",
            "dispatch_ceep_delta_gwh",
            "largest_positive_cost_driver",
            "transmission_result_note",
            "export_result_note",
        ]
        existing = [col for col in preferred if col in explanation.columns]
        remaining = [col for col in explanation.columns if col not in existing]
        explanation[existing + remaining].to_csv(explanation_path, index=False)
    return drivers


def _write_high_yield_analysis_package(
    output_root: Path,
    objective_table: pd.DataFrame,
    solver_deltas: pd.DataFrame,
    capacity_summary: pd.DataFrame,
    trade_summary: pd.DataFrame,
    objective_breakdown_summary: pd.DataFrame,
    dispatch_summary: pd.DataFrame,
    driver_summary: pd.DataFrame,
) -> None:
    excel_path = output_root / "H2RES_high_yield_analysis.xlsx"
    try:
        with pd.ExcelWriter(excel_path) as writer:
            if not objective_table.empty:
                objective_table.to_excel(writer, sheet_name="objective_by_year", index=False)
            if not solver_deltas.empty:
                solver_deltas.to_excel(writer, sheet_name="solver_deltas", index=False)
            if not objective_breakdown_summary.empty:
                objective_breakdown_summary.to_excel(writer, sheet_name="objective_breakdown", index=False)
            if not driver_summary.empty:
                driver_summary.to_excel(writer, sheet_name="high_yield_drivers", index=False)
            if not capacity_summary.empty:
                capacity_summary.to_excel(writer, sheet_name="capacity_summary", index=False)
            if not dispatch_summary.empty:
                dispatch_summary.to_excel(writer, sheet_name="dispatch_summary", index=False)
            if not trade_summary.empty:
                trade_summary.to_excel(writer, sheet_name="trade_summary", index=False)
        print(f"[EXPORT] Wrote high-yield analysis workbook to {excel_path}")
    except Exception as exc:
        print(f"[WARNING] Could not write Excel analysis workbook: {exc}")

    figures_dir = output_root / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"[WARNING] Could not import matplotlib for figures: {exc}")
        return

    try:
        if not solver_deltas.empty:
            plot_df = solver_deltas.loc[
                solver_deltas["scenario"].isin(["low_yield__normal", "high_yield__normal"])
            ].copy()
            plot_df["objective_delta_m"] = pd.to_numeric(plot_df["objective_delta_vs_avg_normal"], errors="coerce") / 1e6
            pivot = plot_df.pivot(index="planning_year", columns="scenario", values="objective_delta_m").sort_index()
            ordered_columns = [col for col in ["low_yield__normal", "high_yield__normal"] if col in pivot.columns]
            pivot = pivot[ordered_columns]
            colors = {"low_yield__normal": "#D95F02", "high_yield__normal": "#1B9E77"}
            fig, ax = plt.subplots(figsize=(8.2, 4.5), dpi=180)
            pivot.plot(kind="bar", ax=ax, color=[colors.get(col, "#7570B3") for col in pivot.columns], width=0.72)
            ax.axhline(0, color="#333333", linewidth=0.8)
            ax.set_title("Objective difference relative to average resource case", fontsize=12, weight="bold")
            ax.set_xlabel("Subproblem start year")
            ax.set_ylabel("Delta objective (million objective units)")
            label_map = {
                "low_yield__normal": "Low yield",
                "high_yield__normal": "High yield",
            }
            handles, _ = ax.get_legend_handles_labels()
            ax.legend(
                handles,
                [label_map.get(col, str(col)) for col in pivot.columns],
                title="",
                frameon=False,
            )
            ax.grid(axis="y", alpha=0.25)
            fig.tight_layout()
            fig.savefig(figures_dir / "objective_delta_by_year.png", bbox_inches="tight")
            plt.close(fig)

        if not driver_summary.empty:
            focus = driver_summary.loc[driver_summary["planning_year"].isin([2025, 2030])].copy()
            required_cols = {
                "objective_delta_m",
                "cost_delta_m__heat_sector_investment_cost",
                "cost_delta_m__optimized_import_cost",
                "cost_delta_m__heat_sector_operation_cost",
            }
            if not focus.empty and required_cols.issubset(focus.columns):
                focus = focus.sort_values("planning_year")
                fig, axes = plt.subplots(
                    1,
                    len(focus),
                    figsize=(7.4, 3.6),
                    dpi=200,
                    sharey=True,
                )
                if len(focus) == 1:
                    axes = [axes]

                positive_color = "#D81B60"
                negative_color = "#00897B"
                neutral_color = "#757575"
                net_color = "#263238"
                category_labels = ["Heat capex", "Import cost", "Heat operation", "Other", "Net"]

                for ax, row in zip(axes, focus.itertuples(index=False)):
                    heat_capex = float(row.cost_delta_m__heat_sector_investment_cost)
                    import_cost = float(row.cost_delta_m__optimized_import_cost)
                    heat_operation = float(row.cost_delta_m__heat_sector_operation_cost)
                    objective_delta = float(row.objective_delta_m)
                    other = objective_delta - heat_capex - import_cost - heat_operation
                    contributions = [heat_capex, import_cost, heat_operation, other]

                    cumulative = 0.0
                    for idx, value in enumerate(contributions):
                        start = cumulative
                        end = cumulative + value
                        bottom = min(start, end)
                        height = abs(value)
                        color = positive_color if value >= 0 else negative_color
                        if idx == 3:
                            color = neutral_color
                        ax.bar(idx, height, bottom=bottom, color=color, width=0.55)
                        ax.plot([idx - 0.27, idx + 0.27], [start, start], color="#555555", linewidth=0.8)
                        if height >= 0.08:
                            va = "bottom" if value >= 0 else "top"
                            label_y = end + (0.12 if value >= 0 else -0.12)
                            ax.text(idx, label_y, f"{value:+.2f}", ha="center", va=va, fontsize=7)
                        cumulative = end

                    ax.bar(4, abs(objective_delta), bottom=min(0, objective_delta), color=net_color, width=0.55)
                    ax.text(
                        4,
                        objective_delta + (0.16 if objective_delta >= 0 else -0.16),
                        f"{objective_delta:+.2f}",
                        ha="center",
                        va="bottom" if objective_delta >= 0 else "top",
                        fontsize=7,
                        weight="bold",
                    )
                    ax.axhline(0, color="#333333", linewidth=0.8)
                    ax.set_title(f"{int(row.planning_year)} subproblem", fontsize=10, weight="bold")
                    ax.set_xticks(range(len(category_labels)))
                    ax.set_xticklabels(category_labels, rotation=25, ha="right", fontsize=8)
                    ax.grid(axis="y", alpha=0.22)
                    ax.spines["top"].set_visible(False)
                    ax.spines["right"].set_visible(False)

                axes[0].set_ylabel("Delta vs average case\n(million objective units)")
                fig.suptitle(
                    "Why high yield is more expensive in selected subproblems",
                    fontsize=12,
                    weight="bold",
                )
                fig.tight_layout(rect=[0, 0, 1, 0.92])
                fig.savefig(figures_dir / "high_yield_cost_driver_deltas.png", bbox_inches="tight")
                plt.close(fig)
    except Exception as exc:
        print(f"[WARNING] Could not write high-yield figures: {exc}")


def build_sensitivity_outputs(output_root: Path, mode: str, run_manifest: pd.DataFrame, country_code: str = "HR") -> None:
    run_manifest_path = output_root / "run_manifest.csv"
    run_manifest.to_csv(run_manifest_path, index=False)

    summary_frames = []
    for row in run_manifest.itertuples(index=False):
        summary_path = Path(row.summary_path)
        if summary_path.exists():
            df = pd.read_csv(summary_path)
            df["run_duration_min"] = row.duration_min
            summary_frames.append(df)

    combined_path = output_root / "solver_subproblem_comparison.csv"
    deltas_path = output_root / "solver_deltas_vs_avg_normal.csv"
    markdown_path = output_root / "climate_sensitivity_summary.md"

    combined = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    merged = pd.DataFrame()
    objective_table = pd.DataFrame()
    if not combined.empty:
        combined.to_csv(combined_path, index=False)
        numeric_cols = [
            "objective",
            "aggregate_solar_cf_mean",
            "aggregate_wind_cf_mean",
            "temperature_mean_c",
            "district_heat_scale_mean",
            "general_heat_scale_mean",
            "cooling_scale_mean",
        ]
        for col in numeric_cols:
            if col in combined.columns:
                combined[col] = pd.to_numeric(combined[col], errors="coerce")

        baseline = combined.loc[combined["scenario"] == "avg_yield__normal"].copy()
        if not baseline.empty:
            baseline = baseline.rename(
                columns={
                    "objective": "objective_avg_normal",
                    "aggregate_solar_cf_mean": "aggregate_solar_cf_mean_avg_normal",
                    "aggregate_wind_cf_mean": "aggregate_wind_cf_mean_avg_normal",
                    "temperature_mean_c": "temperature_mean_c_avg_normal",
                }
            )
            keep_cols = [
                "planning_year",
                "objective_avg_normal",
                "aggregate_solar_cf_mean_avg_normal",
                "aggregate_wind_cf_mean_avg_normal",
                "temperature_mean_c_avg_normal",
            ]
            merged = combined.merge(baseline[keep_cols], on="planning_year", how="left")
            merged["objective_delta_vs_avg_normal"] = merged["objective"] - merged["objective_avg_normal"]
            merged["wind_cf_delta_vs_avg_normal"] = merged["aggregate_wind_cf_mean"] - merged["aggregate_wind_cf_mean_avg_normal"]
            merged["solar_cf_delta_vs_avg_normal"] = merged["aggregate_solar_cf_mean"] - merged["aggregate_solar_cf_mean_avg_normal"]
            merged["temp_mean_delta_vs_avg_normal"] = merged["temperature_mean_c"] - merged["temperature_mean_c_avg_normal"]
            merged.to_csv(deltas_path, index=False)

        objective_table = combined.pivot(index="planning_year", columns="scenario", values="objective").reset_index().sort_values("planning_year")

    capacity_combined, capacity_summary = _combine_capacity_outputs(output_root, run_manifest, country_code)
    _, trade_summary = _combine_trade_outputs(output_root, run_manifest)
    _, objective_breakdown_summary = _combine_objective_breakdown_outputs(output_root, run_manifest)
    _, dispatch_summary = _combine_dispatch_outputs(output_root, run_manifest)
    driver_summary = _write_high_yield_driver_summary(
        output_root,
        merged,
        capacity_combined,
        capacity_summary,
        trade_summary,
        objective_breakdown_summary,
        dispatch_summary,
    )
    _write_high_yield_analysis_package(
        output_root,
        objective_table,
        merged,
        capacity_summary,
        trade_summary,
        objective_breakdown_summary,
        dispatch_summary,
        driver_summary,
    )

    manifest_cols = ["scenario", "return_code", "success", "duration_min", "summary_exists"]
    for optional_col in ["capacity_exists", "trade_exists", "objective_breakdown_exists", "dispatch_exists"]:
        if optional_col in run_manifest.columns:
            manifest_cols.append(optional_col)

    md_lines = [
        "# Climate Sensitivity Gurobi Summary",
        "",
        f"- mode: `{mode}`",
        f"- scenarios attempted: `{len(run_manifest)}`",
        "",
        "## Run Manifest",
        _df_to_markdown(run_manifest[manifest_cols]),
        "",
        "## Submodel Objectives",
        _df_to_markdown(objective_table),
    ]
    if not merged.empty:
        delta_cols = ["planning_year", "scenario", "objective_delta_vs_avg_normal", "wind_cf_delta_vs_avg_normal", "solar_cf_delta_vs_avg_normal", "temp_mean_delta_vs_avg_normal"]
        md_lines.extend(["", "## Deltas Vs Avg Yield + Normal Thermal", _df_to_markdown(merged[delta_cols])])
    if not capacity_summary.empty:
        md_lines.extend(["", "## Capacity Investment Summary", _df_to_markdown(capacity_summary)])
    if not trade_summary.empty:
        md_lines.extend(
            [
                "",
                "## Trade Flow Summary",
                "",
                "Optimized imports are model outputs. Export-related columns are input-side values in the current single-zone setup, not endogenous export decisions.",
                "",
                _df_to_markdown(trade_summary),
            ]
        )
    if not objective_breakdown_summary.empty:
        objective_check = (
            objective_breakdown_summary.groupby(["scenario", "year"], as_index=False)
            .agg(gap_to_objective=("gap_to_objective", "first"))
            .sort_values(["scenario", "year"])
        )
        md_lines.extend(
            [
                "",
                "## Objective Breakdown Check",
                "",
                "The gap should stay close to zero; it compares the active exported cost components with the Gurobi objective.",
                "",
                _df_to_markdown(objective_check),
            ]
        )
    if not dispatch_summary.empty:
        md_lines.extend(
            [
                "",
                "## Dispatch Summary",
                "",
                _df_to_markdown(dispatch_summary),
            ]
        )
    if not driver_summary.empty:
        md_lines.extend(["", "## High Yield Driver Summary", _df_to_markdown(driver_summary)])
    markdown_path.write_text("\n".join(md_lines), encoding="utf-8")


def run_gurobi_sensitivity_batch(
    output_root: Path,
    mode: str = "diagonal",
    scenario: str = "avg_yield__normal",
    python_executable: str = sys.executable,
    stop_on_failure: bool = False,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    output_root.mkdir(parents=True, exist_ok=True)
    run_rows = []
    custom_scenarios = list((config or {}).get("custom_gurobi_scenarios", []) or [])
    if custom_scenarios:
        scenario_specs = custom_scenarios
    else:
        scenario_specs = [{"name": scenario_name} for scenario_name in scenarios_for_mode(mode, scenario=scenario)]

    for scenario_spec in scenario_specs:
        if isinstance(scenario_spec, str):
            scenario_name = scenario_spec
            scenario_config = dict(config or {})
        else:
            scenario_name = str(scenario_spec.get("name", "")).strip()
            scenario_config = dict(config or {})
            scenario_config.update({k: v for k, v in scenario_spec.items() if k != "name"})
        if not scenario_name:
            continue
        result = run_one_gurobi_scenario(PROJECT_ROOT, scenario_name, output_root, python_executable, config=scenario_config)
        run_rows.append(result)
        if result["return_code"] != 0 and stop_on_failure:
            break
    run_manifest = pd.DataFrame(run_rows)
    country_code = str((config or {}).get("country_code", "HR")).upper()
    build_sensitivity_outputs(output_root, mode, run_manifest, country_code=country_code)
    return run_manifest


def _load_solver_capacities(country_code: str) -> pd.DataFrame:
    path = DATA_DIR / f"genco_data_{country_code}_sdewes.csv"
    if not path.exists():
        return pd.DataFrame(columns=["unit_name", "cap_mw", "fuel_type", "technology"])
    df = pd.read_csv(path)
    keep_cols = [col for col in ["unit_name", "cap_mw", "fuel_type", "technology"] if col in df.columns]
    df = df[keep_cols].copy()
    df["cap_mw"] = pd.to_numeric(df.get("cap_mw", 0.0), errors="coerce").fillna(0.0)
    return df


def _capacity_for_units(genco: pd.DataFrame, unit_names: list[str], kind: str) -> float:
    if genco.empty or "unit_name" not in genco.columns:
        return 0.0
    units = set(unit_names)
    mask = genco["unit_name"].isin(units)
    if kind == "solar" and "technology" in genco.columns:
        mask = mask | genco["technology"].astype(str).str.upper().eq("PHOT")
    if kind == "wind" and "fuel_type" in genco.columns:
        mask = mask | genco["fuel_type"].astype(str).str.upper().eq("WIND")
    return float(genco.loc[mask, "cap_mw"].sum())


def run_quick_sensitivity(config: dict[str, Any], output_dir: Path) -> pd.DataFrame:
    """Run a fast climate-only sensitivity without solving Gurobi."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cc = str(config.get("country_code", "HR")).upper()
    year = int(config.get("planning_year", 2020))
    mode = str(config.get("sensitivity_mode", "diagonal"))
    scenario = str(config.get("scenario", "avg_yield__normal"))
    wind_mode = str(config.get("wind_resolution_mode", "auto"))
    baseline_name = "avg_yield__normal"

    scenario_manager = _load_scenario_manager()
    scenarios = scenario_manager.get_representative_scenarios(year=year)
    scenario_names = [name for name in scenarios_for_mode(mode, scenario=scenario) if name in scenarios]
    if baseline_name in scenarios and baseline_name not in scenario_names:
        scenario_names.append(baseline_name)

    genco = _load_solver_capacities(cc)
    rows: list[dict[str, Any]] = []
    unit_rows: list[dict[str, Any]] = []
    for scenario_name in scenario_names:
        record = scenarios[scenario_name]
        profile = record["profile"]
        unit_profiles, diagnostics = scenario_manager.decompose_to_h2res_units(
            profile,
            country_code=cc,
            wind_resolution_mode=wind_mode,
            return_diagnostics=True,
        )

        solar_units = [unit for unit in unit_profiles if "solar" in unit.lower()]
        wind_units = [unit for unit in unit_profiles if "wind" in unit.lower()]
        solar_cap_mw = _capacity_for_units(genco, solar_units, "solar")
        wind_cap_mw = _capacity_for_units(genco, wind_units, "wind")

        solar_proxy_gwh = sum(float(np.mean(unit_profiles[unit])) * float(genco.loc[genco["unit_name"].eq(unit), "cap_mw"].sum()) * 8760.0 / 1000.0 for unit in solar_units)
        wind_proxy_gwh = sum(float(np.mean(unit_profiles[unit])) * float(genco.loc[genco["unit_name"].eq(unit), "cap_mw"].sum()) * 8760.0 / 1000.0 for unit in wind_units)
        temp = np.asarray(profile[:, 2], dtype=float)

        rows.append(
            {
                "scenario": scenario_name,
                "probability": float(record.get("probability", math.nan)),
                "cluster_size": int(record.get("cluster_size", 0)),
                "aggregate_solar_cf_mean": float(np.mean(profile[:, 0])),
                "aggregate_wind_cf_mean": float(np.mean(profile[:, 1])),
                "temperature_mean_c": float(np.mean(temp)),
                "temperature_min_c": float(np.min(temp)),
                "temperature_max_c": float(np.max(temp)),
                "heating_degree_hours_18c": float(np.maximum(18.0 - temp, 0.0).sum()),
                "cooling_degree_hours_22c": float(np.maximum(temp - 22.0, 0.0).sum()),
                "solar_proxy_gwh": solar_proxy_gwh,
                "wind_proxy_gwh": wind_proxy_gwh,
                "solar_plus_wind_proxy_gwh": solar_proxy_gwh + wind_proxy_gwh,
                "solar_capacity_mw": solar_cap_mw,
                "wind_capacity_mw": wind_cap_mw,
                "solar_full_load_hours": solar_proxy_gwh * 1000.0 / solar_cap_mw if solar_cap_mw > 0 else math.nan,
                "wind_full_load_hours": wind_proxy_gwh * 1000.0 / wind_cap_mw if wind_cap_mw > 0 else math.nan,
                "wind_resolution": diagnostics.get("wind_resolution_selected"),
                "solar_resolution": diagnostics.get("solar_resolution_selected"),
                "wind_mass_mean_abs_error": diagnostics.get("wind_mass_mean_abs_error"),
                "solar_mass_mean_abs_error": diagnostics.get("solar_mass_mean_abs_error"),
            }
        )

        for unit, values in sorted(unit_profiles.items()):
            arr = np.asarray(values, dtype=float)
            unit_rows.append(
                {
                    "scenario": scenario_name,
                    "unit_name": unit,
                    "mean_cf": float(arr.mean()),
                    "min_cf": float(arr.min()),
                    "max_cf": float(arr.max()),
                }
            )

    summary = pd.DataFrame(rows)
    summary_path = output_dir / "quick_sensitivity_summary.csv"
    summary.to_csv(summary_path, index=False)

    deltas = summary.copy()
    baseline = summary.loc[summary["scenario"].eq(baseline_name)]
    if not baseline.empty:
        base = baseline.iloc[0]
        for col in [
            "aggregate_solar_cf_mean",
            "aggregate_wind_cf_mean",
            "temperature_mean_c",
            "heating_degree_hours_18c",
            "cooling_degree_hours_22c",
            "solar_proxy_gwh",
            "wind_proxy_gwh",
            "solar_plus_wind_proxy_gwh",
        ]:
            deltas[f"{col}_delta_vs_avg_normal"] = deltas[col] - base[col]
            if abs(float(base[col])) > 1e-12:
                deltas[f"{col}_pct_delta_vs_avg_normal"] = 100.0 * (deltas[col] - base[col]) / base[col]
    deltas_path = output_dir / "quick_sensitivity_deltas_vs_avg_normal.csv"
    deltas.to_csv(deltas_path, index=False)

    unit_path = output_dir / "quick_sensitivity_unit_cf.csv"
    pd.DataFrame(unit_rows).to_csv(unit_path, index=False)

    report_lines = [
        "# Quick Climate Sensitivity",
        "",
        f"- country: `{cc}`",
        f"- planning year: `{year}`",
        f"- mode: `{mode}`",
        f"- wind resolution mode: `{wind_mode}`",
        f"- scenarios: `{len(summary)}`",
        "",
        "## Summary",
        _df_to_markdown(summary),
        "",
        "## Deltas Vs Avg Yield + Normal Thermal",
        _df_to_markdown(deltas),
        "",
        "## Files",
        "",
        f"- summary: `{_rel(summary_path)}`",
        f"- deltas: `{_rel(deltas_path)}`",
        f"- unit CF metrics: `{_rel(unit_path)}`",
    ]
    report_path = output_dir / "quick_sensitivity_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"[QUICK] Wrote {report_path}")
    return summary


def _run_command(name: str, cmd: list[str], run_dir: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    log_path = run_dir / f"{name}.log"
    print(f"[PIPELINE] {name}: {' '.join(cmd)}")
    start = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env or os.environ.copy(), stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
    duration = time.time() - start
    print(f"[PIPELINE] {name} finished with return_code={completed.returncode} in {duration:.1f}s")
    return {"step": name, "return_code": int(completed.returncode), "success": bool(completed.returncode == 0), "duration_sec": duration, "log_path": _rel(log_path)}


def parse_pipeline_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the H2RES climate/profile pipeline.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to pipeline config JSON.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory override.")
    parser.add_argument("--country", default=None, help="ISO2 country override.")
    parser.add_argument("--planning-year", type=int, default=None, help="Planning year override.")
    parser.add_argument("--scenario", default=None, help="Scenario override, e.g. avg_yield__normal.")
    parser.add_argument("--wind-resolution-mode", choices=["auto", "nuts3", "macro"], default=None, help="Wind spatial mode override.")
    parser.add_argument("--sensitivity-mode", choices=["single", "resource", "diagonal", "combined"], default=None, help="Scenario set used for exports, quick checks or Gurobi runs.")
    parser.add_argument("--export-scenario-inputs", action="store_true", help="Export H2RES-ready input files for the selected scenario set without solving Gurobi.")
    parser.add_argument("--quick-sensitivity", action="store_true", help="Run a fast climate-only sensitivity without Gurobi.")
    parser.add_argument("--validate-only", action="store_true", help="Run only validation/interface preparation.")
    parser.add_argument("--run-gurobi", action="store_true", help="Run configured Gurobi sensitivity after validation.")
    parser.add_argument("--fixed-capacity-input", default=None, help="Optional capacity_expansion_outputs.csv file used to fix capacity investments during Gurobi runs.")
    parser.add_argument("--generate-scenarios", action="store_true", help="Regenerate synthetic scenarios before validation.")
    parser.add_argument("--run-preprocessing", action="store_true", help="Run climate preprocessing before validation.")
    parser.add_argument("--continue-on-validation-failure", action="store_true", help="Run Gurobi even if validation checks fail.")
    return parser.parse_args(argv)


def resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()
    config = _load_json(config_path)
    config["_config_path"] = _rel(config_path)

    if args.country:
        config["country_code"] = args.country.upper()
    if args.planning_year is not None:
        config["planning_year"] = int(args.planning_year)
    if args.scenario:
        config["scenario"] = args.scenario
    if args.wind_resolution_mode:
        config["wind_resolution_mode"] = args.wind_resolution_mode
    if args.sensitivity_mode:
        config["sensitivity_mode"] = args.sensitivity_mode
    if args.fixed_capacity_input:
        fixed_path = Path(args.fixed_capacity_input)
        if not fixed_path.is_absolute():
            fixed_path = (PROJECT_ROOT / fixed_path).resolve()
        config["fixed_capacity_input"] = str(fixed_path)

    steps = dict(config.get("steps", {}))
    if args.validate_only:
        steps.update({"run_preprocessing": False, "generate_scenarios": False, "run_validations": True, "prepare_official_interface": True, "run_gurobi_sensitivity": False})
    if args.run_gurobi:
        steps["run_gurobi_sensitivity"] = True
    if args.quick_sensitivity:
        steps["run_quick_sensitivity"] = True
        if not args.sensitivity_mode and str(config.get("sensitivity_mode", "single")) == "single":
            config["sensitivity_mode"] = "diagonal"
    if args.export_scenario_inputs:
        steps["export_scenario_inputs"] = True
        if not args.sensitivity_mode and str(config.get("sensitivity_mode", "single")) == "single":
            config["sensitivity_mode"] = "resource"
    if args.generate_scenarios:
        steps["generate_scenarios"] = True
    if args.run_preprocessing:
        steps["run_preprocessing"] = True
    config["steps"] = steps

    if args.output_dir:
        config["output_root"] = args.output_dir
    return config


def make_run_dir(config: dict[str, Any]) -> Path:
    output_root = Path(str(config.get("output_root", "results/climate_pipeline_runs")))
    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cc = str(config.get("country_code", "HR")).upper()
    scenario = str(config.get("scenario", "avg_yield__normal"))
    year = int(config.get("planning_year", 2020))
    run_dir = output_root / f"{stamp}_{cc}_{scenario}_{year}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_run_summary(run_dir: Path, config: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    _write_json(run_dir / "run_config_resolved.json", config)
    _write_json(run_dir / "run_manifest.json", {"steps": steps})
    lines = [
        "# Climate Pipeline Run Summary",
        "",
        f"- country: `{str(config.get('country_code', 'HR')).upper()}`",
        f"- planning_year: `{config.get('planning_year')}`",
        f"- scenario: `{config.get('scenario')}`",
        f"- wind_resolution_mode: `{config.get('wind_resolution_mode')}`",
        f"- config: `{config.get('_config_path')}`",
        "",
        "## Steps",
        "",
    ]
    for step in steps:
        status = "ok" if step.get("success") else "failed"
        lines.append(f"- `{step['step']}`: {status}")
        if step.get("log_path"):
            lines.append(f"  log: `{step['log_path']}`")
        if step.get("validation_report"):
            lines.append(f"  validation report: `{step['validation_report']}`")
    (run_dir / "run_summary.md").write_text("\n".join(lines), encoding="utf-8")


def pipeline_main(argv: list[str] | None = None) -> int:
    args = parse_pipeline_args(argv)
    config = resolve_config(args)
    run_dir = make_run_dir(config)
    print(f"[PIPELINE] Run directory: {run_dir}")

    steps: list[dict[str, Any]] = []
    step_cfg = config.get("steps", {})
    cc = str(config.get("country_code", "HR")).upper()

    if step_cfg.get("run_preprocessing", False):
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "fetch_ninja_data.py"), "--country", cc]
        steps.append(_run_command("preprocessing", cmd, run_dir))

    if step_cfg.get("generate_scenarios", False):
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "xgboost_climate_model.py"),
            "--generate",
            "--num_scenarios",
            str(int(config.get("num_synthetic_years", 100))),
            "--country",
            cc,
        ]
        steps.append(_run_command("generate_scenarios", cmd, run_dir))

    if step_cfg.get("run_validations", True):
        validation_dir = run_dir / "validation"
        report = run_validations(config, validation_dir, prepare_interface=bool(step_cfg.get("prepare_official_interface", True)))
        steps.append({"step": "validations", "return_code": 0 if report["status"] == "pass" else 1, "success": report["status"] == "pass", "duration_sec": None, "log_path": None, "validation_report": _rel(validation_dir / "validation_report.json")})

    validation_failed = any(step["step"] == "validations" and not step["success"] for step in steps)
    blocked_by_validation = validation_failed and not args.continue_on_validation_failure
    if blocked_by_validation:
        print("[PIPELINE] Validation failed; stopping before Gurobi.")

    if not blocked_by_validation and step_cfg.get("export_scenario_inputs", False):
        output_dir = run_dir / "scenario_inputs"
        mode = str(config.get("sensitivity_mode", "resource"))
        start = time.time()
        summary = export_scenario_input_batch(config, output_dir, mode=mode)
        steps.append({"step": "scenario_input_export", "return_code": 0, "success": True, "duration_sec": time.time() - start, "log_path": _rel(output_dir / "scenario_input_export.md"), "scenario_count": int(len(summary))})

    if not blocked_by_validation and step_cfg.get("run_quick_sensitivity", False):
        output_dir = run_dir / "quick_sensitivity"
        start = time.time()
        summary = run_quick_sensitivity(config, output_dir)
        steps.append({"step": "quick_sensitivity", "return_code": 0, "success": True, "duration_sec": time.time() - start, "log_path": _rel(output_dir / "quick_sensitivity_report.md"), "scenario_count": int(len(summary))})

    if not blocked_by_validation and step_cfg.get("run_gurobi_sensitivity", False):
        output_dir = run_dir / "gurobi_sensitivity"
        mode = str(config.get("sensitivity_mode", "single"))
        scenario = str(config.get("scenario", "avg_yield__normal"))
        start = time.time()
        run_manifest = run_gurobi_sensitivity_batch(output_dir, mode=mode, scenario=scenario, python_executable=sys.executable, config=config)
        steps.append({"step": "gurobi_sensitivity", "return_code": 0 if run_manifest["return_code"].eq(0).all() else 1, "success": bool(run_manifest["return_code"].eq(0).all()), "duration_sec": time.time() - start, "log_path": _rel(output_dir / "run_manifest.csv")})

    _write_run_summary(run_dir, config, steps)
    success = all(step.get("success", False) for step in steps)
    print(f"[PIPELINE] Finished. Summary: {run_dir / 'run_summary.md'}")
    return 0 if success else 1


def main(argv: list[str] | None = None) -> int:
    return pipeline_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
