"""Build historical wind, solar, and temperature inputs for H2RES.

Wind uses Open-Meteo, a fleet power curve, and optional IRENA calibration.
Solar uses Renewables.ninja with optional IRENA PV calibration. Temperature
uses Renewables.ninja. The script exports national
profiles together with regional aggregation layers used by the preprocessing
workflow.
"""

import argparse
import glob
import json
import os
import re
import time
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

try:
    from sklearn.cluster import KMeans
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    print("[WARN] scikit-learn not installed. K-Means wind farm clustering disabled.")

try:
    import geopandas as gpd
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False
    gpd = None

# Country configuration

COUNTRY_REGISTRY = {
    "HR": {
        "full_name": "Croatia",
        "macro_zones": {
            "HR_COAST_SOUTH": {"description": "Southern coastal and Adriatic belt"},
            "HR_NORTH_CENTRAL": {"description": "Northern coast, Lika, and central Croatia"},
            "HR_EAST": {"description": "Slavonia and eastern Croatia"},
        },
        "wind_county_lookup": {
            "Dubrovnik-Neretva": {"nuts_id": "HR037", "macro_zone": "HR_COAST_SOUTH"},
            "Lika-Senj": {"nuts_id": "HR032", "macro_zone": "HR_NORTH_CENTRAL"},
            "Sibenik-Knin": {"nuts_id": "HR034", "macro_zone": "HR_COAST_SOUTH"},
            "Split-Dalmatie": {"nuts_id": "HR035", "macro_zone": "HR_COAST_SOUTH"},
            "Zadar": {"nuts_id": "HR033", "macro_zone": "HR_NORTH_CENTRAL"},
            "Zagreb": {"nuts_id": "HR065", "macro_zone": "HR_NORTH_CENTRAL"},
            "City of Zagreb": {"nuts_id": "HR050", "macro_zone": "HR_NORTH_CENTRAL"},
            "Osijek-Baranja": {"nuts_id": "HR025", "macro_zone": "HR_EAST"},
        },
        "solar_zones": {
            'Zone1_Dalmacija': {
                'description': 'Dalmacija',
                'weight_MW': 1285.45,
                'lat': 43.788,
                'lon': 16.001,
                'macro_zone': 'HR_COAST_SOUTH',
            },
            'Zone2_Slavonija': {
                'description': 'Slavonija',
                'weight_MW': 798.0,
                'lat': 45.311,
                'lon': 18.235,
                'macro_zone': 'HR_EAST',
            },
            'Zone3_Sjever': {
                'description': 'Sjever/Kontinent',
                'weight_MW': 229.9,
                'lat': 45.865,
                'lon': 16.206,
                'macro_zone': 'HR_NORTH_CENTRAL',
            }
        },
        "temp_points": {
            'Zone1_Zagreb': {
                'description': 'Zagreb',
                'weight_pop': 790000,
                'lat': 45.81,
                'lon': 15.98,
                'macro_zone': 'HR_NORTH_CENTRAL',
            },
            'Zone2_Split': {
                'description': 'Split',
                'weight_pop': 160000,
                'lat': 43.51,
                'lon': 16.44,
                'macro_zone': 'HR_COAST_SOUTH',
            },
            'Zone3_Osijek': {
                'description': 'Osijek',
                'weight_pop': 96000,
                'lat': 45.55,
                'lon': 18.70,
                'macro_zone': 'HR_EAST',
            }
        }
    },
    "DE": {
        "full_name": "Germany",
        "macro_zones": {
            "DE_SOUTH": {"description": "Southern Germany"},
            "DE_EAST": {"description": "Eastern Germany"},
            "DE_NORTH": {"description": "Northern Germany"},
        },
        "wind_county_lookup": {},
        "solar_zones": {
            'Zone1_Bavaria': {
                'description': 'Southern Solar Belt',
                'weight_MW': 20000.0,
                'lat': 48.8,
                'lon': 11.4,
                'macro_zone': 'DE_SOUTH',
            },
            'Zone2_East': {
                'description': 'Eastern Germany (Brandenburg)',
                'weight_MW': 15000.0,
                'lat': 52.0,
                'lon': 13.5,
                'macro_zone': 'DE_EAST',
            },
            'Zone3_North': {
                'description': 'Northern Plains',
                'weight_MW': 10000.0,
                'lat': 53.5,
                'lon': 9.5,
                'macro_zone': 'DE_NORTH',
            }
        },
        "temp_points": {
            'Zone1_Berlin': {
                'description': 'Berlin/North',
                'weight_pop': 3600000,
                'lat': 52.52,
                'lon': 13.40,
                'macro_zone': 'DE_EAST',
            },
            'Zone2_Munich': {
                'description': 'Munich/South',
                'weight_pop': 1500000,
                'lat': 48.13,
                'lon': 11.58,
                'macro_zone': 'DE_SOUTH',
            },
            'Zone3_Cologne': {
                'description': 'Cologne/West',
                'weight_pop': 1000000,
                'lat': 50.93,
                'lon': 6.96,
                'macro_zone': 'DE_NORTH',
            }
        }
    }
}

# Runtime configuration
API_TOKEN = os.environ.get("NINJA_API_TOKEN", "7e3755ce21702fd48d9dabc34f21cb3215698c4e").strip()
HAS_NINJA_TOKEN = bool(API_TOKEN)
BASE_URL = "https://www.renewables.ninja/api"
HEADERS = {"Authorization": f"Token {API_TOKEN}"}

REQUEST_DELAY = 75
START_YEAR = 1980
END_YEAR = 2024

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data", "SC")
RAW_DATA_DIR = os.path.join(BASE_DATA_DIR, "raw_data")
HISTORICAL_DIR = os.path.join(BASE_DATA_DIR, "historical")
WIND_RAW_DIR = os.path.join(RAW_DATA_DIR, "openmeteo_wind")

os.makedirs(RAW_DATA_DIR, exist_ok=True)
os.makedirs(HISTORICAL_DIR, exist_ok=True)
os.makedirs(WIND_RAW_DIR, exist_ok=True)

IRENA_XLSX_PATH = os.path.join(BASE_DATA_DIR, "IRENA_Statistics_Extract_2025H2.xlsx")

ISO2_TO_ISO3 = {
    "HR": "HRV", "IT": "ITA", "SI": "SVN", "AT": "AUT", "DE": "DEU", "FR": "FRA",
    "ES": "ESP", "PT": "PRT", "NL": "NLD", "BE": "BEL", "DK": "DNK", "SE": "SWE",
    "FI": "FIN", "PL": "POL", "CZ": "CZE", "SK": "SVK", "HU": "HUN", "RO": "ROU",
    "BG": "BGR", "GR": "GRC", "IE": "IRL", "NO": "NOR", "CH": "CHE", "GB": "GBR",
}

ASSET_REFERENCE_YEAR_BY_COUNTRY = {
    "HR": 2023,
}

DATA_PER_COUNTRY_DIR = os.path.join(BASE_DATA_DIR, "data_per_country")
CUSTOM_ZONES_PATH = os.path.join(BASE_DATA_DIR, "custom_zones.csv")
NUTS3_CANDIDATES = [
    os.environ.get("NUTS3_GEOJSON_PATH", ""),
    os.path.join(BASE_DATA_DIR, "NUTS_RG_01M_2021_4326_LEVL_3.geojson"),
    os.path.join(SCRIPT_DIR, "..", "NUTS_RG_01M_2021_4326_LEVL_3.geojson"),
]

COUNTRY_FILE_MAP = {
    "AE": "united-arab-emirates", "AL": "albania", "AM": "armenia", "AR": "argentina",
    "AT": "austria", "AU": "australia", "AZ": "azerbaijan", "BA": "bosnia-and-herzegovina",
    "BB": "barbados", "BD": "bangladesh", "BE": "belgium", "BG": "bulgaria",
    "BH": "bahrain", "BO": "bolivia", "BR": "brazil", "BS": "the-bahamas",
    "BT": "bhutan", "BY": "belarus", "CA": "canada", "CH": "switzerland",
    "CL": "chile", "CN": "china", "CO": "colombia", "CR": "costa-rica",
    "CU": "cuba", "CV": "cape-verde", "CW": "curacao", "CY": "cyprus",
    "CZ": "czech-republic", "DE": "germany", "DJ": "djibouti", "DK": "denmark",
    "DM": "dominica", "DO": "dominican-republic", "DZ": "algeria", "EC": "ecuador",
    "EE": "estonia", "EG": "egypt", "ER": "eritrea", "ES": "spain",
    "ET": "ethiopia", "FI": "finland", "FJ": "fiji", "FM": "micronesia",
    "FO": "faroe-islands", "FR": "france", "GB": "united-kingdom", "GD": "grenada",
    "GE": "georgia", "GH": "ghana", "GM": "gambia", "GR": "greece",
    "GT": "guatemala", "GU": "guam", "HN": "honduras", "HR": "croatia",
    "HU": "hungary", "ID": "indonesia", "IE": "ireland", "IL": "israel",
    "IN": "india", "IR": "iran", "IS": "iceland", "IT": "italy",
    "JM": "jamaica", "JO": "jordan", "JP": "japan", "KE": "kenya",
    "KH": "cambodia", "KN": "saint-kitts-and-nevis", "KR": "south-korea", "KW": "kuwait",
    "KZ": "kazakhstan", "LA": "laos", "LK": "sri-lanka", "LT": "lithuania",
    "LU": "luxembourg", "LV": "latvia", "MA": "morocco", "ME": "montenegro",
    "MG": "madagascar", "MK": "north-macedonia", "MN": "mongolia", "MR": "mauritania",
    "MU": "mauritius", "MX": "mexico", "MZ": "mozambique", "NA": "namibia",
    "NE": "niger", "NG": "nigeria", "NI": "nicaragua", "NL": "netherlands",
    "NO": "norway", "NZ": "new-zealand", "OM": "oman", "PA": "panama",
    "PE": "peru", "PH": "philippines", "PK": "pakistan", "PL": "poland",
    "PR": "puerto-rico", "PT": "portugal", "RO": "romania", "RS": "serbia",
    "RU": "russia", "SA": "saudi-arabia", "SC": "seychelles", "SD": "sudan",
    "SE": "sweden", "SG": "singapore", "SI": "slovenia", "SK": "slovakia",
    "SN": "senegal", "SV": "el-salvador", "SY": "syria", "TD": "chad",
    "TH": "thailand", "TN": "tunisia", "TO": "tonga", "TR": "turkey",
    "TW": "taiwan", "TZ": "tanzania", "UA": "ukraine", "US": "usa",
    "UY": "uruguay", "UZ": "uzbekistan", "VE": "venezuela", "VN": "vietnam",
    "VU": "vanuatu", "WS": "samoa", "XK": "kosovo", "ZA": "south-africa",
}


def _print_banner(title: str):
    """Print a consistent section banner."""
    line = "=" * 60
    print(f"\n{line}\n{title}\n{line}")


# Spatial helpers

