"""Run the public interface on deterministic illustrative data, without a solver.

The profiles below are fabricated for an interface demonstration. They are not
trained-model output, meteorological observations or scientific performance data.
"""
from pathlib import Path
import argparse
import json
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline
from h2res_interface import availability_frame, availability_parameters, couple_temperature


def create_inputs(folder):
    """Write a complete, inspectable two-zone mapping fixture for country ZZ."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    hours = np.arange(8760)
    daylight = np.maximum(np.sin(2*np.pi*(hours % 24-6)/24), 0)
    season = .7 + .3*np.sin(2*np.pi*hours/8760)
    values = np.empty((18, 8760, 3), dtype=np.float32)
    for i in range(18):
        values[i, :, 0] = (.18 + .006*i)*daylight*season
        values[i, :, 1] = .18 + .006*i + .035*np.sin(2*np.pi*hours/144+i*.1)
        values[i, :, 2] = 8 + .6*i + 10*np.sin(2*np.pi*hours/8760)
    np.save(folder/"illustrative_profiles.npy", values)
    time = pd.date_range("2001-01-01", periods=8760, freq="h")
    pd.DataFrame({"time": time, "SOLAR_WEST": .25*daylight*season,
                  "SOLAR_EAST": .4*daylight*season}).to_parquet(folder/"ZZ_solar_macro_profiles.parquet", index=False)
    pd.DataFrame({"time": time, "WIND_INLAND": .25+.04*np.sin(2*np.pi*hours/144),
                  "WIND_COAST": .35+.03*np.cos(2*np.pi*hours/144)}).to_parquet(folder/"ZZ_wind_macro_profiles.parquet", index=False)
    pd.DataFrame({"unit_name": ["solar_west", "solar_east", "wind_inland", "wind_coast"],
                  "technology": ["PHOT", "PHOT", "WTON", "WTON"],
                  "fuel_type": ["Solar", "Solar", "Wind", "Wind"],
                  "cap_mw": [30., 70., 60., 40.]}).to_csv(folder/"genco_data_ZZ_sdewes.csv", index=False)
    pd.DataFrame({"unit_name": ["solar_west", "solar_east"],
                  "source_zone": ["SOLAR_WEST", "SOLAR_EAST"],
                  "weight": [1., 1.]}).to_csv(folder/"ZZ_solar_transfer_macro_to_units.csv", index=False)
    pd.DataFrame({"unit_name": ["wind_inland", "wind_coast"],
                  "source_zone": ["WIND_INLAND", "WIND_COAST"],
                  "weight": [1., 1.]}).to_csv(folder/"ZZ_wind_transfer_macro_to_units.csv", index=False)
    pd.DataFrame({"year": 2020, "period": np.arange(1, 8761), "hror": .25}).to_csv(
        folder/"ncre_aval_factor_ZZ_2020_2050_sdewes.csv", index=False)
    (folder/"DEMO_ONLY.txt").write_text("Illustrative data for interface checks; not research results.\n", encoding="utf-8")


def run(output):
    output = Path(output).resolve()
    output.relative_to((pipeline.ROOT/"exports").resolve())
    output.mkdir(parents=True, exist_ok=False)
    inputs = output/"inputs"
    create_inputs(inputs)
    pipeline.main(["select", "--profiles", str(inputs/"illustrative_profiles.npy"),
                   "--method", "orthogonal", "--output", str(output/"cases.npz")])
    pipeline.main(["export-units", "--scenarios", str(output/"cases.npz"),
                   "--scenario", "avg_yield__normal", "--mapping-dir", str(inputs),
                   "--country", "ZZ", "--wind-resolution", "macro", "--output", str(output/"units.npz")])
    with np.load(output/"units.npz") as archive:
        units = {name: archive[name] for name in archive.files}
    availability_frame(units, 2030).to_csv(output/"availability_2030.csv", index=False)
    parameters = availability_parameters(units, 2030)
    with np.load(output/"cases.npz") as archive:
        temp = archive["avg_yield__normal"][:, 2]
    baseline_temp = np.full(8760, 12.)
    heat = {("district_heat", p, 2030): 10. for p in range(1, 8761)}
    cooling = {("cooling", p, 2030): 4. for p in range(1, 8761)}
    cop = {("heat_pump", p, 2030): 3. for p in range(1, 8761)}
    coupled = couple_temperature(temp, baseline_temp, 2030, heat_demand=heat,
        cooling_demand=cooling, hp_cop_period=cop, district_heat_units=["district_heat"],
        cooling_units=["cooling"], heat_pump_units=["heat_pump"], country_code="ZZ")
    if not np.isclose(sum(coupled["heat_demand"].values()), sum(heat.values())):
        raise RuntimeError("Temperature demo did not preserve annual heat demand")
    summary = {"data_kind": "illustrative_only", "research_result": False,
               "ensemble_size": 18, "unit_count": len(units),
               "availability_parameter_count": len(parameters),
               "first_parameter_key": [next(iter(units)), 1, 2030],
               "heat_total_before": sum(heat.values()),
               "heat_total_after": sum(coupled["heat_demand"].values()),
               "cooling_total_before": sum(cooling.values()),
               "cooling_total_after": sum(coupled["cooling_demand"].values()),
               "minimum_cop": min(coupled["hp_cop_period"].values()),
               "maximum_cop": max(coupled["hp_cop_period"].values()),
               "solver_called": False, "training_called": False}
    pipeline.save_json(output/"demo_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="exports/demo_01")
    run(parser.parse_args().output)
