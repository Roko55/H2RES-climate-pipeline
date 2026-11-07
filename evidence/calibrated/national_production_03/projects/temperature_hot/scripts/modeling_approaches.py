"""
Created on Mon Sep 22 15:58:10 2025
"""
import csv
import os
import gurobipy as gp
import numpy as np

def _prefer_existing_path(*paths):
    """Return the first existing path, otherwise keep the primary path."""
    for path in paths:
        if os.path.exists(path):
            return path
    return paths[0]

def _env_flag(name, default='0'):
    value = os.environ.get(name, default)
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off', ''}

def _parse_profile_overrides(raw_value):
    """Parse channel override rules used for controlled climate experiments."""
    overrides = {}
    raw_value = str(raw_value or '').strip()
    if not raw_value:
        return overrides
    for part in raw_value.replace('|', ';').split(';'):
        if not part.strip() or '=' not in part:
            continue
        channel, expression = part.split('=', 1)
        channel = channel.strip().lower()
        expression = expression.strip()
        if channel in {'solar', 'pv'}:
            channel = 'solar'
        elif channel in {'wind', 'wton'}:
            channel = 'wind'
        elif channel in {'temp', 'temperature'}:
            channel = 'temperature'
        else:
            continue
        if expression:
            overrides[channel] = expression
    return overrides

def _split_function_args(expression, function_name):
    prefix = f'{function_name}('
    if not expression.lower().startswith(prefix) or not expression.endswith(')'):
        return None
    inner = expression[len(prefix):-1]
    return [item.strip() for item in inner.split(',') if item.strip()]

def _apply_profile_overrides(base_profile, scenario_records, overrides):
    """Return a controlled profile with selected channels replaced or transformed."""
    if not overrides:
        return (base_profile, '')
    channel_index = {'solar': 0, 'wind': 1, 'temperature': 2}
    profile = base_profile.copy()
    applied = []
    for channel, expression in overrides.items():
        idx = channel_index[channel]
        max_args = _split_function_args(expression, 'max')
        scale_args = _split_function_args(expression, 'scale_to_mean')
        if max_args:
            if len(max_args) < 2:
                raise ValueError(f"Override '{channel}={expression}' needs at least two scenarios.")
            values = []
            for scenario_name in max_args:
                if scenario_name not in scenario_records:
                    raise KeyError(f"Override scenario '{scenario_name}' not available.")
                values.append(scenario_records[scenario_name]['profile'][:, idx])
            profile[:, idx] = np.maximum.reduce(values).clip(0.0, 1.0)
            applied.append(f"{channel}=max({','.join(max_args)})")
            continue
        if scale_args:
            if len(scale_args) != 1:
                raise ValueError(f"Override '{channel}={expression}' needs exactly one target scenario.")
            target_name = scale_args[0]
            if target_name not in scenario_records:
                raise KeyError(f"Override scenario '{target_name}' not available.")
            target_mean = float(scenario_records[target_name]['profile'][:, idx].mean())
            base_mean = float(profile[:, idx].mean())
            ratio = target_mean / base_mean if base_mean > 0 else 1.0
            profile[:, idx] = (profile[:, idx] * ratio).clip(0.0, 1.0)
            applied.append(f'{channel}=scale_to_mean({target_name})')
            continue
        source_name = expression
        if source_name not in scenario_records:
            raise KeyError(f"Override scenario '{source_name}' not available.")
        profile[:, idx] = scenario_records[source_name]['profile'][:, idx]
        applied.append(f'{channel}={source_name}')
    return (profile, '; '.join(applied))

def _gurobi_status_label(status_code):
    labels = {2: 'optimal', 3: 'infeasible', 4: 'infeasible_or_unbounded', 5: 'unbounded', 12: 'numeric', 13: 'suboptimal'}
    return labels.get(int(status_code), f'status_{int(status_code)}')

def _mean_mapping_value(mapping):
    if not mapping:
        return None
    values = [float(v) for v in mapping.values()]
    if not values:
        return None
    return sum(values) / len(values)

def _fs_path(path):
    abs_path = os.path.abspath(path)
    if os.name == 'nt' and (not abs_path.startswith('\\\\?\\')):
        return '\\\\?\\' + abs_path
    return abs_path

