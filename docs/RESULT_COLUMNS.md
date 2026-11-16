# Current result columns and units

Current tables are in results/final. The archived results/channel_sensitivity
tables retain their original definitions.

- annual_costs.csv: annual EUR, capital payments including surviving additions,
  operations, one-off charges, same-year service-demand MWh and EUR/MWh.
- annual_cost_differences.csv: case minus Average, million EUR, each year once.
- annual_cost_components.csv: component EUR and differences that close to the total.
- capacity_additions.csv: realized additions, generation/heating MW and storage MWh.
- cumulative_capacity.csv: added solar/wind electrical GW and heat-pump thermal GW.
- average_energy_supply.csv: annual electricity and delivered heat, TWh.
- policy_checks.csv: gross/net trade TWh, no-new-gas checks, electricity/heating
  CO2 tonnes, limits, biomass boiler heat and unmet demand MWh.
- thermal_demand.csv: annual heat and household cooling electricity TWh.
- selected_case_inputs.csv: year, zero-based donor indices, full-profile SHA256.
- operating_results.csv: all 45 separate checks, annual unmet demand/CEEP in MWh/TWh.
- mean_profile_MAE.csv: six comparisons, solar/wind CF and temperature Celsius.
- fair_crps.csv: three fair ensemble scores on common reporting hours.

year_total_EUR includes one-off charges. Capital uses 5% and executed lives.
The original input-table currency basis is retained; no common price year or
inflation conversion is implied. Industry enters the demand denominator and
cost once, with prescribed fuel inputs. It is not an optimized transition.

For current four-hour storage additions, grid MW = native MWh * 0.9 / 4.
Existing stock keeps its ratings. Added capacity is not surviving end-year stock.
Krsko entitlement is included once in net imports and excluded from domestic
generation. A TWh equals 1000000 MWh. The operating portfolios are distinct
from the seven national paths. Unserved demand is annual energy.