def _normalize_county_zone_name(value) -> str:
    """Normalize noisy county labels from the wind-farm database."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+Other name:.*$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _load_country_custom_zones(country_code: str) -> dict:
    """Read optional NUTS-to-custom-zone mappings from custom_zones.csv."""
    if not os.path.exists(CUSTOM_ZONES_PATH):
        return {}
    try:
        df = pd.read_csv(CUSTOM_ZONES_PATH)
    except Exception as e:
        print(f"  [WARN] Could not read custom zones file: {e}")
        return {}

    required = {"System", "zone_old", "zone_new"}
    if not required.issubset(df.columns):
        return {}

    mask = df["System"].astype(str).str.upper() == country_code.upper()
    sub = df.loc[mask, ["zone_old", "zone_new"]].dropna()
    return dict(zip(sub["zone_old"].astype(str), sub["zone_new"].astype(str)))


def _find_nuts3_geojson() -> str:
    """Locate an optional NUTS3 geometry file if available locally."""
    for candidate in NUTS3_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _attach_nuts_from_geometry(df: pd.DataFrame, country_code: str) -> pd.DataFrame:
    """
    Optional geospatial fallback: derive NUTS3 IDs from lat/lon if a NUTS
    geometry file and geopandas are available.
    """
    if not HAS_GEOPANDAS or df.empty:
        return df

    nuts_path = _find_nuts3_geojson()
    if not nuts_path:
        return df

    if "nuts_id" not in df.columns:
        df = df.copy()
        df["nuts_id"] = ""

    missing = df["nuts_id"].astype(str).str.strip().eq("")
    if not missing.any():
        return df

    try:
        nuts = gpd.read_file(nuts_path)
        if "NUTS_ID" not in nuts.columns:
            return df

        nuts = nuts[nuts["NUTS_ID"].astype(str).str.startswith(country_code.upper(), na=False)].copy()
        if nuts.empty:
            return df

        points = gpd.GeoDataFrame(
            df.loc[missing, ["lat", "lon"]].copy(),
            geometry=gpd.points_from_xy(df.loc[missing, "lon"], df.loc[missing, "lat"]),
            crs=4326,
        )
        joined = gpd.sjoin(points, nuts[["NUTS_ID", "geometry"]], how="left", predicate="within")
        df.loc[missing, "nuts_id"] = joined["NUTS_ID"].fillna("").values
    except Exception as e:
        print(f"  [WARN] Spatial NUTS enrichment skipped: {e}")

    return df


def enrich_wind_farms_with_spatial_metadata(farms_df: pd.DataFrame,
                                            country_code: str) -> pd.DataFrame:
    """
    Attach normalized county labels, optional NUTS IDs, and macro-zone labels
    to wind-farm records. This keeps the detailed plant-level truth while
    exposing a stable aggregation layer above it.
    """
    df = farms_df.copy()
    if "county_zone" not in df.columns:
        df["county_zone"] = ""

    df["county_zone_raw"] = df["county_zone"].fillna("").astype(str)
    df["county_zone"] = df["county_zone_raw"].map(_normalize_county_zone_name)
    df["nuts_id"] = ""
    df["macro_zone"] = ""

    reg = COUNTRY_REGISTRY.get(country_code.upper(), {})
    county_lookup = reg.get("wind_county_lookup", {})
    normalized_lookup = {
        _normalize_county_zone_name(alias): meta for alias, meta in county_lookup.items()
    }

    for alias, meta in normalized_lookup.items():
        exact_mask = df["county_zone"] == alias
        prefix_mask = df["county_zone"].str.startswith(alias, na=False)
        mask = exact_mask | prefix_mask
        if "nuts_id" in meta:
            df.loc[mask & df["nuts_id"].eq(""), "nuts_id"] = str(meta["nuts_id"])
        if "macro_zone" in meta:
            df.loc[mask & df["macro_zone"].eq(""), "macro_zone"] = str(meta["macro_zone"])

    df = _attach_nuts_from_geometry(df, country_code)

    custom_zone_lookup = _load_country_custom_zones(country_code)
    mask_macro_missing = df["macro_zone"].eq("") & df["nuts_id"].ne("")
    if custom_zone_lookup:
        df.loc[mask_macro_missing, "macro_zone"] = (
            df.loc[mask_macro_missing, "nuts_id"].map(custom_zone_lookup).fillna("")
        )

    macro_desc = {
        zone_id: meta.get("description", "")
        for zone_id, meta in reg.get("macro_zones", {}).items()
    }
    df["macro_zone_description"] = df["macro_zone"].map(macro_desc).fillna("")
    return df


def _safe_group_label(value, fallback: str = "UNASSIGNED") -> str:
    """Return a stable group label even when metadata is missing."""
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


# Renewables.ninja fetchers

def parse_ninja_index(index):
    """Parse index from Renewables.ninja."""
    try:
        numeric_index = pd.to_numeric(index, errors='coerce')
        if not numeric_index.isna().all():
            return pd.to_datetime(numeric_index, unit='ms')
    except (ValueError, TypeError, AttributeError):
        pass
    return pd.to_datetime(index, format='ISO8601', errors='coerce')


def fetch_ninja_yearly(lat: float, lon: float, year: int, endpoint: str) -> pd.DataFrame:
    """Fetch one year of Renewables.ninja PV output used for solar and temperature."""
    url = f"{BASE_URL}/data/{endpoint}"
    params = {
        "lat": lat, "lon": lon,
        "date_from": f"{year}-01-01",
        "date_to": f"{year}-12-31",
        "dataset": "merra2",
        "capacity": 1.0,
        "format": "json",
    }
    if endpoint == "pv":
        params.update({"system_loss": 0.1, "tracking": 0, "tilt": 35, "azim": 180, "raw": True})

    response = requests.get(url, headers=HEADERS, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(data["data"]).T
    df.index = parse_ninja_index(df.index)

    df = df.rename(columns={"electricity": "solar"})

    required_cols = ["solar", "temperature"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = 0.0

    return df[required_cols]


def fetch_zone_history(name: str, zone_config: dict, endpoint: str):
    """Fetch and update history for a single solar/temp zone via Ninja."""
    output_file = os.path.join(RAW_DATA_DIR, f"{name}.parquet")
    existing_df = None
    existing_years = set()

    if os.path.exists(output_file):
        try:
            existing_df = pd.read_parquet(output_file)
            existing_years = set(existing_df.index.year.unique())
            print(f"  [INFO] Found {len(existing_df)} hours for {name}")
        except Exception as e:
            print(f"  [WARN] Could not read existing file {output_file}: {e}")

    years_to_fetch = [y for y in range(START_YEAR, END_YEAR + 1) if y not in existing_years]

    if not years_to_fetch:
        print(f"  [OK] Zone {name} is up to date.")
        return existing_df

    print(f"  [FETCH] {name}: Fetching {len(years_to_fetch)} years...")
    new_data = []

    for year in years_to_fetch:
        print(f"    Year {year}...", end=" ", flush=True)
        max_retries = 3
        success = False

        for attempt in range(max_retries):
            try:
                df = fetch_ninja_yearly(zone_config['lat'], zone_config['lon'], year, endpoint)
                new_data.append(df)
                success = True
                print("OK")
                time.sleep(REQUEST_DELAY)
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                wait_time = 60 * (attempt + 1)
                print(f"\n      [NETWORK ERROR] {e}")
                print(f"      Waiting {wait_time}s before retry {attempt+1}/{max_retries}...")
                time.sleep(wait_time)
            except requests.exceptions.HTTPError as e:
                if "429" in str(e):
                    print(f"\n      [RATE LIMIT] Hit limit, waiting 300s...")
                    time.sleep(300)
                else:
                    print(f"\n      [HTTP ERROR] {e}")
                    break
            except Exception as e:
                print(f"\n      [ERR] {e}")
                break

        if not success:
            print(f"    [STOP] Persistent error on year {year}. Stopping this zone.")
            break

    if new_data:
        combined = pd.concat([existing_df] + new_data if existing_df is not None else new_data)
        combined = combined[~combined.index.duplicated(keep='first')].sort_index()
        combined.to_parquet(output_file)
        return combined

    return existing_df


# Open-Meteo wind fetchers

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_openmeteo_wind(lat: float, lon: float, date_from: str, date_to: str,
                         sleep_s: float = 0.25, max_retries: int = 8) -> pd.Series:
    """
    Fetch hourly wind speed at 100m from Open-Meteo archive API.
    Returns a pd.Series of wind speed (m/s) indexed by datetime (UTC).
    """
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "start_date": date_from,
        "end_date": date_to,
        "hourly": "windspeed_100m,windspeed_10m",
        "timezone": "UTC",
    }

    last_err = None
    for k in range(max_retries):
        try:
            r = requests.get(OPENMETEO_ARCHIVE_URL, params=params, timeout=60)
            if r.status_code == 429:
                wait = max(1.0, sleep_s * (2 ** k))
                time.sleep(wait)
                last_err = f"HTTP 429 (rate limit), waited {wait:.1f}s"
                continue
            r.raise_for_status()
            try:
                j = r.json()
            except ValueError as e:
                raise ValueError(f"Open-Meteo returned non-JSON response: {e}")

            if "hourly" not in j or "time" not in j["hourly"]:
                raise ValueError(f"Open-Meteo response missing hourly data: {list(j.keys())}")

            data = pd.DataFrame(j["hourly"])
            data["time"] = pd.to_datetime(data["time"], format="ISO8601",
                                          errors="coerce", utc=True)
            data = data.dropna(subset=["time"]).set_index("time").sort_index()

            if "windspeed_100m" in data.columns and data["windspeed_100m"].notna().any():
                ws = pd.to_numeric(data["windspeed_100m"], errors="coerce")
            elif "windspeed_10m" in data.columns and data["windspeed_10m"].notna().any():
                ws = pd.to_numeric(data["windspeed_10m"], errors="coerce")
                ws = ws * ((100.0 / 10.0) ** 0.143)
            else:
                raise ValueError("No wind speed column in Open-Meteo response")

            ws = ws.interpolate(method='linear', limit=3).bfill(limit=1).ffill(limit=1)

            time.sleep(sleep_s)
            return ws

        except Exception as e:
            last_err = str(e)
            wait = max(1.0, sleep_s * (2 ** k))
            time.sleep(wait)

    raise RuntimeError(f"Open-Meteo fetch failed after {max_retries} retries: {last_err}")


# Wind power-curve utilities

def fleet_power_curve(v: np.ndarray, cut_in: float = 3.5, rated: float = 13.0,
                      cut_out: float = 25.0, sigma: float = 1.5) -> np.ndarray:
    """
    Smoothed sigmoid fleet power curve.

    Parameters
    ----------
    v : array-like
        Wind speed in m/s.
    cut_in : float
        Cut-in speed (m/s).
    rated : float
        Rated speed (m/s).
    cut_out : float
        Cut-out speed (m/s).
    sigma : float
        Smoothing parameter for sigmoid transitions.

    Returns
    -------
    np.ndarray
        Capacity factor 0..1.
    """
    v = np.asarray(v, dtype=float)

    ramp_in = 1.0 / (1.0 + np.exp(-(v - cut_in) / (sigma * 0.5)))

    x = np.clip((v - cut_in) / max(1e-9, rated - cut_in), 0.0, 1.0)
    cubic = x ** 3

    ramp_out = 1.0 / (1.0 + np.exp((v - cut_out) / sigma))

    return np.clip(cubic * ramp_in * ramp_out, 0.0, 1.0)


# Wind pipeline

def load_wind_schema(country_code: str, use_database: bool = True,
                     n_wind_units: int = 4) -> pd.DataFrame:
    """
    Load the wind farm schema CSV for the given country.

    If `use_database=True` or no manual schema file exists, the schema is
    generated from the per-country wind-farm database.
    """
    schema_file = os.path.join(BASE_DATA_DIR, f"wind_schema_{country_code}.csv")

    if not use_database and os.path.exists(schema_file):
        df = pd.read_csv(schema_file)
        df["cap_mw_old"] = pd.to_numeric(df["cap_mw_old"], errors="coerce")
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
        df = df.dropna(subset=["cap_mw_old", "lat", "lon"])
        df = df[df["cap_mw_old"] > 0]

        print(f"  [SCHEMA] Loaded {len(df)} wind farm sites from {schema_file}")
        for unit in df["new_unit_name"].unique():
            sub = df[df["new_unit_name"] == unit]
            total_mw = sub["cap_mw_old"].sum()
            print(f"    {unit}: {len(sub)} site(s), {total_mw:.1f} MW total")

        return df

    print(f"  [AUTO] Building wind schema from database for {country_code}...")
    try:
        farms_df = load_wind_farms_from_database(country_code)
    except FileNotFoundError as e:
        if os.path.exists(schema_file):
            print(f"  [WARN] Database not found, falling back to manual schema")
            return load_wind_schema(country_code, use_database=False)
        raise FileNotFoundError(
            f"No wind data available for '{country_code}': "
            f"no manual schema at {schema_file} and {e}"
        )

    schema = auto_group_wind_farms(farms_df, country_code, n_groups=n_wind_units)

    auto_schema_file = os.path.join(
        BASE_DATA_DIR, f"wind_schema_{country_code}_auto.csv"
    )
    schema.to_csv(auto_schema_file, index=False)
    print(f"  [AUTO] Generated schema saved to: {auto_schema_file}")

    return schema


def _find_country_csv(country_code: str) -> str:
    """
    Locate the wind-farm CSV for a country in `data_per_country/`.
    """
    stem = COUNTRY_FILE_MAP.get(country_code.upper())
    if stem is None:
        reg = COUNTRY_REGISTRY.get(country_code.upper(), {})
        stem = reg.get("full_name", country_code).lower().replace(" ", "-")

    for prefix in ["★", ""]:
        candidate = os.path.join(DATA_PER_COUNTRY_DIR, f"{prefix}{stem}.csv")
        if os.path.exists(candidate):
            return candidate

    raise FileNotFoundError(
        f"No wind farm database file found for '{country_code}' "
        f"(tried ★{stem}.csv and {stem}.csv in {DATA_PER_COUNTRY_DIR})"
    )


def load_wind_farms_from_database(country_code: str) -> pd.DataFrame:
    """
    Read wind-farm data from the per-country CSV export.
    """
    csv_path = _find_country_csv(country_code)

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    basename = os.path.basename(csv_path).replace("\u2605", "[*]")
    print(f"  [DB] Reading {len(df)} farms from {basename}")

    cols = set(df.columns)
    is_normalized = "latitude" in cols and "capacity_mw" in cols

    if is_normalized:
        df = df.rename(columns={"latitude": "lat", "longitude": "lon",
                                "capacity_mw": "cap_mw"})
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
        df["cap_mw"] = pd.to_numeric(df["cap_mw"], errors="coerce")
    else:
        df = df.rename(columns={"latitude_decimal": "lat",
                                "longitude_decimal": "lon"})
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

        def _parse_kw(val):
            if pd.isna(val):
                return np.nan
            s = str(val).replace(",", "").replace(" ", "")
            s = s.lower().replace("kw", "").strip()
            try:
                return float(s) / 1000.0  # kW -> MW
            except (ValueError, TypeError):
                return np.nan

        if "area_location" in df.columns:
            df["cap_mw"] = df["area_location"].apply(_parse_kw)
        elif "power_kw" in df.columns:
            df["cap_mw"] = df["power_kw"].apply(_parse_kw)
        else:
            df["cap_mw"] = np.nan

        if "power_kw" in df.columns:
            mask_na = df["cap_mw"].isna()
            if mask_na.any():
                df.loc[mask_na, "cap_mw"] = df.loc[mask_na, "power_kw"].apply(_parse_kw)

    if "farm_name" not in df.columns:
        df["farm_name"] = [f"farm_{i}" for i in range(len(df))]

    if "county_zone" not in df.columns:
        df["county_zone"] = ""

    if "status" in df.columns:
        status_lower = df["status"].astype(str).str.strip().str.lower()
        keep_mask = status_lower.isin(["operational", "under construction"])
        n_before = len(df)
        dropped_statuses = df.loc[~keep_mask, "status"].astype(str).value_counts()
        df = df[keep_mask]
        n_dropped = n_before - len(df)
        if n_dropped > 0:
            dropped_summary = {str(k): int(v) for k, v in dropped_statuses.items()}
            print(f"  [DB] Filtered out {n_dropped} farms: {dropped_summary}")

    df = df.dropna(subset=["lat", "lon", "cap_mw"])
    df = df[df["cap_mw"] > 0]

    df = enrich_wind_farms_with_spatial_metadata(df, country_code)

    print(f"  [DB] {len(df)} usable farms, total {df['cap_mw'].sum():.1f} MW")

    cols = [
        "farm_name", "lat", "lon", "cap_mw", "status", "county_zone",
        "county_zone_raw", "nuts_id", "macro_zone", "macro_zone_description",
    ]
    for col in cols:
        if col not in df.columns:
            df[col] = ""

    return df[cols].reset_index(drop=True)


def auto_group_wind_farms(farms_df: pd.DataFrame, country_code: str,
                          n_groups: int = 4) -> pd.DataFrame:
    """
    Group wind farms into H2RES solver units.

    Croatia is intentionally grouped directly by active NUTS3 regions so the
    generated wind schema stays aligned with the HR NUTS3 solver-unit setup.
    Other countries keep the legacy K-Means grouping unless the model input
    data are explicitly changed for those systems.

    Parameters
    ----------
    farms_df : DataFrame from load_wind_farms_from_database()
    country_code : str, e.g. "HR"
    n_groups : int, number of H2RES wind units to create

    Returns
    -------
    DataFrame compatible with load_wind_schema() output:
        new_unit_name, technology, cap_mw_old, lat, lon, System, farm_name
    """
    n_farms = len(farms_df)
    k = min(n_groups, n_farms)

    use_hr_nuts_units = (
        country_code.upper() == "HR"
        and "nuts_id" in farms_df.columns
        and farms_df["nuts_id"].fillna("").astype(str).str.strip().ne("").any()
    )

    if use_hr_nuts_units:
        farms_df = farms_df.copy()
        farms_df["new_unit_name"] = (
            f"{country_code}_Wind_" + farms_df["nuts_id"].fillna("").astype(str).str.strip()
        )
        farms_df = farms_df[farms_df["new_unit_name"].ne(f"{country_code}_Wind_")]
    elif k < 2 or n_farms < 2:
        farms_df = farms_df.copy()
        farms_df["new_unit_name"] = f"{country_code}_WindPP"
    elif not HAS_SKLEARN:
        print("  [WARN] sklearn not available, using simple equal-split grouping")
        farms_df = farms_df.copy()
        labels = np.array_split(np.arange(n_farms), k)
        unit_labels = []
        for grp_idx, indices in enumerate(labels):
            suffix = "" if grp_idx == 0 else str(grp_idx)
            for _ in indices:
                unit_labels.append(f"{country_code}_WindPP{suffix}")
        farms_df["new_unit_name"] = unit_labels
    else:
        coords = farms_df[["lat", "lon"]].values
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(coords)

        cluster_caps = {}
        for c in range(k):
            cluster_caps[c] = farms_df.loc[labels == c, "cap_mw"].sum()

        sorted_clusters = sorted(cluster_caps, key=cluster_caps.get, reverse=True)
        label_to_unit = {}
        for rank, c in enumerate(sorted_clusters):
            suffix = "" if rank == 0 else str(rank)
            label_to_unit[c] = f"{country_code}_WindPP{suffix}"

        farms_df = farms_df.copy()
        farms_df["new_unit_name"] = [label_to_unit[l] for l in labels]

    schema = pd.DataFrame({
        "new_unit_name": farms_df["new_unit_name"],
        "technology": "WTON",
        "cap_mw_old": farms_df["cap_mw"],
        "lat": farms_df["lat"],
        "lon": farms_df["lon"],
        "System": country_code,
        "farm_name": farms_df["farm_name"],
        "county_zone": farms_df.get("county_zone", ""),
        "county_zone_raw": farms_df.get("county_zone_raw", ""),
        "nuts_id": farms_df.get("nuts_id", ""),
        "macro_zone": farms_df.get("macro_zone", ""),
        "macro_zone_description": farms_df.get("macro_zone_description", ""),
    })

    grouping_label = "NUTS3" if use_hr_nuts_units else "spatial clusters"
    print(f"\n  [AUTO-GROUP] {n_farms} farms -> {schema['new_unit_name'].nunique()} H2RES units ({grouping_label}):")
    for unit in schema["new_unit_name"].unique():
        sub = schema[schema["new_unit_name"] == unit]
        print(f"    {unit}: {len(sub)} farm(s), {sub['cap_mw_old'].sum():.1f} MW")

    return schema


def _grid_cluster_sites(farms_df: pd.DataFrame,
                        resolution_deg: float = 0.25) -> pd.DataFrame:
    """
    Cluster nearby farms into grid cells to reduce Open-Meteo API calls.
    Farms within the same grid cell share one wind speed fetch but keep
    individual capacities for weighting.

    Parameters
    ----------
    farms_df : DataFrame with lat, lon, cap_mw, farm_name columns
    resolution_deg : grid cell size in degrees (0.25° ≈ 28km)

    Returns
    -------
    DataFrame with columns: grid_lat, grid_lon, cap_mw, farm_names (list),
        n_farms, fetch_name (unique identifier for caching)
    """
    df = farms_df.copy()
    df["grid_lat"] = ((df["lat"] / resolution_deg).round() * resolution_deg).round(6)
    df["grid_lon"] = ((df["lon"] / resolution_deg).round() * resolution_deg).round(6)

    grouped = df.groupby(["grid_lat", "grid_lon"]).agg(
        cap_mw=("cap_mw", "sum"),
        n_farms=("farm_name", "count"),
        farm_names=("farm_name", list),
    ).reset_index()

    grouped["fetch_name"] = [
        f"grid_{row['grid_lat']:.3f}_{row['grid_lon']:.3f}"
        for _, row in grouped.iterrows()
    ]

    print(f"  [GRID] {len(farms_df)} farms -> {len(grouped)} grid cells "
          f"(resolution={resolution_deg}°)")

    return grouped


def _find_cache_by_coords(lat: float, lon: float) -> str:
    """
    Search existing cache files for a matching coordinate (±0.001°).
    """
    pattern = os.path.join(WIND_RAW_DIR, f"*_{lat:.3f}_{lon:.3f}.parquet")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def fetch_wind_site_cf(lat: float, lon: float, site_name: str) -> pd.Series:
    """
    Fetch wind speed for a single site and convert to CF via power curve.
    Caches raw data in WIND_RAW_DIR.

    Cache lookup is coordinate-based: any existing file at the same
    lat/lon (to 3 decimals) is reused regardless of site_name.
    New files are saved with the given site_name.
    """
    safe_name = site_name.replace(" ", "_").replace("/", "_")
    cache_file = os.path.join(WIND_RAW_DIR, f"{safe_name}_{lat:.3f}_{lon:.3f}.parquet")

    candidates = [cache_file]
    coord_match = _find_cache_by_coords(lat, lon)
    if coord_match and coord_match != cache_file:
        candidates.append(coord_match)

    for candidate in candidates:
        if os.path.exists(candidate):
            try:
                cached = pd.read_parquet(candidate)
                if "cf" in cached.columns and len(cached) > 8000:
                    print(f"    [CACHE] {site_name}: {len(cached)} hours (cached)")
                    return cached["cf"]
            except Exception as e:
                print(f"    [WARN] Cache read failed for {candidate}: {e}")

    print(f"    [FETCH] {site_name} ({lat:.3f}°N, {lon:.3f}°E)...", end=" ", flush=True)
    ws = fetch_openmeteo_wind(
        lat=lat, lon=lon,
        date_from=f"{START_YEAR}-01-01",
        date_to=f"{END_YEAR}-12-31"
    )

    cf = pd.Series(fleet_power_curve(ws.values), index=ws.index, name="cf")
    print(f"OK ({len(cf)} hours, mean CF={cf.mean():.3f})")

    cache_df = pd.DataFrame({"windspeed_100m": ws, "cf": cf})
    cache_df.to_parquet(cache_file)

    return cf


def _aggregate_site_curves_by_key(site_curves: list, group_key: str) -> tuple:
    """
    Aggregate detailed wind site curves into a higher-level grouping such as
    H2RES unit or macro zone using capacity-weighted hourly averages.
    """
    if not site_curves:
        return pd.DataFrame(), pd.DataFrame()

    idx = None
    for it in site_curves:
        idx = it["cf"].index if idx is None else idx.union(it["cf"].index)

    grouped = {}
    for it in site_curves:
        label = _safe_group_label(it.get(group_key, ""), f"UNASSIGNED_{group_key.upper()}")
        grouped.setdefault(label, []).append(it)

    profiles = {}
    meta_rows = []
    for label, items in grouped.items():
        weighted_sum = pd.Series(0.0, index=idx)
        weight_sum = pd.Series(0.0, index=idx)
        total_cap = 0.0

        for it in items:
            cap = float(it["cap"])
            total_cap += cap
            s = it["cf"].reindex(idx)
            m = s.notna()
            weighted_sum.loc[m] += cap * s.loc[m].astype(float)
            weight_sum.loc[m] += cap

        profiles[label] = (weighted_sum / weight_sum).fillna(0.0).clip(0.0, 1.0)
        meta_rows.append({
            "group_key": group_key,
            "group_value": label,
            "n_sites": len(items),
            "total_cap_mw": round(total_cap, 6),
            "units": "|".join(sorted({str(it.get("unit", "")) for it in items if it.get("unit", "")})),
            "farm_names": "|".join(sorted({str(it.get("farm_name", "")) for it in items if it.get("farm_name", "")})),
            "nuts_ids": "|".join(sorted({str(it.get("nuts_id", "")) for it in items if it.get("nuts_id", "")})),
            "counties": "|".join(sorted({str(it.get("county_zone", "")) for it in items if it.get("county_zone", "")})),
        })

    out_df = pd.DataFrame(profiles, index=idx)
    meta_df = pd.DataFrame(meta_rows).sort_values("group_value").reset_index(drop=True)
    return out_df, meta_df


def _save_profile_frame(df: pd.DataFrame, out_path: str):
    """Save a profile DataFrame to parquet with timezone-safe index handling."""
    if df.empty:
        return
    to_save = df.copy()
    if to_save.index.tz is not None:
        to_save.index = to_save.index.tz_localize(None)
    to_save.to_parquet(out_path)


def _build_transfer_matrix(site_meta_df: pd.DataFrame, source_col: str) -> pd.DataFrame:
    """
    Build a capacity-based transfer matrix from source zones to fixed wind units.

    Weight definition:
        weight(unit, zone) = cap(unit, zone) / cap(unit)
    """
    if site_meta_df.empty or source_col not in site_meta_df.columns:
        return pd.DataFrame(columns=[
            "unit_name", "source_zone", "weight", "source_cap_mw", "unit_cap_mw"
        ])

    work = site_meta_df.copy()
    work["unit"] = work["unit"].astype(str).str.strip()
    work["source_zone"] = work[source_col].fillna("").astype(str).str.strip()
    work["cap_mw"] = pd.to_numeric(work["cap_mw"], errors="coerce").fillna(0.0)
    work = work[(work["unit"] != "") & (work["cap_mw"] > 0)]
    work = work[work["source_zone"] != ""]
    if work.empty:
        return pd.DataFrame(columns=[
            "unit_name", "source_zone", "weight", "source_cap_mw", "unit_cap_mw"
        ])

    grouped = (
        work.groupby(["unit", "source_zone"], as_index=False)["cap_mw"]
        .sum()
        .rename(columns={"cap_mw": "source_cap_mw"})
    )
    unit_totals = (
        grouped.groupby("unit", as_index=False)["source_cap_mw"]
        .sum()
        .rename(columns={"source_cap_mw": "unit_cap_mw"})
    )
    merged = grouped.merge(unit_totals, on="unit", how="left")
    merged["weight"] = np.where(
        merged["unit_cap_mw"] > 0,
        merged["source_cap_mw"] / merged["unit_cap_mw"],
        0.0,
    )
    merged["weight"] = merged["weight"].clip(lower=0.0, upper=1.0)
    merged = merged.rename(columns={"unit": "unit_name"})
    merged = merged[[
        "unit_name", "source_zone", "weight", "source_cap_mw", "unit_cap_mw"
    ]]
    return merged.sort_values(["unit_name", "source_zone"]).reset_index(drop=True)


def _hourly_mean_8760(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse a time-indexed profile DataFrame to a representative 8760 shape."""
    if df.empty:
        return pd.DataFrame(index=np.arange(8760))

    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        if "time" in work.columns:
            work["time"] = pd.to_datetime(work["time"], errors="coerce")
            work = work.dropna(subset=["time"]).set_index("time")
        else:
            work.index = pd.to_datetime(work.index, errors="coerce")
    work = work[~work.index.isna()].sort_index()
    if work.index.tz is not None:
        work.index = work.index.tz_localize(None)

    work = work.select_dtypes(include=[np.number]).copy()
    if work.empty:
        return pd.DataFrame(index=np.arange(8760))

    idx = work.index
    leap_day = (idx.month == 2) & (idx.day == 29)
    work = work.loc[~leap_day]
    idx = work.index

    is_leap = idx.is_leap_year.astype(int)
    after_feb_28 = ((idx.month > 2) | ((idx.month == 2) & (idx.day > 28))).astype(int)
    adj_doy = idx.dayofyear - (is_leap * after_feb_28)
    hour_of_year = (adj_doy - 1) * 24 + idx.hour
    work = work.assign(hour_of_year=hour_of_year.values)

    hourly = work.groupby("hour_of_year").mean(numeric_only=True)
    hourly = hourly.reindex(np.arange(8760))
    hourly = hourly.interpolate(limit_direction="both").ffill().bfill()
    return hourly