def _load_fixed_capacity_lookup(path):
    if not path:
        return {}
    if not os.path.exists(path):
        print(f'[CAPACITY_TRANSFER] Fixed-capacity input not found: {path}')
        return {}
    lookup = {}
    with open(path, 'r', newline='', encoding='utf-8') as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            variable = str(row.get('variable', '')).strip()
            unit = str(row.get('unit', '')).strip()
            unit_2 = str(row.get('unit_2', '') or '').strip()
            try:
                year = int(float(row.get('year', '')))
                value = float(row.get('value', 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if variable and unit:
                lookup[variable, unit, unit_2, year] = value
    print(f'[CAPACITY_TRANSFER] Loaded {len(lookup)} fixed capacity values from {path}')
    return lookup

def _write_capacity_outputs(path, rows):
    os.makedirs(_fs_path(os.path.dirname(path)), exist_ok=True)
    fieldnames = ['variable', 'unit', 'unit_2', 'year', 'value']
    with open(_fs_path(path), 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f'[CAPACITY_TRANSFER] Wrote capacity expansion outputs to {path}')

def _write_trade_outputs(path, rows):
    os.makedirs(_fs_path(os.path.dirname(path)), exist_ok=True)
    fieldnames = ['period', 'year', 'optimized_import_mwh', 'import_capacity_mw', 'input_total_imports_mwh', 'input_total_exports_mwh', 'input_export_capacity_mw']
    with open(_fs_path(path), 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f'[TRADE] Wrote trade flow outputs to {path}')

def _write_objective_breakdown_outputs(path, rows):
    os.makedirs(_fs_path(os.path.dirname(path)), exist_ok=True)
    fieldnames = ['year', 'component', 'component_group', 'value', 'active_in_objective', 'objective_total', 'active_component_sum', 'gap_to_objective']
    with open(_fs_path(path), 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f'[OBJECTIVE] Wrote objective breakdown outputs to {path}')

def _write_dispatch_summary_outputs(path, rows):
    os.makedirs(_fs_path(os.path.dirname(path)), exist_ok=True)
    fieldnames = ['year', 'category', 'subcategory', 'value', 'unit']
    with open(_fs_path(path), 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f'[DISPATCH] Wrote dispatch summary outputs to {path}')

def _input_trade_lookup(table, column):
    lookup = {}
    if table is None:
        return lookup
    try:
        for _, row in table.iterrows():
            period = int(float(row.get('period', 0)))
            if period not in lookup:
                lookup[period] = float(row.get(column, 0.0) or 0.0)
    except Exception:
        return {}
    return lookup

def _expr_numeric_value(expr):
    if expr is None:
        return None
    try:
        return float(expr.getValue())
    except AttributeError:
        try:
            return float(expr)
        except (TypeError, ValueError):
            return None
    except Exception:
        return None

def _append_objective_breakdown_rows(rows, year, objective_total, ceep_is_active):
    component_specs = [('per_mw', 'electricity_generation_cost', True), ('per_mw_FC', 'fuel_cell_generation_cost', True), ('import_cost', 'optimized_import_cost', True), ('ramp_up_cost_var', 'ramping_cost', True), ('ramp_down_cost_var', 'ramping_cost', True), ('inv_cost', 'generation_investment_cost', True), ('inv_cost_fossil', 'generation_investment_cost', True), ('inv_cost_FuelCell', 'hydrogen_and_fuel_cell_cost', True), ('heat_pump_cap_cost', 'heat_sector_investment_cost', True), ('ResToHeat_cost', 'heat_sector_operation_cost', True), ('boiler_gen_cost', 'heat_sector_operation_cost', True), ('inv_cost_boiler', 'heat_sector_investment_cost', True), ('industry_fuel_cost', 'industry_fuel_cost', True), ('CO2_emissions_cost', 'co2_cost', True), ('h2_storage_cost', 'hydrogen_and_fuel_cell_cost', True), ('h2_CAPEX_cost', 'hydrogen_and_fuel_cell_cost', True), ('h2_OPEX_cost', 'hydrogen_and_fuel_cell_cost', True), ('evToPowert_cost', 'storage_and_flexibility_cost', True), ('statToPowert_cost', 'storage_and_flexibility_cost', True), ('statCapInvCost', 'storage_and_flexibility_cost', True), ('spill_cost', 'spill_and_ceep_cost', True), ('ceep_cost', 'spill_and_ceep_cost', bool(ceep_is_active)), ('no_load_cost', 'no_load_cost', True), ('no_load_cost_dh', 'no_load_cost', True), ('extra_investment_cost', 'extra_investment_cost', True), ('CO2_cost_industry', 'diagnostic_not_in_objective', False)]
    raw_rows = []
    active_component_sum = 0.0
    for component, group, active in component_specs:
        value = _expr_numeric_value(globals().get(component))
        if value is None:
            continue
        if active:
            active_component_sum += value
        raw_rows.append({'year': int(year), 'component': component, 'component_group': group, 'value': value, 'active_in_objective': int(active), 'objective_total': objective_total})
    gap = None
    if objective_total is not None:
        gap = active_component_sum - float(objective_total)
    for row in raw_rows:
        row['active_component_sum'] = active_component_sum
        row['gap_to_objective'] = gap
        rows.append(row)
    rows.append({'year': int(year), 'component': 'objective_total', 'component_group': 'objective_total', 'value': objective_total, 'active_in_objective': 0, 'objective_total': objective_total, 'active_component_sum': active_component_sum, 'gap_to_objective': gap})

def _generation_category(unit, fuel, technology):
    fuel_norm = str(fuel or '').strip().lower()
    tech_norm = str(technology or '').strip().lower()
    unit_norm = str(unit or '').strip().lower()
    if fuel_norm == 'wind' or 'wind' in unit_norm:
        return 'wind'
    if fuel_norm == 'solar' or 'solar' in unit_norm or tech_norm == 'phot':
        return 'solar'
    if fuel_norm == 'hydro' or tech_norm in {'hror', 'hdam', 'hphs'}:
        return 'hydro'
    if fuel_norm in {'coal', 'gas', 'diesel', 'oil'}:
        return 'fossil'
    if fuel_norm == 'biomass':
        return 'biomass'
    if fuel_norm == 'nuclear':
        return 'nuclear'
    return 'other'

def _accumulate_summary(summary, year, category, subcategory, value, unit='MWh'):
    key = (int(year), str(category), str(subcategory), str(unit))
    summary[key] = summary.get(key, 0.0) + float(value or 0.0)

def _build_dispatch_summary_rows():
    summary = {}
    fuel_lookup = dict(globals().get('unit_fuel', []))
    tech_lookup = dict(globals().get('unit_tech', []))
    for (unit, _period, year), value in generation_results.items():
        _accumulate_summary(summary, year, 'electricity_generation', _generation_category(unit, fuel_lookup.get(unit), tech_lookup.get(unit)), value)
    for (_period, year), value in imports_results.items():
        _accumulate_summary(summary, year, 'trade', 'optimized_import', value)
    for (_period, year), value in ceep_results.items():
        _accumulate_summary(summary, year, 'system_balance', 'ceep', value)
    for (_period, year), value in noload_elec_results.items():
        _accumulate_summary(summary, year, 'system_balance', 'no_load_electricity', value)
    for (_period, year), value in noload_heat_ind_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'no_load_industrial_heat', value)
    for (_unit, _period, year), value in noload_heat_dh_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'no_load_district_heat', value)
    for (_unit, _period, year), value in boiler_ind_generation_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'industrial_boiler_generation', value)
    for (_unit, _chp, _period, year), value in boiler_dh_generation_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'district_boiler_generation', value)
    for (_unit, _period, year), value in heat_pump_ind_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'industrial_heat_pump_output', value)
    for (_unit, _chp, _period, year), value in heat_pump_dh_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'district_heat_pump_output', value)
    for (_period, year), value in ResToHeat_results.items():
        _accumulate_summary(summary, year, 'heat_sector', 'renewables_to_heat', value)
    for (_period, year), value in ev_sto_in_results.items():
        _accumulate_summary(summary, year, 'storage_and_flexibility', 'ev_charge', value)
    for (_period, year), value in ev_sto_out_results.items():
        _accumulate_summary(summary, year, 'storage_and_flexibility', 'ev_discharge', value)
    for (_unit, _period, year), value in stat_sto_in_results.items():
        _accumulate_summary(summary, year, 'storage_and_flexibility', 'stationary_storage_charge', value)
    for (_unit, _period, year), value in stat_sto_out_results.items():
        _accumulate_summary(summary, year, 'storage_and_flexibility', 'stationary_storage_discharge', value)
    for (_period, year), value in power_to_h2_results.items():
        _accumulate_summary(summary, year, 'hydrogen', 'power_to_hydrogen', value)
    for (_unit, _period, year), value in generation_H2_results.items():
        _accumulate_summary(summary, year, 'hydrogen', 'electrolyzer_hydrogen_output', value)
    for (_unit, _period, year), value in generation_FC_results.items():
        _accumulate_summary(summary, year, 'hydrogen', 'fuel_cell_electricity_generation', value)
    for (_unit, _period, year), value in h2_sto_out_results.items():
        _accumulate_summary(summary, year, 'hydrogen', 'hydrogen_storage_discharge', value)
    for (_unit, _period, year), value in sto_out_flow_results.items():
        _accumulate_summary(summary, year, 'hydro', 'hydro_spill', value)
    rows = [{'year': year, 'category': category, 'subcategory': subcategory, 'value': value, 'unit': unit} for (year, category, subcategory, unit), value in sorted(summary.items())]
    return rows
scen = 'model_Test_3_Myopic'
rps_inv = False
carbonLimit = False
res_inv = True
ceep_limit = True
ceep_penalty = True
fossil_dispatch = True
hydro_storage = True
NoResToHeatInv = False
PrimaryReserve = False
SecondaryReserve = False
save_csv = False
Rolling_Horizon = True
climate_scenario = os.environ.get('H2RES_CLIMATE_SCENARIO', 'avg_yield__normal')
climate_run_label = os.environ.get('H2RES_RUN_LABEL', climate_scenario)
base_climate_scenario = os.environ.get('H2RES_BASE_CLIMATE_SCENARIO', climate_scenario)
profile_channel_overrides = _parse_profile_overrides(os.environ.get('H2RES_PROFILE_OVERRIDES', ''))
country_code = os.environ.get('H2RES_CLIMATE_COUNTRY', 'HR').upper()
asset_reference_year = 2023
wind_resolution_mode = os.environ.get('H2RES_WIND_RESOLUTION_MODE', 'auto').lower()
max_start_year_raw = os.environ.get('H2RES_MAX_START_YEAR', '').strip()
try:
    max_start_year = int(max_start_year_raw) if max_start_year_raw else None
except ValueError:
    print(f"[WARNING] Ignoring invalid H2RES_MAX_START_YEAR='{max_start_year_raw}'.")
    max_start_year = None