def _load_units_from_genco(country_code: str, technology: str) -> pd.DataFrame:
    """Load generator rows for one technology from genco_data."""
    genco_path = os.path.join(BASE_DATA_DIR, f"genco_data_{country_code}_sdewes.csv")
    if not os.path.exists(genco_path):
        return pd.DataFrame()

    df = pd.read_csv(genco_path)
    tech_mask = df["technology"].astype(str).str.upper() == str(technology).upper()
    subset = df.loc[tech_mask].copy()
    if "cap_mw" in subset.columns:
        subset["cap_mw"] = pd.to_numeric(subset["cap_mw"], errors="coerce").fillna(0.0)
    return subset


def _infer_macro_zone_from_unit_name(unit_name: str, macro_zones: list[str]) -> Optional[str]:
    unit_upper = str(unit_name or "").upper()
    for zone in macro_zones:
        if str(zone).upper() in unit_upper:
            return zone
    return None


def _build_solver_transfer_from_macro_units(unit_df: pd.DataFrame,
                                            macro_zones: list[str]) -> pd.DataFrame:
    """
    Build a macro-zone transfer matrix for fixed solar solver units.

    The current HR solar layer uses one solver unit per macro zone, so the
    mapping is identity-like but kept in transfer-matrix form for consistency
    with the wind pipeline.
    """
    rows = []
    for _, row in unit_df.iterrows():
        unit_name = str(row.get("unit_name", "")).strip()
        cap_mw = float(row.get("cap_mw", 0.0) or 0.0)
        if not unit_name or cap_mw <= 0:
            continue
        macro_zone = _infer_macro_zone_from_unit_name(unit_name, macro_zones)
        if not macro_zone:
            continue
        rows.append({
            "unit_name": unit_name,
            "source_zone": macro_zone,
            "weight": 1.0,
            "source_cap_mw": cap_mw,
            "unit_cap_mw": cap_mw,
        })
    return pd.DataFrame(rows)