write_solver_summary = _env_flag('H2RES_WRITE_SOLVER_SUMMARY', '1')
solver_summary_root = os.environ.get('H2RES_SOLVER_SUMMARY_DIR', './results/climate_scenario_gurobi')
capacity_export_dir = os.environ.get('H2RES_CAPACITY_EXPORT_DIR', '')
fixed_capacity_input_path = os.environ.get('H2RES_FIXED_CAPACITY_INPUT', '')
fixed_capacity_lookup = _load_fixed_capacity_lookup(fixed_capacity_input_path)
climate_base_year = 2020
climate_target_year = 2050
climate_solar_factor_2050 = 1.03
climate_wind_factor_2050 = 0.92
climate_temp_offset_2050 = 1.5
temperature_coupling = _env_flag('H2RES_TEMPERATURE_COUPLING', '1')
temperature_district_heat_units = ['TE-TOZagreb']
temperature_general_heat_units = ['general_demand']
temperature_cooling_units = ['TE-TOZagreb', 'general_cooling']
temperature_atw_cop_units = ['ATW_HP']
temperature_heating_base_c = 18.0
temperature_cooling_base_c = 22.0
temperature_demand_shape_blend = 1.0
temperature_scale_annual_totals = _env_flag('H2RES_TEMPERATURE_SCALE_ANNUAL_TOTALS', '0')
temperature_district_heat_elasticity = 0.9
temperature_general_heat_elasticity = 0.7
temperature_cooling_elasticity = 0.95
temperature_hp_cop_sensitivity = 0.85
periods = list(range(1, 8761))
years = list([2020, 2025, 2030, 2035, 2040, 2045, 2050])
window = 2
rps = list([0.5, 0.6, 0.6, 0.7, 0.8, 0.9, 0.9])
CO2_limit = list([8261526 * 1, 8261526 * 0.8, 8261526 * 0.6, 8261526 * 0.4, 8261526 * 0.3, 8261526 * 0.2, 8261526 * 0.1])
carbon_price = list([20, 40, 60, 70, 80, 90, 100])
ceep_parameter = 0.1
ceep_value = 100
LostLoad_Cost = 1000000
NPV = list([1, 0.78, 0.61, 0.48, 0.38, 0.3, 0.23])
TechChangeSolar = list([1, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5])
TechChangeWind = list([1, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5])
ThermalDecomInd = list([1, 0.6, 0.3, 0, 0, 0, 0])
HeatPumpDecomInd = list([1, 0.6, 0.3, 0, 0, 0, 0])
ThermalDecomDH = list([1, 0.6, 0.3, 0, 0, 0, 0])
StaStoDecomInd = list([1, 0.6, 0.3, 0, 0, 0, 0])
Import_Price = 45
Imp_price_inc = 0.05
V2G_cost = 25
ev_Demand_year = list([1, 1, 1, 1, 1, 1, 1])
ev_sto_min = 0
ev_Grid_eff = 0.9
number_of_veh = 1670000
average_ch_rate = 7
average_bat = 50
charging_veh = number_of_veh * average_ch_rate / 1000
stor_veh = number_of_veh * average_bat / 1000
ev_Grid_P = list([0.075 * charging_veh, 0.2 * charging_veh, 0.4 * charging_veh, 0.4 * charging_veh, 0.4 * charging_veh, 0.4 * charging_veh, 0.4 * charging_veh])
ev_stor = list([0.122 * stor_veh, 0.143 * stor_veh, 0.163 * stor_veh, 0.184 * stor_veh, 0.204 * stor_veh, 0.225 * stor_veh, 0.25 * stor_veh])
genco_dat = f'./data/SC/genco_data_{country_code}_sdewes.csv'
demand_dat = './data/SC/demand_2020_2050_sdewes.csv'
fuel_price_dat = './data/SC/fuel_cost_2020_2050_sdewes.csv'
avl_factor_plant_dat = f'./data/SC/ncre_aval_factor_{country_code}_2020_2050_sdewes.csv'
inflows_dat = f'./data/SC/scaled_inflows_{country_code}_2020_2050_sdewes.csv'
import_export = f'./data/SC/import_export_{country_code}_2020_2050_sdewes.csv'
heat_demand_dat = _prefer_existing_path(f'./data/SC/heat_demand_{country_code}_2020_2050_sdewes_NONE.csv', f'./data/SC/heat_demand_{country_code}_2020_2050_sdewes.csv')
cooling_demand_dat = _prefer_existing_path(f'./data/SC/cooling_demand_{country_code}_2020_2050_sdewes_NONE.csv', f'./data/SC/cooling_demand_{country_code}_2020_2050_sdewes.csv')
ev_transpload_dat = _prefer_existing_path('./data/SC/ev_transp_load_NONE.csv', './data/SC/ev_transp_load.csv')
h2_demand_dat = _prefer_existing_path('./data/SC/demand_H2_2020_2050_sdewes_NONE.csv', './data/SC/demand_H2_2020_2050_sdewes.csv')
flex_tech_dat = './data/SC/flex_tech_HR_2020_2050_sdewes_limited.xlsx'
print('Loading and processing data...')
exec(open('scripts/read_process_data.py').read())
if Rolling_Horizon == False:
    print('Building and solve the model')
    exec(open('scripts/Build_model.py').read())
    print('Printing and ploting results')
    exec(open('scripts/export_plot.py').read())
    exec(open('scripts/combine_plot.py').read())
if Rolling_Horizon == True:
    ai_climate_available = False
    temperature_coupling_available = False
    try:
        from scripts.scenario_manager import get_representative_scenarios, decompose_to_h2res_units, CLIMATE_CHANGE
        CLIMATE_CHANGE['base_year'] = climate_base_year
        CLIMATE_CHANGE['target_year'] = climate_target_year
        CLIMATE_CHANGE['solar_factor_2050'] = 1.0
        CLIMATE_CHANGE['wind_factor_2050'] = 1.0
        CLIMATE_CHANGE['temp_offset_2050'] = 0.0
        ai_climate_available = True
    except Exception as e:
        print(f'\n[WARNING] AI Climate Component initialization failed: {e}')
        print('   (Ensure xgboost_climate_model.py has been trained and scenarios generated.)')
    try:
        from scripts.temperature_coupling import apply_temperature_coupling_to_runtime
        temperature_coupling_available = True
    except Exception as e:
        print(f'\n[WARNING] Temperature coupling initialization failed: {e}')
        print('   Heat/cooling demand and heat-pump COP will remain on CSV inputs.')
    if base_climate_scenario in {'all_diagonal', 'all_combined', 'all'}:
        raise ValueError("Multi-scenario sweep modes must be executed through 'scripts/climate_pipeline.py --run-gurobi'. Set H2RES_CLIMATE_SCENARIO to a concrete scenario such as 'avg_yield__normal'.")
    elif '__' in base_climate_scenario:
        scenario_runs = [base_climate_scenario]
    elif base_climate_scenario in {'low_yield', 'avg_yield', 'high_yield'}:
        print(f"[INFO] Legacy one-axis scenario '{base_climate_scenario}' requested. Using the neutral thermal state '__normal'.")
        scenario_runs = [f'{base_climate_scenario}__normal']
    else:
        print(f"[WARNING] Unknown base_climate_scenario='{base_climate_scenario}'. Defaulting to 'avg_yield__normal'.")
        scenario_runs = ['avg_yield__normal']
    if climate_run_label != scenario_runs[0] or profile_channel_overrides:
        print(f"[CONTROLLED_SCENARIO] run_label='{climate_run_label}', base='{scenario_runs[0]}', overrides={profile_channel_overrides or '{}'}")
    ai_scenario_cache = {}
    if ai_climate_available:
        print('\n[AI Climate] Pre-generating representative scenarios for all planning years...')
        try:
            for _yr in years:
                ai_scenario_cache[_yr] = get_representative_scenarios(_yr)
                print(f'  Cached scenarios for year {_yr}.')
        except Exception as e:
            print(f'[WARNING] Scenario pre-caching failed: {e}. Will fallback to per-year loading.')
            ai_scenario_cache = {}
    hor_yrs = years
    baseline_heat_demand = dict(heat_demand)
    baseline_cooling_demand = dict(cooling_demand)
    baseline_HP_COP_period = dict(HP_COP_period)
    years = []
    cap_inv_cumulative = {(u, y): 0.0 for u in units for y in hor_yrs}
    cap_inv_FC_cumulative = {(u, y): 0.0 for u in FC_units for y in hor_yrs}
    heat_pump_dh_cap_cumulative = {(u, v, y): 0.0 for u in HeatPump_units for v in chp_units for y in hor_yrs}
    heat_pump_ind_cap_cumulative = {(u, y): 0.0 for u in HeatPump_units for y in hor_yrs}
    trad_boiler_ind_cap_inv_cumulative = {(u, y): 0.0 for u in thermal_units for y in hor_yrs}
    trad_boiler_dh_cap_inv_cumulative = {(u, v, y): 0.0 for u in thermal_units for v in chp_units for y in hor_yrs}
    stat_sto_cap_inv_cumulative = {(u, y): 0.0 for u in Stat_storage_unit for y in hor_yrs}
    h2_sto_max_cap_cumulative = {(u, y): 0.0 for u in H2_storage_unit for y in hor_yrs}
    h2_electrolysis_max_cap_cumulative = {(u, y): 0.0 for u in Elec_h2_units for y in hor_yrs}
    values_cap_inv = {}
    values_cap_inv_FC = {}
    values_heat_pump_dh_cap = {}
    values_heat_pump_ind_cap = {}
    values_trad_boiler_ind_cap_inv = {}
    values_trad_boiler_dh_cap_inv = {}
    values_stat_sto_cap_inv = {}
    values_h2_sto_max_cap = {}
    values_h2_electrolysis_max_cap = {}
    generation_results = {}
    imports_results = {}
    ceep_results = {}
    cap_inv_results = {}
    noload_elec_results = {}
    noload_heat_ind_results = {}
    noload_heat_dh_results = {}
    CO2_total_results = {}
    CO2_total_heat_results = {}
    CO2_total_industry_results = {}
    cap_inv_FC_results = {}
    generation_FC_results = {}
    storage_level_results = {}
    sto_out_flow_results = {}
    heat_gen_results = {}
    Fuel_ind_results = {}
    H2tInd_results = {}
    heat_pump_dh_results = {}
    heat_pump_ind_results = {}
    heat_pump_dh_cap_results = {}
    heat_pump_ind_cap_results = {}
    boiler_dh_generation_results = {}
    boiler_ind_generation_results = {}
    trad_boiler_ind_cap_inv_results = {}
    trad_boiler_dh_cap_inv_results = {}
    ResToHeat_results = {}
    ev_sto_in_results = {}
    ev_sto_out_results = {}
    ev_sto_soc_results = {}
    stat_sto_in_results = {}
    stat_sto_out_results = {}
    stat_sto_cap_inv_results = {}
    power_to_h2_results = {}
    h2_sto_soc_results = {}
    h2_sto_out_results = {}
    h2_sto_max_cap_results = {}
    h2_electrolysis_max_cap_results = {}
    generation_H2_results = {}
    solver_subproblem_summary = []
    objective_breakdown_results = []
    for i in [hor_yrs.index(int(os.environ['FULL_MATCHED_YEAR']))] if int(os.environ['FULL_MATCHED_YEAR']) else range(int(os.environ['FULL_START_INDEX']), len(hor_yrs)):
        if max_start_year is not None and hor_yrs[i] > max_start_year:
            print(f'[CONTROLLED_SCENARIO] Stopping rolling horizon after start year {max_start_year}.')
            break
        years = hor_yrs[i:i + window]
        print(f'Building and solving the submodel for the year {hor_yrs[i]}')
        if ai_climate_available:
            try:
                active_scenario = scenario_runs[0]
                anchor_prob = None
                anchor_decomp_diag = None
                anchor_temp_diag = None
                anchor_profile_summary = None
                for injection_year in years:
                    if injection_year in ai_scenario_cache:
                        ai_data = ai_scenario_cache[injection_year]
                    else:
                        ai_data = get_representative_scenarios(injection_year)
                    scenario_profile = ai_data[active_scenario]['profile']
                    scenario_prob = ai_data[active_scenario]['probability']
                    override_label = ''
                    if profile_channel_overrides:
                        scenario_profile, override_label = _apply_profile_overrides(scenario_profile, ai_data, profile_channel_overrides)
                    unit_profiles, decomp_diag = decompose_to_h2res_units(scenario_profile, country_code=country_code, wind_resolution_mode=wind_resolution_mode, return_diagnostics=True)
                    n_updated = 0
                    for unit_name, profile_8760 in unit_profiles.items():
                        for p_idx in range(min(len(periods), 8760)):
                            p = periods[p_idx]
                            if (unit_name, p, injection_year) in aval_factor_plant:
                                aval_factor_plant[unit_name, p, injection_year] = float(profile_8760[p_idx])
                                n_updated += 1
                    temp_diag = None
                    if temperature_coupling and temperature_coupling_available:
                        temp_diag = apply_temperature_coupling_to_runtime(scenario_profile[:, 2], year=injection_year, country_code=country_code, periods=periods, heat_demand=heat_demand, cooling_demand=cooling_demand, hp_cop_period=HP_COP_period, baseline_heat_demand=baseline_heat_demand, baseline_cooling_demand=baseline_cooling_demand, baseline_hp_cop_period=baseline_HP_COP_period, district_heat_units=temperature_district_heat_units, general_heat_units=temperature_general_heat_units, cooling_units=temperature_cooling_units, atw_cop_units=temperature_atw_cop_units, heating_base_c=temperature_heating_base_c, cooling_base_c=temperature_cooling_base_c, demand_shape_blend=temperature_demand_shape_blend, scale_annual_totals=temperature_scale_annual_totals, district_heat_elasticity=temperature_district_heat_elasticity, general_heat_elasticity=temperature_general_heat_elasticity, cooling_elasticity=temperature_cooling_elasticity, cop_temperature_sensitivity=temperature_hp_cop_sensitivity)
                    if injection_year == years[0]:
                        anchor_prob = float(scenario_prob)
                        anchor_decomp_diag = dict(decomp_diag)
                        anchor_temp_diag = dict(temp_diag) if temp_diag is not None else None
                        anchor_profile_summary = {'aggregate_solar_cf_mean': float(scenario_profile[:, 0].mean()), 'aggregate_wind_cf_mean': float(scenario_profile[:, 1].mean()), 'temperature_mean_c': float(scenario_profile[:, 2].mean())}
                    wind_mode_label = decomp_diag.get('wind_resolution_selected', 'n/a')
                    wind_mass_error = decomp_diag.get('wind_mass_mean_abs_error', float('nan'))
                    print(f'  [{climate_run_label}] Applied climate profiles for year {injection_year}: {n_updated} values across {len(unit_profiles)} units (prob={scenario_prob:.1%}, wind_resolution={wind_mode_label}, wind_mass_err={wind_mass_error:.6f})')
                    if override_label:
                        print(f'  [CONTROLLED_SCENARIO] overrides: {override_label}')
                    if temp_diag is not None:
                        heat_units_updated = len(temp_diag.get('district_heat_units_updated', [])) + len(temp_diag.get('general_heat_units_updated', []))
                        print(f"  [TEMP] Year {injection_year}: mean={temp_diag['temp_mean_c']:.1f}C, range=[{temp_diag['temp_min_c']:.1f}, {temp_diag['temp_max_c']:.1f}]C, heat={heat_units_updated} units, cooling={len(temp_diag['cooling_units_updated'])} units, COP={len(temp_diag['cop_units_updated'])} units")
            except Exception as e:
                print(f'[WARNING] Climate profile injection error for year {hor_yrs[i]}: {e}')
        if i == int(os.environ['FULL_START_INDEX']):
            _full_restore(globals())
        _channel_prebuild(globals())
        exec(open('scripts/Build_model_RH.py').read())
        status_code = int(ucp.Status)
        objective_value = float(ucp.ObjVal) if status_code in {2, 13} else None
        if objective_value is not None:
            _append_objective_breakdown_rows(objective_breakdown_results, years[0], objective_value, ceep_penalty)
        if write_solver_summary:
            active_scenario_name = climate_run_label if ai_climate_available else 'climate_disabled'
            summary_row = {'scenario': active_scenario_name, 'base_climate_scenario': active_scenario if ai_climate_available else None, 'profile_overrides': '; '.join((f'{k}={v}' for k, v in profile_channel_overrides.items())) if ai_climate_available else None, 'planning_year': int(hor_yrs[i]), 'window_start_year': int(years[0]), 'window_end_year': int(years[-1]), 'status_code': status_code, 'status_label': _gurobi_status_label(status_code), 'objective': objective_value, 'climate_probability': anchor_prob if ai_climate_available else None, 'aggregate_solar_cf_mean': None, 'aggregate_wind_cf_mean': None, 'temperature_mean_c': None, 'solar_resolution': None, 'solar_mass_mean_abs_error': None, 'wind_resolution': None, 'wind_mass_mean_abs_error': None, 'district_heat_scale_mean': None, 'general_heat_scale_mean': None, 'cooling_scale_mean': None, 'cop_unit_count': None}
            if anchor_profile_summary is not None:
                summary_row.update(anchor_profile_summary)
            if anchor_decomp_diag is not None:
                summary_row.update({'solar_resolution': anchor_decomp_diag.get('solar_resolution_selected'), 'solar_mass_mean_abs_error': anchor_decomp_diag.get('solar_mass_mean_abs_error'), 'wind_resolution': anchor_decomp_diag.get('wind_resolution_selected'), 'wind_mass_mean_abs_error': anchor_decomp_diag.get('wind_mass_mean_abs_error')})
            if anchor_temp_diag is not None:
                summary_row.update({'district_heat_scale_mean': _mean_mapping_value(anchor_temp_diag.get('district_heat_scale_factors')), 'general_heat_scale_mean': _mean_mapping_value(anchor_temp_diag.get('general_heat_scale_factors')), 'cooling_scale_mean': _mean_mapping_value(anchor_temp_diag.get('cooling_scale_factors')), 'cop_unit_count': len(anchor_temp_diag.get('cop_units_updated', []))})
            solver_subproblem_summary.append(summary_row)
        print(f'Run for Submodel for the year {hor_yrs[i]} completed')
        for year in years:
            for unit in units:
                values_cap_inv[unit, year] = cap_inv[unit, year].X
            for unit in FC_units:
                values_cap_inv_FC[unit, year] = cap_inv_FC[unit, year].X
            for unit in HeatPump_units:
                values_heat_pump_ind_cap[unit, year] = heat_pump_ind_cap[unit, year].X
                for chp in chp_units:
                    values_heat_pump_dh_cap[unit, chp, year] = heat_pump_dh_cap[unit, chp, year].X
            for unit in thermal_units:
                values_trad_boiler_ind_cap_inv[unit, year] = trad_boiler_ind_cap_inv[unit, year].X
                for chp in chp_units:
                    values_trad_boiler_dh_cap_inv[unit, chp, year] = trad_boiler_dh_cap_inv[unit, chp, year].X
            for unit in Stat_storage_unit:
                values_stat_sto_cap_inv[unit, year] = stat_sto_cap_inv[unit, year].X
            for unit in H2_storage_unit:
                values_h2_sto_max_cap[unit, year] = h2_sto_max_cap[unit, year].X
            for unit in Elec_h2_units:
                values_h2_electrolysis_max_cap[unit, year] = h2_electrolysis_max_cap[unit, year].X
        for j in range(i + 1, min(i + 1 + window, len(hor_yrs))):
            for u in units:
                cap_inv_cumulative[u, hor_yrs[j]] = gp.quicksum((values_cap_inv[u, hor_yrs[ip]] * round((1 - decom_rate[u]) ** (max(decom_start_new[u] - 1, hor_yrs[j] - hor_yrs[ip]) - (decom_start_new[u] - 1)), 3) for ip in range(len(hor_yrs)) if ip < j))
            for u in FC_units:
                cap_inv_FC_cumulative[u, hor_yrs[j]] = cap_inv_FC_cumulative[u, hor_yrs[j - 1]] + values_cap_inv_FC[u, hor_yrs[j - 1]]
            for u in HeatPump_units:
                heat_pump_ind_cap_cumulative[u, hor_yrs[j]] = gp.quicksum((values_heat_pump_ind_cap[u, hor_yrs[ip]] * round((1 - HP_decom_rate[u]) ** (max(HP_decom_start_new[u] - 1, hor_yrs[j] - hor_yrs[ip]) - (HP_decom_start_new[u] - 1)), 3) for ip in range(len(hor_yrs)) if ip < j))
                for v in chp_units:
                    heat_pump_dh_cap_cumulative[u, v, hor_yrs[j]] = gp.quicksum((values_heat_pump_dh_cap[u, v, hor_yrs[ip]] * round((1 - HP_decom_rate[u]) ** (max(HP_decom_start_new[u] - 1, hor_yrs[j] - hor_yrs[ip]) - (HP_decom_start_new[u] - 1)), 3) for ip in range(len(hor_yrs)) if ip < j))
            for u in thermal_units:
                trad_boiler_ind_cap_inv_cumulative[u, hor_yrs[j]] = gp.quicksum((values_trad_boiler_ind_cap_inv[u, hor_yrs[ip]] * round((1 - Boiler_decom_rate[u]) ** (max(Boiler_decom_start_new[u] - 1, hor_yrs[j] - hor_yrs[ip]) - (Boiler_decom_start_new[u] - 1)), 3) for ip in range(len(hor_yrs)) if ip < j))
                for v in chp_units:
                    trad_boiler_dh_cap_inv_cumulative[u, v, hor_yrs[j]] = gp.quicksum((values_trad_boiler_dh_cap_inv[u, v, hor_yrs[ip]] * round((1 - Boiler_decom_rate[u]) ** (max(Boiler_decom_start_new[u] - 1, hor_yrs[j] - hor_yrs[ip]) - (Boiler_decom_start_new[u] - 1)), 3) for ip in range(len(hor_yrs)) if ip < j))
            for u in Stat_storage_unit:
                stat_sto_cap_inv_cumulative[u, hor_yrs[j]] = gp.quicksum((values_stat_sto_cap_inv[u, hor_yrs[ip]] * round((1 - Sta_sto_decom_rate[u]) ** (max(Sta_sto_decom_start_new[u] - 1, hor_yrs[j] - hor_yrs[ip]) - (Sta_sto_decom_start_new[u] - 1)), 3) for ip in range(len(hor_yrs)) if ip < j))
            for u in H2_storage_unit:
                h2_sto_max_cap_cumulative[u, hor_yrs[j]] = h2_sto_max_cap_cumulative[u, hor_yrs[j - 1]] + values_h2_sto_max_cap[u, hor_yrs[j - 1]]
            for u in Elec_h2_units:
                h2_electrolysis_max_cap_cumulative[u, hor_yrs[j]] = h2_electrolysis_max_cap_cumulative[u, hor_yrs[j - 1]] + values_h2_electrolysis_max_cap[u, hor_yrs[j - 1]]
        for genco in units:
            cap_inv_results[genco, years[0]] = cap_inv[genco, years[0]].x
            for period in periods:
                generation_results[genco, period, years[0]] = generation[genco, period, years[0]].x
        for period in periods:
            imports_results[period, years[0]] = imports[period, years[0]].x
            ceep_results[period, years[0]] = ceep[period, years[0]].x
            noload_elec_results[period, years[0]] = noload_elec[period, years[0]].x
            noload_heat_ind_results[period, years[0]] = noload_heat_ind[period, years[0]].x
            ResToHeat_results[period, years[0]] = ResToHeat[period, years[0]].x
            ev_sto_in_results[period, years[0]] = ev_sto_in[period, years[0]].x
            ev_sto_out_results[period, years[0]] = ev_sto_out[period, years[0]].x
            ev_sto_soc_results[period, years[0]] = ev_sto_soc[period, years[0]].x
            power_to_h2_results[period, years[0]] = power_to_h2[period, years[0]].x
            H2tInd_results[period, years[0]] = H2tInd[period, years[0]].x
        for unit in chp_units:
            for period in periods:
                noload_heat_dh_results[unit, period, years[0]] = noload_heat_dh[unit, period, years[0]].x
                heat_gen_results[unit, period, years[0]] = heat_gen[unit, period, years[0]].x
        for unit in FC_units:
            cap_inv_FC_results[unit, years[0]] = cap_inv_FC[unit, years[0]].x
            for period in periods:
                generation_FC_results[unit, period, years[0]] = generation_FC[unit, period, years[0]].x
        for unit in hydro_storage_units:
            for period in periods:
                storage_level_results[unit, period, years[0]] = storage_level[unit, period, years[0]].x
                sto_out_flow_results[unit, period, years[0]] = sto_out_flow[unit, period, years[0]].x
        for fuel in industry_fuels:
            for period in periods:
                Fuel_ind_results[fuel, period, years[0]] = Fuel_ind[fuel, period, years[0]].x
        for hp_unit in HeatPump_units:
            heat_pump_ind_cap_results[hp_unit, years[0]] = heat_pump_ind_cap[hp_unit, years[0]].x
            for period in periods:
                heat_pump_ind_results[hp_unit, period, years[0]] = heat_pump_ind[hp_unit, period, years[0]].x
            for chp in chp_units:
                heat_pump_dh_cap_results[hp_unit, chp, years[0]] = heat_pump_dh_cap[hp_unit, chp, years[0]].x
                for period in periods:
                    heat_pump_dh_results[hp_unit, chp, period, years[0]] = heat_pump_dh[hp_unit, chp, period, years[0]].x
        for unit in thermal_units:
            trad_boiler_ind_cap_inv_results[unit, years[0]] = trad_boiler_ind_cap_inv[unit, years[0]].x
            for period in periods:
                boiler_ind_generation_results[unit, period, years[0]] = boiler_ind_generation[unit, period, years[0]].x
            for chp in chp_units:
                trad_boiler_dh_cap_inv_results[unit, chp, years[0]] = trad_boiler_dh_cap_inv[unit, chp, years[0]].x
                for period in periods:
                    boiler_dh_generation_results[unit, chp, period, years[0]] = boiler_dh_generation[unit, chp, period, years[0]].x
        for unit in Stat_storage_unit:
            stat_sto_cap_inv_results[unit, years[0]] = stat_sto_cap_inv[unit, years[0]].x
            for period in periods:
                stat_sto_in_results[unit, period, years[0]] = stat_sto_in[unit, period, years[0]].x
                stat_sto_out_results[unit, period, years[0]] = stat_sto_out[unit, period, years[0]].x
        for unit in H2_storage_unit:
            h2_sto_max_cap_results[unit, years[0]] = h2_sto_max_cap[unit, years[0]].x
            for period in periods:
                h2_sto_soc_results[unit, period, years[0]] = h2_sto_soc[unit, period, years[0]].x
                h2_sto_out_results[unit, period, years[0]] = h2_sto_out[unit, period, years[0]].x
        for unit in Elec_h2_units:
            h2_electrolysis_max_cap_results[unit, years[0]] = h2_electrolysis_max_cap[unit, years[0]].x
            for period in periods:
                generation_H2_results[unit, period, years[0]] = generation_H2[unit, period, years[0]].x
        CO2_total_results[years[0]] = CO2_total[years[0]].x
        CO2_total_heat_results[years[0]] = CO2_total_heat[years[0]].x
        CO2_total_industry_results[years[0]] = CO2_total_industry[years[0]].x
        _full_checkpoint(globals())