def _default_solar_template() -> dict:
    """Fallback PHOT row template used if no solar rows already exist in genco_data."""
    return {
        "unit_name": "",
        "cap_mw": 0.0,
        "fuel_type": "Solar",
        "decom_start_existing_cap": 15,
        "life_time": 25,
        "decom_start_new": 20,
        "final_life_cap": 1.0,
        "max_inv_period": 10000.0,
        "cap_factor": 1,
        "efficiency": 1.0,
        "cost_no_fuel": 0,
        "cap_inv_cost": 550000.0,
        "RampingCost": 0.0,
        "CO2Intensity": 0.0,
        "technology": "PHOT",
        "RampUpRate": 1.0,
        "RampDownRate": 1.0,
        "PrimaryReserve": "N",
        "SecondaryReserve": "N",
        "Stab_factor": 1,
        "CHPType": "N",
        "STOCapacity": np.nan,
        "STOSelfDischarge": np.nan,
        "STOMaxChargingPower": np.nan,
        "STOChargingEfficiency": np.nan,
        "CHPPowerToHeat": np.nan,
        "CHPPowerLossFactor": np.nan,
        "CHPMaxHeat": np.nan,
    }


def rebuild_country_solar_asset_layer(country_code: str,
                                      solar_macro_profiles: pd.DataFrame,
                                      macro_zone_order: list[str],
                                      asset_reference_year: int,
                                      target_solar_mw: Optional[float]):
    """
    Rebuild the country solar asset layer so preprocessing, genco_data, and
    solver-facing solar artifacts share the same reference year.
    """
    cc = str(country_code or "HR").upper()
    genco_path = os.path.join(BASE_DATA_DIR, f"genco_data_{cc}_sdewes.csv")
    ncre_path = os.path.join(BASE_DATA_DIR, f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv")
    solar_profiles_path = os.path.join(BASE_DATA_DIR, f"{cc}_solar_macro_profiles.parquet")
    solar_meta_path = os.path.join(BASE_DATA_DIR, f"{cc}_solar_macro_metadata.csv")
    solar_unit_meta_path = os.path.join(BASE_DATA_DIR, f"{cc}_solar_unit_metadata.csv")
    solar_transfer_path = os.path.join(BASE_DATA_DIR, f"{cc}_solar_transfer_macro_to_units.csv")
    asset_meta_path = os.path.join(BASE_DATA_DIR, f"{cc}_asset_layer_metadata.json")

    if not os.path.exists(genco_path):
        raise FileNotFoundError(f"Missing genco_data file: {genco_path}")
    if not os.path.exists(ncre_path):
        raise FileNotFoundError(f"Missing ncre availability file: {ncre_path}")

    genco_df = pd.read_csv(genco_path)
    old_solar_df = genco_df[genco_df["technology"].astype(str).str.upper() == "PHOT"].copy()
    template = _default_solar_template()
    if not old_solar_df.empty:
        template.update(old_solar_df.iloc[0].to_dict())

    zone_caps = {}
    if target_solar_mw and target_solar_mw > 0:
        first_hour = solar_macro_profiles.iloc[0] if not solar_macro_profiles.empty else pd.Series(dtype=float)
        for zone in macro_zone_order:
            zone_caps[zone] = float(target_solar_mw) * 0.0
        if solar_macro_profiles.shape[1] > 0:
            weights = {}
            for zone in macro_zone_order:
                weights[zone] = 1.0
            weight_sum = float(sum(weights.values()))
            for zone in macro_zone_order:
                zone_caps[zone] = float(target_solar_mw) * weights[zone] / max(weight_sum, 1.0)
    else:
        target_solar_mw = float(old_solar_df["cap_mw"].sum()) if not old_solar_df.empty else 0.0
        for zone in macro_zone_order:
            zone_caps[zone] = float(target_solar_mw) / max(len(macro_zone_order), 1)

    if solar_macro_profiles.empty:
        raise ValueError("Solar macro profiles are empty; cannot rebuild solar asset layer.")

    solar_meta_df = pd.read_csv(os.path.join(BASE_DATA_DIR, f"{cc}_macro_climate_metadata.csv"))
    solar_meta_df = solar_meta_df[solar_meta_df["channel"].astype(str).str.lower() == "solar"].copy()
    solar_meta_df["macro_zone"] = solar_meta_df["macro_zone"].astype(str)
    if not solar_meta_df.empty:
        solar_meta_df["weight_value"] = pd.to_numeric(solar_meta_df["weight_value"], errors="coerce").fillna(0.0)
        total_weight = float(solar_meta_df["weight_value"].sum())
        if total_weight > 0 and target_solar_mw > 0:
            for zone in macro_zone_order:
                share = float(
                    solar_meta_df.loc[solar_meta_df["macro_zone"] == zone, "weight_value"].sum()
                ) / total_weight
                zone_caps[zone] = float(target_solar_mw) * share

    new_rows = []
    for zone in macro_zone_order:
        row = dict(template)
        row["unit_name"] = f"{cc}_Solar_{zone}"
        row["cap_mw"] = round(float(zone_caps.get(zone, 0.0)), 6)
        row["fuel_type"] = "Solar"
        row["technology"] = "PHOT"
        row["cap_inv_cost"] = float(template.get("cap_inv_cost", 550000.0) or 550000.0)
        row["max_inv_period"] = float(template.get("max_inv_period", 10000.0) or 10000.0)
        new_rows.append(row)

    keep_mask = genco_df["technology"].astype(str).str.upper() != "PHOT"
    genco_new = pd.concat([genco_df.loc[keep_mask].copy(), pd.DataFrame(new_rows)], ignore_index=True)
    genco_new.to_csv(genco_path, index=False)

    solar_hourly = _hourly_mean_8760(solar_macro_profiles[macro_zone_order].copy())
    ncre_df = pd.read_csv(ncre_path)
    solar_unit_names = [row["unit_name"] for row in new_rows]
    old_solar_cols = [col for col in ncre_df.columns if col in set(old_solar_df.get("unit_name", []))]
    ncre_new = ncre_df.drop(columns=old_solar_cols, errors="ignore").copy()
    hour_of_year = (pd.to_numeric(ncre_new["period"], errors="coerce").fillna(1).astype(int) - 1) % 8760
    for zone, unit_name in zip(macro_zone_order, solar_unit_names):
        zone_series = solar_hourly[zone].reindex(hour_of_year.values).to_numpy(dtype=float)
        ncre_new[unit_name] = np.clip(zone_series, 0.0, 1.0)

    ordered_cols = []
    for col in ncre_df.columns:
        if col in old_solar_cols:
            if not ordered_cols or ordered_cols[-1] not in solar_unit_names:
                ordered_cols.extend([name for name in solar_unit_names if name not in ordered_cols])
        elif col in ncre_new.columns and col not in ordered_cols:
            ordered_cols.append(col)
    for col in ncre_new.columns:
        if col not in ordered_cols:
            ordered_cols.append(col)
    ncre_new = ncre_new[ordered_cols]
    ncre_new.to_csv(ncre_path, index=False)

    _save_profile_frame(solar_macro_profiles[macro_zone_order].copy(), solar_profiles_path)
    meta_rows = []
    for zone in macro_zone_order:
        cap_mw = float(zone_caps.get(zone, 0.0))
        meta_rows.append({
            "channel": "solar",
            "macro_zone": zone,
            "weight_basis": "installed_cap_mw",
            "weight_value": round(cap_mw, 6),
            "description": "Asset-year-consistent macro solar aggregation",
        })
    pd.DataFrame(meta_rows).to_csv(solar_meta_path, index=False)

    unit_meta = pd.DataFrame({
        "unit_name": solar_unit_names,
        "macro_zone": macro_zone_order,
        "cap_mw": [round(float(zone_caps.get(zone, 0.0)), 6) for zone in macro_zone_order],
        "asset_reference_year": [int(asset_reference_year)] * len(macro_zone_order),
    })
    unit_meta.to_csv(solar_unit_meta_path, index=False)

    transfer_df = _build_solver_transfer_from_macro_units(unit_meta, macro_zone_order)
    transfer_df.to_csv(solar_transfer_path, index=False)

    wind_irena_mw = read_irena_installed_capacity(cc, "onshore wind", year=asset_reference_year)
    wind_genco = _load_units_from_genco(cc, "WTON")
    wind_genco_total = float(wind_genco["cap_mw"].sum()) if not wind_genco.empty else 0.0
    asset_meta = {
        "country_code": cc,
        "asset_reference_year": int(asset_reference_year),
        "solar_reference_installed_cap_mw": round(float(target_solar_mw or 0.0), 6),
        "solar_genco_total_cap_mw": round(float(sum(zone_caps.values())), 6),
        "wind_irena_installed_cap_mw": round(float(wind_irena_mw), 6) if wind_irena_mw else None,
        "wind_genco_total_cap_mw": round(float(wind_genco_total), 6),
        "solar_solver_units": solar_unit_names,
        "solar_macro_zones": macro_zone_order,
        "note": (
            "Solar solver units are rebuilt to the locked asset year. "
            "Wind remains metadata-driven and is reported for transparency."
        ),
    }
    with open(asset_meta_path, "w", encoding="utf-8") as f:
        json.dump(asset_meta, f, indent=2)

    print(f"  [ASSET] Solar asset layer rebuilt for {cc} ({asset_reference_year}).")
    print(f"  [ASSET] genco_data updated: {genco_path}")
    print(f"  [ASSET] ncre availability updated: {ncre_path}")
    print(f"  [ASSET] Solar macro profiles saved: {solar_profiles_path}")
    print(f"  [ASSET] Solar transfer matrix saved: {solar_transfer_path}")


def read_irena_targets(country_code: str, year: int = None) -> dict:
    """
    Read IRENA CF targets from the XLSX file.
    Returns {year: actual_cf} for onshore wind.
    """
    if not os.path.exists(IRENA_XLSX_PATH):
        print(f"  [IRENA] XLSX not found: {IRENA_XLSX_PATH}")
        return _read_irena_fallback_csv(country_code)

    iso3 = ISO2_TO_ISO3.get(country_code.upper())
    if not iso3:
        print(f"  [IRENA] No ISO3 mapping for '{country_code}'. "
              f"Wind CF will NOT be IRENA-calibrated (alpha=1.0).")
        return {}

    try:
        df = pd.read_excel(IRENA_XLSX_PATH, sheet_name="Country")

        def find_col(candidates):
            cols = {c.lower(): c for c in df.columns}
            for cand in candidates:
                if cand.lower() in cols:
                    return cols[cand.lower()]
            return None

        col_iso3 = find_col(["ISO3 code", "ISO3", "iso3 code"])
        col_year = find_col(["Year", "year"])
        col_tech = find_col(["Technology", "technology"])
        col_gen = find_col(["Electricity Generation (GWh)", "Electricity Generation",
                           "generation (gwh)", "generation_gwh"])
        col_cap = find_col(["Electricity Installed Capacity (MW)",
                           "Electricity Installed Capacity", "capacity_mw"])

        missing = [n for n, c in [("ISO3", col_iso3), ("Year", col_year),
                                   ("Tech", col_tech), ("Gen", col_gen),
                                   ("Cap", col_cap)] if c is None]
        if missing:
            print(f"  [IRENA] XLSX missing columns: {missing}. Found: {list(df.columns)[:10]}...")
            return _read_irena_fallback_csv(country_code)

        df[col_iso3] = df[col_iso3].astype(str).str.strip().str.upper()
        df[col_year] = pd.to_numeric(df[col_year], errors="coerce")
        df[col_tech] = df[col_tech].astype(str).str.strip()
        df[col_gen] = pd.to_numeric(df[col_gen], errors="coerce")
        df[col_cap] = pd.to_numeric(df[col_cap], errors="coerce")

        tech_lower = df[col_tech].astype(str).str.strip().str.lower()
        mask = (df[col_iso3] == iso3) & (tech_lower.str.contains("onshore wind", na=False))
        sub = df[mask].copy()

        targets = {}
        for _, row in sub.iterrows():
            y = int(row[col_year])
            cap = float(row[col_cap])
            gen = float(row[col_gen])
            if cap > 0 and gen > 0:
                cf = (gen * 1000) / (cap * 8760)
                targets[y] = float(cf)

        if targets:
            print(f"  [IRENA XLSX] Loaded CF targets for {len(targets)} years ({min(targets)}–{max(targets)})")
        else:
            print(f"  [IRENA XLSX] No onshore wind data for {iso3}")

        return targets

    except Exception as e:
        print(f"  [IRENA XLSX] Error reading: {e}")
        return _read_irena_fallback_csv(country_code)


def _read_irena_fallback_csv(country_code: str) -> dict:
    """Fallback: read IRENA data from CSV."""
    fallback_path = os.path.join(HISTORICAL_DIR, "irena_fallback.csv")
    if not os.path.exists(fallback_path):
        print(f"  [IRENA] No fallback CSV at {fallback_path}")
        return {}

    try:
        df = pd.read_csv(fallback_path)
        mask = (df["CountryCode"] == country_code)
        if "Technology" in df.columns:
            mask = mask & df["Technology"].str.lower().str.contains("onshore wind", na=False)
        sub = df[mask]

        targets = {}
        for _, row in sub.iterrows():
            y = int(row["Year"])
            cap = float(row["Capacity_MW"])
            gen = float(row["Generation_GWh"])
            if cap > 0 and gen > 0:
                targets[y] = (gen * 1000) / (cap * 8760)

        if targets:
            print(f"  [IRENA CSV] Loaded {len(targets)} years from fallback CSV")
        return targets
    except Exception as e:
        print(f"  [IRENA CSV] Error: {e}")
        return {}


def _find_irena_column(df: pd.DataFrame, candidates: list):
    """Find a column in an IRENA table while tolerating naming variants."""
    cols = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if str(cand).lower() in cols:
            return cols[str(cand).lower()]
    return None


def get_asset_reference_year(country_code: str) -> int:
    """Return the locked asset reference year for a country."""
    cc = str(country_code or "HR").upper()
    return int(ASSET_REFERENCE_YEAR_BY_COUNTRY.get(cc, 2023))


def read_irena_installed_capacity(country_code: str,
                                  technology_keyword: str,
                                  year: int = None) -> Optional[float]:
    """
    Read installed capacity (MW) for a specific technology and year from IRENA.

    Parameters
    ----------
    country_code : str
        ISO2 code, for example ``HR``.
    technology_keyword : str
        Technology match, for example ``"solar photovoltaic"`` or
        ``"onshore wind"``.
    year : int, optional
        Year to read. Defaults to the configured asset reference year.
    """
    if not os.path.exists(IRENA_XLSX_PATH):
        return None

    iso3 = ISO2_TO_ISO3.get(str(country_code or "").upper())
    if not iso3:
        return None

    target_year = int(year or get_asset_reference_year(country_code))

    try:
        df = pd.read_excel(IRENA_XLSX_PATH, sheet_name="Country")
        col_iso3 = _find_irena_column(df, ["ISO3 code", "ISO3", "iso3 code"])
        col_year = _find_irena_column(df, ["Year", "year"])
        col_tech = _find_irena_column(df, ["Technology", "technology"])
        col_cap = _find_irena_column(df, [
            "Electricity Installed Capacity (MW)",
            "Electricity Installed Capacity",
            "capacity_mw",
        ])
        if any(col is None for col in [col_iso3, col_year, col_tech, col_cap]):
            return None

        work = df[[col_iso3, col_year, col_tech, col_cap]].copy()
        work[col_iso3] = work[col_iso3].astype(str).str.strip().str.upper()
        work[col_year] = pd.to_numeric(work[col_year], errors="coerce")
        work[col_cap] = pd.to_numeric(work[col_cap], errors="coerce")
        tech_mask = work[col_tech].astype(str).str.lower().str.contains(
            str(technology_keyword).lower(),
            na=False,
        )
        sub = work[
            (work[col_iso3] == iso3)
            & (work[col_year] == target_year)
            & tech_mask
        ]
        if sub.empty:
            return None
        capacities = pd.to_numeric(sub[col_cap], errors="coerce").dropna()
        capacities = capacities[capacities > 0]
        if capacities.empty:
            return None
        value = float(capacities.max())
        return value if value > 0 else None
    except Exception:
        return None


def _scale_weighted_configs_to_target(configs: dict,
                                      weight_key: str,
                                      target_total: float) -> dict:
    """Scale weight-based zone configs so their total matches ``target_total``."""
    out = {}
    positive_total = sum(
        float(cfg.get(weight_key, 0.0) or 0.0)
        for cfg in configs.values()
        if float(cfg.get(weight_key, 0.0) or 0.0) > 0
    )
    if positive_total <= 0 or float(target_total) <= 0:
        return {name: dict(cfg) for name, cfg in configs.items()}

    scale = float(target_total) / positive_total
    for name, cfg in configs.items():
        new_cfg = dict(cfg)
        weight = float(cfg.get(weight_key, 0.0) or 0.0)
        new_cfg[weight_key] = weight * scale
        out[name] = new_cfg
    return out


def read_irena_solar_targets(country_code: str,
                             recent_years: int = 5,
                             min_capacity_mw: float = 10.0) -> dict:
    """
    Read recent IRENA solar PV capacity-factor targets.

    Returns {year: actual_cf}. Only complete years with positive generation and
    a minimum installed capacity are used, because very early PV years are too
    small to be a stable national calibration target.
    """
    if not os.path.exists(IRENA_XLSX_PATH):
        print(f"  [IRENA SOLAR] XLSX not found: {IRENA_XLSX_PATH}")
        return _read_irena_solar_fallback_csv(country_code, recent_years, min_capacity_mw)

    iso3 = ISO2_TO_ISO3.get(country_code.upper())
    if not iso3:
        print(f"  [IRENA SOLAR] No ISO3 mapping for '{country_code}'. "
              f"PV CF will NOT be IRENA-calibrated (alpha=1.0).")
        return {}

    try:
        df = pd.read_excel(IRENA_XLSX_PATH, sheet_name="Country")
        col_iso3 = _find_irena_column(df, ["ISO3 code", "ISO3", "iso3 code"])
        col_year = _find_irena_column(df, ["Year", "year"])
        col_tech = _find_irena_column(df, ["Technology", "technology"])
        col_gen = _find_irena_column(df, [
            "Electricity Generation (GWh)", "Electricity Generation",
            "generation (gwh)", "generation_gwh"
        ])
        col_cap = _find_irena_column(df, [
            "Electricity Installed Capacity (MW)",
            "Electricity Installed Capacity", "capacity_mw"
        ])

        missing = [name for name, col in [
            ("ISO3", col_iso3), ("Year", col_year), ("Tech", col_tech),
            ("Gen", col_gen), ("Cap", col_cap)
        ] if col is None]
        if missing:
            print(f"  [IRENA SOLAR] XLSX missing columns: {missing}. "
                  f"Found: {list(df.columns)[:10]}...")
            return _read_irena_solar_fallback_csv(country_code, recent_years, min_capacity_mw)

        df[col_iso3] = df[col_iso3].astype(str).str.strip().str.upper()
        df[col_year] = pd.to_numeric(df[col_year], errors="coerce")
        df[col_tech] = df[col_tech].astype(str).str.strip()
        df[col_gen] = pd.to_numeric(df[col_gen], errors="coerce")
        df[col_cap] = pd.to_numeric(df[col_cap], errors="coerce")

        tech_lower = df[col_tech].astype(str).str.lower()
        mask = (
            (df[col_iso3] == iso3)
            & tech_lower.str.contains("solar photovoltaic", na=False)
        )
        sub = df[mask].copy()

        targets = {}
        for _, row in sub.iterrows():
            if pd.isna(row[col_year]) or pd.isna(row[col_cap]) or pd.isna(row[col_gen]):
                continue
            y = int(row[col_year])
            cap = float(row[col_cap])
            gen = float(row[col_gen])
            if cap >= min_capacity_mw and gen > 0:
                cf = (gen * 1000.0) / (cap * 8760.0)
                if 0.0 < cf < 1.0:
                    targets[y] = float(cf)

        if recent_years and targets:
            keep_years = sorted(targets)[-int(recent_years):]
            targets = {y: targets[y] for y in keep_years}

        if targets:
            print(f"  [IRENA SOLAR] Loaded PV CF targets for {len(targets)} years "
                  f"({min(targets)}-{max(targets)})")
        else:
            print(f"  [IRENA SOLAR] No complete solar PV data for {iso3}")
        return targets

    except Exception as e:
        print(f"  [IRENA SOLAR] Error reading XLSX: {e}")
        return _read_irena_solar_fallback_csv(country_code, recent_years, min_capacity_mw)


def _read_irena_solar_fallback_csv(country_code: str,
                                   recent_years: int = 5,
                                   min_capacity_mw: float = 10.0) -> dict:
    """Fallback: read solar PV IRENA targets from historical/irena_fallback.csv."""
    fallback_path = os.path.join(HISTORICAL_DIR, "irena_fallback.csv")
    if not os.path.exists(fallback_path):
        print(f"  [IRENA SOLAR] No fallback CSV at {fallback_path}")
        return {}

    try:
        df = pd.read_csv(fallback_path)
        mask = df["CountryCode"].astype(str).str.upper() == country_code.upper()
        if "Technology" in df.columns:
            mask = mask & df["Technology"].astype(str).str.lower().str.contains(
                "solar photovoltaic", na=False
            )
        sub = df[mask]

        targets = {}
        for _, row in sub.iterrows():
            y = int(row["Year"])
            cap = float(row["Capacity_MW"])
            gen = float(row["Generation_GWh"])
            if cap >= min_capacity_mw and gen > 0:
                cf = (gen * 1000.0) / (cap * 8760.0)
                if 0.0 < cf < 1.0:
                    targets[y] = float(cf)

        if recent_years and targets:
            keep_years = sorted(targets)[-int(recent_years):]
            targets = {y: targets[y] for y in keep_years}
        if targets:
            print(f"  [IRENA SOLAR CSV] Loaded {len(targets)} years from fallback CSV")
        return targets
    except Exception as e:
        print(f"  [IRENA SOLAR CSV] Error: {e}")
        return {}


def calibrate_solar_profiles_to_irena(solar_nat: pd.Series,
                                      solar_zone_df: pd.DataFrame,
                                      solar_macro_df: pd.DataFrame,
                                      irena_targets: dict) -> tuple:
    """Scale national, zone, and macro PV capacity factors to match IRENA."""
    if solar_nat is None or solar_nat.empty or not irena_targets:
        return solar_nat, solar_zone_df, solar_macro_df, {
            "calibration_alpha": 1.0,
            "target_cf": None,
            "national_cf_before": None,
            "national_cf_after": None,
        }

    target_cf = float(np.mean(list(irena_targets.values())))
    national_cf_before = float(pd.Series(solar_nat).astype(float).mean())
    if national_cf_before <= 0:
        return solar_nat, solar_zone_df, solar_macro_df, {
            "calibration_alpha": 1.0,
            "target_cf": target_cf,
            "national_cf_before": national_cf_before,
            "national_cf_after": national_cf_before,
        }

    alpha = float(np.clip(target_cf / national_cf_before, 0.1, 5.0))
    solar_nat_cal = (solar_nat.astype(float) * alpha).clip(0.0, 1.0)
    solar_zone_cal = solar_zone_df.copy()
    solar_macro_cal = solar_macro_df.copy()

    if not solar_zone_cal.empty:
        solar_zone_cal = (solar_zone_cal.astype(float) * alpha).clip(0.0, 1.0)
    if not solar_macro_cal.empty:
        solar_macro_cal = (solar_macro_cal.astype(float) * alpha).clip(0.0, 1.0)

    national_cf_after = float(solar_nat_cal.mean())

    print(f"\n  [SOLAR CALIBRATION] IRENA PV alpha = {alpha:.4f}")
    print(f"    IRENA target CF (mean of {len(irena_targets)} years: "
          f"{min(irena_targets)}-{max(irena_targets)}) = {target_cf:.4f}")
    print(f"    Simulated national mean CF (before) = {national_cf_before:.4f}")
    print(f"    Calibrated national mean CF (after)  = {national_cf_after:.4f}")

    return solar_nat_cal, solar_zone_cal, solar_macro_cal, {
        "calibration_alpha": alpha,
        "target_cf": target_cf,
        "national_cf_before": national_cf_before,
        "national_cf_after": national_cf_after,
    }


def _save_solar_calibration_metadata(country_code: str, calibration: dict,
                                     irena_targets: dict):
    """Save PV calibration metadata next to the climate artifacts."""
    if not irena_targets:
        return

    meta = {
        "country": country_code,
        "technology": "solar photovoltaic",
        "calibration_alpha": round(float(calibration["calibration_alpha"]), 6),
        "irena_target_cf_mean": round(float(calibration["target_cf"]), 6),
        "irena_years_used": sorted(list(irena_targets.keys())),
        "irena_cf_per_year": {
            str(k): round(float(v), 6) for k, v in sorted(irena_targets.items())
        },
        "national_cf_before_calibration": round(float(calibration["national_cf_before"]), 6),
        "national_cf_after_calibration": round(float(calibration["national_cf_after"]), 6),
        "data_period": f"{START_YEAR}-{END_YEAR}",
        "note": "Solar PV profiles are scaled nationally; regional PV weights stay unchanged.",
    }

    out_path = os.path.join(BASE_DATA_DIR, f"{country_code}_solar_calibration_metadata.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  [IRENA SOLAR] Calibration metadata saved to: {out_path}")


def calibrate_wind_pre_aggregation(site_curves: list, irena_targets: dict) -> float:
    """
    Pre-aggregation calibration:
    Find alpha such that the national capacity-weighted mean CF
    equals the IRENA target CF.

    Parameters
    ----------
    site_curves : list of dict
        [{"unit": str, "cap": float, "cf": pd.Series}, ...]
    irena_targets : dict
        {year: actual_cf} from IRENA.

    Returns
    -------
    float
        Optimal alpha scaling factor.
    """
    if not irena_targets or not site_curves:
        return 1.0

    target_cf = float(np.mean(list(irena_targets.values())))

    def national_mean(alpha):
        total_cap = sum(it["cap"] for it in site_curves)
        if total_cap == 0:
            return 0.0
        weighted_sum = 0.0
        for it in site_curves:
            cap = float(it["cap"])
            cf_vals = np.clip(alpha * it["cf"].values.astype(float), 0.0, 1.0)
            weighted_sum += cap * float(np.nanmean(cf_vals))
        return weighted_sum / total_cap

    lo, hi = 0.0, 10.0

    while national_mean(hi) < target_cf and hi < 1e6:
        hi *= 2.0

    for _ in range(20):
        mid = (lo + hi) / 2.0
        if national_mean(mid) < target_cf:
            lo = mid
        else:
            hi = mid

    alpha = float(np.clip((lo + hi) / 2.0, 0.1, 5.0))

    print(f"\n  [CALIBRATION] Pre-aggregation alpha = {alpha:.4f}")
    print(f"    IRENA target CF (mean of {len(irena_targets)} years: "
          f"{min(irena_targets)}-{max(irena_targets)}) = {target_cf:.4f}")
    print(f"    Simulated national mean CF (before) = {national_mean(1.0):.4f}")
    print(f"    Calibrated national mean CF (after)  = {national_mean(alpha):.4f}")

    return alpha


def _save_irena_calibration_metadata(country_code: str, alpha: float,
                                      irena_targets: dict, national_cf_before: float,
                                      national_cf_after: float):
    """
    Save IRENA calibration metadata to a JSON file for reproducibility.
    Stored next to HR_data.parquet as HR_calibration_metadata.json.
    """
    meta = {
        "country": country_code,
        "calibration_alpha": round(float(alpha), 6),
        "irena_target_cf_mean": round(float(np.mean(list(irena_targets.values()))), 6),
        "irena_years_used": sorted(list(irena_targets.keys())),
        "irena_cf_per_year": {str(k): round(float(v), 6) for k, v in sorted(irena_targets.items())},
        "national_cf_before_calibration": round(float(national_cf_before), 6),
        "national_cf_after_calibration": round(float(national_cf_after), 6),
        "data_period": f"{START_YEAR}-{END_YEAR}"
    }
    
    out_path = os.path.join(BASE_DATA_DIR, f"{country_code}_calibration_metadata.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  [IRENA] Calibration metadata saved to: {out_path}")


def build_wind_profiles(country_code: str, use_database: bool = True,
                        n_wind_units: int = 4,
                        grid_threshold: int = 100,
                        grid_resolution: float = 0.25) -> pd.Series:
    """
    Build a national wind profile and supporting detailed outputs.
    """
    _print_banner("OPEN-METEO WIND PIPELINE")

    schema = load_wind_schema(country_code, use_database=use_database,
                              n_wind_units=n_wind_units)

    n_sites = len(schema)
    use_grid = n_sites > grid_threshold

    if use_grid:
        print(f"\n  [GRID MODE] {n_sites} sites > {grid_threshold} threshold, "
              f"using grid clustering ({grid_resolution}° resolution)")
        grid = _grid_cluster_sites(schema.rename(columns={"cap_mw_old": "cap_mw"}),
                                   resolution_deg=grid_resolution)

        grid_cf_cache = {}
        for _, grow in grid.iterrows():
            try:
                cf = fetch_wind_site_cf(grow["grid_lat"], grow["grid_lon"],
                                        grow["fetch_name"])
                grid_cf_cache[(grow["grid_lat"], grow["grid_lon"])] = cf
            except Exception as e:
                print(f"    [ERR] Grid cell {grow['fetch_name']} failed: {e}")

        schema_with_grid = schema.copy()
        schema_with_grid["grid_lat"] = (
            (schema_with_grid["lat"] / grid_resolution).round() * grid_resolution
        ).round(6)
        schema_with_grid["grid_lon"] = (
            (schema_with_grid["lon"] / grid_resolution).round() * grid_resolution
        ).round(6)

        site_curves = []
        for _, row in schema_with_grid.iterrows():
            key = (row["grid_lat"], row["grid_lon"])
            if key in grid_cf_cache:
                site_curves.append({
                    "unit": row["new_unit_name"],
                    "cap": float(row["cap_mw_old"]),
                    "cf": grid_cf_cache[key],
                    "farm_name": row.get("farm_name", "site"),
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "county_zone": row.get("county_zone", ""),
                    "nuts_id": row.get("nuts_id", ""),
                    "macro_zone": row.get("macro_zone", ""),
                })
    else:
        site_curves = []
        for _, row in schema.iterrows():
            site_name = f"{row['new_unit_name']}_{row.get('farm_name', 'site')}"
            try:
                cf = fetch_wind_site_cf(row["lat"], row["lon"], site_name)
                site_curves.append({
                    "unit": row["new_unit_name"],
                    "cap": float(row["cap_mw_old"]),
                    "cf": cf,
                    "farm_name": row.get("farm_name", "site"),
                    "lat": float(row["lat"]),
                    "lon": float(row["lon"]),
                    "county_zone": row.get("county_zone", ""),
                    "nuts_id": row.get("nuts_id", ""),
                    "macro_zone": row.get("macro_zone", ""),
                })
            except Exception as e:
                print(f"    [ERR] Site {site_name} failed: {e}")

    if not site_curves:
        raise RuntimeError("No wind site data retrieved. Cannot build profiles.")

    print(f"\n  [OK] Retrieved CF for {len(site_curves)} site(s)")

    print("\n  [IRENA] Loading calibration targets...")
    irena_targets = read_irena_targets(country_code)

    total_cap_pre = sum(it["cap"] for it in site_curves)
    national_cf_before = (
        sum(it["cap"] * float(np.nanmean(it["cf"].values)) for it in site_curves) / total_cap_pre
        if total_cap_pre > 0 else 0.0
    )

    alpha = calibrate_wind_pre_aggregation(site_curves, irena_targets)

    national_cf_after = min(national_cf_before * alpha, 1.0)

    if irena_targets:
        _save_irena_calibration_metadata(
            country_code, alpha, irena_targets, national_cf_before, national_cf_after
        )

    for it in site_curves:
        it["cf"] = (it["cf"] * alpha).clip(0.0, 1.0)

    idx = None
    for it in site_curves:
        idx = it["cf"].index if idx is None else idx.union(it["cf"].index)

    total_cap = sum(it["cap"] for it in site_curves)
    weighted_sum = pd.Series(0.0, index=idx)
    weight_sum = pd.Series(0.0, index=idx)

    for it in site_curves:
        cap = float(it["cap"])
        s = it["cf"].reindex(idx)
        m = s.notna()
        weighted_sum.loc[m] += cap * s.loc[m].astype(float)
        weight_sum.loc[m] += cap

    n_all_missing = (weight_sum == 0).sum()
    if n_all_missing > 0:
        print(f"  [WARN] Wind: {n_all_missing} hours where ALL sites have missing data (set to CF=0)")

    national_cf = (weighted_sum / weight_sum).fillna(0.0).clip(0.0, 1.0)
    national_cf.name = "wind"

    print(f"\n  [WIND] National aggregated CF: mean={national_cf.mean():.4f}, "
          f"max={national_cf.max():.4f}, {len(national_cf)} hours")

    try:
        site_cf_dict = {}
        site_meta_rows = []
        for i, it in enumerate(site_curves):
            col_name = f"{it['unit']}_{it.get('farm_name', str(i))}"
            if col_name in site_cf_dict:
                col_name = f"{col_name}_{i}"
            site_cf_dict[col_name] = it["cf"].reindex(idx).fillna(0.0)
            site_meta_rows.append({
                "column": col_name,
                "unit": it["unit"],
                "cap_mw": it["cap"],
                "farm_name": it.get("farm_name", ""),
                "lat": it.get("lat", np.nan),
                "lon": it.get("lon", np.nan),
                "county_zone": it.get("county_zone", ""),
                "nuts_id": it.get("nuts_id", ""),
                "macro_zone": it.get("macro_zone", ""),
            })

        site_cf_df = pd.DataFrame(site_cf_dict, index=idx)
        site_meta_df = pd.DataFrame(site_meta_rows)

        sites_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_sites.parquet")
        meta_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_sites_meta.csv")
        unit_profiles_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_unit_profiles.parquet")
        unit_meta_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_unit_metadata.csv")
        nuts_profiles_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_nuts_profiles.parquet")
        nuts_meta_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_nuts_metadata.csv")
        macro_profiles_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_macro_profiles.parquet")
        macro_meta_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_macro_metadata.csv")
        transfer_nuts_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_transfer_nuts_to_units.csv")
        transfer_macro_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_transfer_macro_to_units.csv")

        if site_cf_df.index.tz is not None:
            site_cf_df.index = site_cf_df.index.tz_localize(None)

        site_cf_df.to_parquet(sites_file)
        site_meta_df.to_csv(meta_file, index=False)
        unit_cf_df, unit_meta_df = _aggregate_site_curves_by_key(site_curves, "unit")
        nuts_cf_df, nuts_meta_df = _aggregate_site_curves_by_key(site_curves, "nuts_id")
        macro_cf_df, macro_meta_df = _aggregate_site_curves_by_key(site_curves, "macro_zone")
        transfer_nuts_df = _build_transfer_matrix(site_meta_df, "nuts_id")
        transfer_macro_df = _build_transfer_matrix(site_meta_df, "macro_zone")

        _save_profile_frame(unit_cf_df, unit_profiles_file)
        _save_profile_frame(nuts_cf_df, nuts_profiles_file)
        _save_profile_frame(macro_cf_df, macro_profiles_file)
        unit_meta_df.to_csv(unit_meta_file, index=False)
        nuts_meta_df.to_csv(nuts_meta_file, index=False)
        macro_meta_df.to_csv(macro_meta_file, index=False)
        transfer_nuts_df.to_csv(transfer_nuts_file, index=False)
        transfer_macro_df.to_csv(transfer_macro_file, index=False)

        print(f"  [SITES] Per-site CF matrix saved: {sites_file} "
              f"({site_cf_df.shape[1]} sites, {site_cf_df.shape[0]} hours)")
        print(f"  [SITES] Site metadata saved: {meta_file}")
        print(f"  [UNITS] Wind unit profiles saved: {unit_profiles_file}")
        print(f"  [NUTS3] Wind NUTS3 profiles saved: {nuts_profiles_file}")
        print(f"  [MACRO] Wind macro profiles saved: {macro_profiles_file}")
        print(f"  [TRANSFER] NUTS3->unit matrix saved: {transfer_nuts_file}")
        print(f"  [TRANSFER] Macro->unit matrix saved: {transfer_macro_file}")
    except Exception as e:
        print(f"  [WARN] Could not save per-site CF matrix: {e}")

    return national_cf


# Solar and temperature aggregation

def _aggregate_channel_profiles(channel_name: str, prefix: str, value_col: str,
                                configs: dict, weight_key: str) -> tuple:
    """Aggregate one climate channel to both national and macro-zone level."""

    def load_zone(name):
        f = os.path.join(RAW_DATA_DIR, f"{prefix}_{name}.parquet")
        if not os.path.exists(f):
            return None
        return pd.read_parquet(f)

    zone_profiles = {}
    zone_weights = {}
    metadata_rows = []

    for name, config in configs.items():
        df = load_zone(name)
        if df is None or value_col not in df.columns:
            continue

        raw_available = df[value_col].notna()
        values = (
            df[value_col]
            .interpolate(method="linear", limit=3)
            .bfill(limit=1)
            .ffill(limit=1)
            .fillna(0.0)
            .astype(float)
        )
        weights = pd.Series(float(config[weight_key]), index=values.index).where(raw_available, 0.0)
        zone_profiles[name] = values
        zone_weights[name] = weights
        metadata_rows.append({
            "channel": channel_name,
            "zone_name": name,
            "macro_zone": config.get("macro_zone", ""),
            "weight_basis": weight_key,
            "weight_value": float(config[weight_key]),
            "description": config.get("description", ""),
            "lat": float(config["lat"]),
            "lon": float(config["lon"]),
        })

    if not zone_profiles:
        return None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(metadata_rows)

    weighted_values = [zone_profiles[name] * zone_weights[name] for name in zone_profiles]
    total_sum = pd.concat(weighted_values, axis=1).sum(axis=1)
    total_weight = pd.concat(list(zone_weights.values()), axis=1).sum(axis=1)
    national = (total_sum / total_weight.replace(0, pd.NA)).fillna(0.0).astype(float)

    if channel_name == "solar":
        national = national.clip(0.0, 1.0)

    zone_df = pd.DataFrame(zone_profiles)
    macro_profiles = {}

    for zone_name, values in zone_profiles.items():
        macro_zone = _safe_group_label(configs[zone_name].get("macro_zone", ""), zone_name)
        weighted = values * zone_weights[zone_name]
        if macro_zone not in macro_profiles:
            macro_profiles[macro_zone] = {"weighted": [], "weights": []}
        macro_profiles[macro_zone]["weighted"].append(weighted)
        macro_profiles[macro_zone]["weights"].append(zone_weights[zone_name])

    macro_df = {}
    for macro_zone, bundle in macro_profiles.items():
        macro_sum = pd.concat(bundle["weighted"], axis=1).sum(axis=1)
        macro_weight = pd.concat(bundle["weights"], axis=1).sum(axis=1)
        series = (macro_sum / macro_weight.replace(0, pd.NA)).fillna(0.0).astype(float)
        if channel_name == "solar":
            series = series.clip(0.0, 1.0)
        macro_df[macro_zone] = series

    return national, zone_df, pd.DataFrame(macro_df), pd.DataFrame(metadata_rows)


def aggregate_solar_temp_detailed(solar_zones: dict, temp_points: dict,
                                  country_code: str = "HR") -> tuple:
    """Calculate national, zone-level, and macro-level solar/temp outputs."""
    _print_banner("AGGREGATING SOLAR + TEMPERATURE")

    print("  Aggregating Solar...")
    solar_nat, solar_zone_df, solar_macro_df, solar_meta = _aggregate_channel_profiles(
        channel_name="solar",
        prefix="solar",
        value_col="solar",
        configs=solar_zones,
        weight_key="weight_MW",
    )

    print("  Aggregating Temperature...")
    temp_nat, temp_zone_df, temp_macro_df, temp_meta = _aggregate_channel_profiles(
        channel_name="temperature",
        prefix="temp",
        value_col="temperature",
        configs=temp_points,
        weight_key="weight_pop",
    )

    if solar_nat is None or temp_nat is None:
        print("  [ERROR] Missing solar/temp data. Run fetch first.")
        return None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    solar_targets = read_irena_solar_targets(country_code)
    solar_nat, solar_zone_df, solar_macro_df, solar_calibration = (
        calibrate_solar_profiles_to_irena(
            solar_nat,
            solar_zone_df,
            solar_macro_df,
            solar_targets,
        )
    )
    _save_solar_calibration_metadata(country_code, solar_calibration, solar_targets)

    national_df = pd.DataFrame({
        "solar": solar_nat.astype(float),
        "temperature": temp_nat.astype(float),
    })

    zone_df = pd.DataFrame(index=national_df.index)
    for col in solar_zone_df.columns:
        zone_df[f"solar__{col}"] = solar_zone_df[col].reindex(zone_df.index)
    for col in temp_zone_df.columns:
        zone_df[f"temperature__{col}"] = temp_zone_df[col].reindex(zone_df.index)

    macro_df = pd.DataFrame(index=national_df.index)
    for col in solar_macro_df.columns:
        macro_df[f"solar__{col}"] = solar_macro_df[col].reindex(macro_df.index)
    for col in temp_macro_df.columns:
        macro_df[f"temperature__{col}"] = temp_macro_df[col].reindex(macro_df.index)

    meta_df = pd.concat([solar_meta, temp_meta], ignore_index=True)
    return national_df, zone_df, macro_df, meta_df


def aggregate_solar_temp(solar_zones: dict, temp_points: dict,
                         country_code: str = "HR") -> pd.DataFrame:
    """Backward-compatible wrapper returning only national solar/temp series."""
    national_df, _, _, _ = aggregate_solar_temp_detailed(
        solar_zones,
        temp_points,
        country_code=country_code,
    )
    return national_df


# 2050 corrections

def apply_2050_corrections(df, temp_offset=1.5, wind_factor=0.92, solar_factor=1.03):
    """Apply the default 2050 climate adjustments used by the model."""
    df_c = df.copy()
    df_c["temperature"] += temp_offset
    df_c["wind"] = (df_c["wind"] * wind_factor).clip(0, 1)
    df_c["solar"] = (df_c["solar"] * solar_factor).clip(0, 1)
    return df_c


# Visualization

def plot_aggregated(df, country_code: str, country_name: str, df_2050=None):
    """Simple plot of historical vs corrected aggregation."""
    plt.figure(figsize=(15, 10))

    last_date = df.index.max()
    start_date = last_date - pd.Timedelta(days=730)
    plot_df = df.loc[start_date:]

    ax1 = plt.subplot(3, 1, 1)
    plt.plot(plot_df.index, plot_df["wind"], label="Wind (Calibrated)", color="blue", alpha=0.7)
    plt.title(f"Weighted Aggregated Capacity Factors ({country_name})")
    plt.ylabel("Capacity Factor")
    plt.legend()
    plt.grid(True, alpha=0.3)

    plt.subplot(3, 1, 2, sharex=ax1)
    plt.plot(plot_df.index, plot_df["solar"], label="Solar (Ninja)", color="orange", alpha=0.7)
    plt.ylabel("Capacity Factor")
    plt.legend()
    plt.grid(True, alpha=0.3)

    ax3 = plt.subplot(3, 1, 3, sharex=ax1)
    plt.plot(plot_df.index, plot_df["temperature"], label="Temp (Historical)", color="red", alpha=0.7)
    if df_2050 is not None:
        p2050 = df_2050.loc[start_date:]
        plt.plot(p2050.index, p2050["temperature"], label="Temp (2050 shift)",
                 color="darkred", ls="--", alpha=0.5)
    plt.ylabel("Temperature (°C)")
    plt.legend()
    plt.grid(True, alpha=0.3)

    plt.tight_layout()
    plot_path = os.path.join(BASE_DATA_DIR, f"{country_code}_aggregated_climatology.png")
    plt.savefig(plot_path)
    print(f"  [SAVED] Visualization: {plot_path}")


# Main entry point

def main(country_code="HR", use_database=True, n_wind_units=4):
    """
    Main orchestrator for climate data acquisition.

    Parameters
    ----------
    country_code : str
    use_database : bool, use data_per_country/ wind farm database (default True).
        Set to False only to use legacy manual wind_schema_XX.csv files.
    n_wind_units : int, number of H2RES wind units for auto-grouping
    """
    country_code = str(country_code).upper()
    has_registry = country_code in COUNTRY_REGISTRY

    if has_registry:
        current_config = COUNTRY_REGISTRY[country_code]
        solar_zones = {name: dict(cfg) for name, cfg in current_config["solar_zones"].items()}
        temp_points = {name: dict(cfg) for name, cfg in current_config["temp_points"].items()}
        country_full_name = current_config["full_name"]
        asset_reference_year = get_asset_reference_year(country_code)
        target_solar_mw = read_irena_installed_capacity(
            country_code,
            "solar photovoltaic",
            year=asset_reference_year,
        )
        if target_solar_mw:
            solar_zones = _scale_weighted_configs_to_target(
                solar_zones,
                weight_key="weight_MW",
                target_total=target_solar_mw,
            )
        else:
            target_solar_mw = sum(float(cfg.get("weight_MW", 0.0) or 0.0) for cfg in solar_zones.values())
    else:
        solar_zones = {}
        temp_points = {}
        country_full_name = COUNTRY_FILE_MAP.get(country_code, country_code)
        asset_reference_year = get_asset_reference_year(country_code)
        target_solar_mw = None

    _print_banner("MULTI-SOURCE CLIMATE DATA PIPELINE")
    print(f"  Country: {country_full_name} ({country_code})")
    print(f"  Asset reference year: {asset_reference_year}")
    print(f"  Wind:  Open-Meteo + Fleet Power Curve + IRENA calibration")
    if use_database:
        print(f"  Wind source: data_per_country/ database (auto-schema)")
    print(f"  Solar: Renewables.ninja PV model + IRENA calibration")
    print(f"  Temp:  Renewables.ninja")

    print("\n[Phase 1/3] Building Wind Profiles (Open-Meteo)...")
    try:
        wind_cf = build_wind_profiles(country_code, use_database=use_database,
                                      n_wind_units=n_wind_units)
    except FileNotFoundError as e:
        print(f"  [ERROR] {e}")
        print("  Cannot proceed without wind schema. Exiting.")
        return
    except Exception as e:
        print(f"  [ERROR] Wind pipeline failed: {e}")
        return

    if not has_registry:
        print(f"\n  [SKIP] No solar/temp config in COUNTRY_REGISTRY for '{country_code}'.")
        print("  Only wind profiles were generated. Add solar_zones and temp_points")
        print("  to COUNTRY_REGISTRY to enable full pipeline.")
        return

    if not HAS_NINJA_TOKEN:
        print("\n[WARNING] No Ninja API_TOKEN set. Skipping solar/temp fetch.")
    else:
        print("\n[Phase 2/3] Fetching Solar Zones (Ninja)...")
        for name, config in solar_zones.items():
            fetch_zone_history(f"solar_{name}", config, endpoint="pv")

        print("\n            Fetching Temp Points (Ninja)...")
        for name, config in temp_points.items():
            fetch_zone_history(f"temp_{name}", config, endpoint="pv")

    print("\n[Phase 3/3] Combining Wind + Solar + Temperature...")

    solar_temp_files = []
    for prefix, zones in [("solar", solar_zones), ("temp", temp_points)]:
        for name in zones:
            solar_temp_files.append(os.path.join(RAW_DATA_DIR, f"{prefix}_{name}.parquet"))

    missing = [f for f in solar_temp_files if not os.path.exists(f)]
    if missing:
        print(f"  [!] Missing {len(missing)} solar/temp files. Cannot aggregate.")
        print("  Run again with valid Ninja API token to fetch solar/temp data.")
        return

    df_solar_temp, climate_zone_df, climate_macro_df, climate_meta_df = (
        aggregate_solar_temp_detailed(solar_zones, temp_points,
                                      country_code=country_code)
    )
    if df_solar_temp is None:
        return

    climate_zone_file = os.path.join(BASE_DATA_DIR, f"{country_code}_climate_zone_profiles.parquet")
    climate_macro_file = os.path.join(BASE_DATA_DIR, f"{country_code}_macro_climate_profiles.parquet")
    climate_meta_file = os.path.join(BASE_DATA_DIR, f"{country_code}_macro_climate_metadata.csv")
    _save_profile_frame(climate_zone_df, climate_zone_file)

    if wind_cf.index.tz is not None and df_solar_temp.index.tz is None:
        df_solar_temp.index = df_solar_temp.index.tz_localize('UTC')
    elif wind_cf.index.tz is None and df_solar_temp.index.tz is not None:
        wind_cf.index = wind_cf.index.tz_localize('UTC')

    common_start = max(wind_cf.index.min(), df_solar_temp.index.min())
    common_end = min(wind_cf.index.max(), df_solar_temp.index.max())

    wind_trimmed = wind_cf.loc[common_start:common_end].sort_index()
    st_trimmed = df_solar_temp.loc[common_start:common_end].sort_index()

    left_df = pd.DataFrame({"_dummy": 0}, index=st_trimmed.index)
    right_df = wind_trimmed.to_frame(name="wind")
    wind_reindexed = pd.merge_asof(
        left_df, right_df,
        left_index=True, right_index=True,
        direction="nearest", tolerance=pd.Timedelta("2h")
    )["wind"]

    df_final = st_trimmed.copy()
    df_final["wind"] = wind_reindexed.fillna(0.0).clip(0, 1)

    wind_macro_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_macro_profiles.parquet")
    wind_macro_meta_file = os.path.join(BASE_DATA_DIR, f"{country_code}_wind_macro_metadata.csv")
    macro_output_df = climate_macro_df.copy()

    if os.path.exists(wind_macro_file):
        wind_macro_df = pd.read_parquet(wind_macro_file)
        if wind_macro_df.index.tz is not None and macro_output_df.index.tz is None:
            macro_output_df.index = macro_output_df.index.tz_localize("UTC")
        elif wind_macro_df.index.tz is None and macro_output_df.index.tz is not None:
            wind_macro_df.index = wind_macro_df.index.tz_localize("UTC")

        common_macro_start = max(wind_macro_df.index.min(), macro_output_df.index.min())
        common_macro_end = min(wind_macro_df.index.max(), macro_output_df.index.max())
        macro_output_df = macro_output_df.loc[common_macro_start:common_macro_end].sort_index()
        wind_macro_df = wind_macro_df.loc[common_macro_start:common_macro_end].sort_index()

        for col in wind_macro_df.columns:
            macro_output_df[f"wind__{col}"] = (
                wind_macro_df[col]
                .reindex(macro_output_df.index, method="nearest", tolerance=pd.Timedelta("2h"))
                .fillna(0.0)
                .clip(0.0, 1.0)
            )

    _save_profile_frame(macro_output_df, climate_macro_file)

    if os.path.exists(wind_macro_meta_file):
        wind_meta_df = pd.read_csv(wind_macro_meta_file)
        wind_meta_norm = pd.DataFrame({
            "channel": "wind",
            "zone_name": wind_meta_df.get("group_value", pd.Series(dtype=str)),
            "macro_zone": wind_meta_df.get("group_value", pd.Series(dtype=str)),
            "weight_basis": "total_cap_mw",
            "weight_value": wind_meta_df.get("total_cap_mw", pd.Series(dtype=float)),
            "description": "Capacity-weighted wind macro aggregation",
            "lat": np.nan,
            "lon": np.nan,
        })
        climate_meta_df = pd.concat([climate_meta_df, wind_meta_norm], ignore_index=True)

    climate_meta_df.to_csv(climate_meta_file, index=False)
    print(f"  [ZONES] Climate zone profiles saved: {climate_zone_file}")
    print(f"  [MACRO] Climate macro profiles saved: {climate_macro_file}")
    print(f"  [MACRO] Climate macro metadata saved: {climate_meta_file}")

    if has_registry and country_code == "HR":
        macro_zone_order = list(current_config.get("macro_zones", {}).keys())
        solar_macro_cols = [f"solar__{zone}" for zone in macro_zone_order if f"solar__{zone}" in macro_output_df.columns]
        solar_macro_profiles = pd.DataFrame(index=macro_output_df.index)
        for col in solar_macro_cols:
            solar_macro_profiles[col.replace("solar__", "")] = macro_output_df[col].astype(float)
        rebuild_country_solar_asset_layer(
            country_code=country_code,
            solar_macro_profiles=solar_macro_profiles,
            macro_zone_order=[col.replace("solar__", "") for col in solar_macro_cols],
            asset_reference_year=asset_reference_year,
            target_solar_mw=target_solar_mw,
        )

    print(f"\n  Combined dataset: {len(df_final)} hours "
          f"({df_final.index.min()} to {df_final.index.max()})")
    print(f"  Wind  CF mean: {df_final['wind'].mean():.4f}")
    print(f"  Solar CF mean: {df_final['solar'].mean():.4f}")
    print(f"  Temp    mean:  {df_final['temperature'].mean():.1f}°C")

    data_file = os.path.join(BASE_DATA_DIR, f"{country_code}_data.parquet")
    df_save = df_final.reset_index()
    df_save = df_save.rename(columns={df_save.columns[0]: "time"})
    if df_save["time"].dt.tz is not None:
        df_save["time"] = df_save["time"].dt.tz_localize(None)
    df_save.to_parquet(data_file, index=False)
    print(f"  [OK] Historical data saved to: {data_file}")

    df_2050 = apply_2050_corrections(df_final)
    c2050_file = os.path.join(BASE_DATA_DIR, f"{country_code}_data_2050_corrected.parquet")
    df_save_2050 = df_2050.reset_index()
    df_save_2050 = df_save_2050.rename(columns={df_save_2050.columns[0]: "time"})
    if df_save_2050["time"].dt.tz is not None:
        df_save_2050["time"] = df_save_2050["time"].dt.tz_localize(None)
    df_save_2050.to_parquet(c2050_file, index=False)
    print(f"  [OK] 2050 corrected data saved to: {c2050_file}")

    plot_aggregated(df_final, country_code, country_full_name, df_2050)

    _print_banner("PROCESSING COMPLETE")


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Build historical wind, solar, and temperature inputs.\n"
            "By default the wind layer is derived from the data_per_country database."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--country", type=str, default="HR",
                        help="ISO2 country code (e.g. HR, DE, BA)")
    parser.add_argument("--use-manual-schema", action="store_true",
                        help="Use legacy manual wind_schema_XX.csv instead of "
                             "the data_per_country database")
    parser.add_argument("--n-wind-units", type=int, default=4,
                        help="Number of H2RES wind units for auto-grouping (default: 4)")
    return parser.parse_known_args()[0]

if __name__ == "__main__":
    args = parse_args()

    main(country_code=args.country,
         use_database=not args.use_manual_schema,
         n_wind_units=args.n_wind_units)
