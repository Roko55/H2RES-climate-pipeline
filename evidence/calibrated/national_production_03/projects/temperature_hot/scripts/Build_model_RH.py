# -*- coding: utf-8 -*-
"""
Created on Fri Jan  8 18:44:57 2021
@author: Felipe Feijoo
"""
#%%
#create UCP model
import gurobipy as gp
from gurobipy import GRB
#import os

#%%
print('Building the model--> ...')
ucp = gp.Model('PowerGeneration')


##################################################################################################################################################################
###############################Variables##########################################################################################################################
##################################################################################################################################################################
rampup_cost   = ucp.addVars(disp_units, periods,years, vtype=GRB.CONTINUOUS, lb=0, name = 'ramp_up_cost_var')
rampdown_cost = ucp.addVars(disp_units, periods,years, vtype=GRB.CONTINUOUS, lb=0, name = 'ramp_cown_cost_var')
generation    = ucp.addVars(units, periods, years    , vtype=GRB.CONTINUOUS, lb=0, name = 'gen_output')
imports       = ucp.addVars(periods, years           , vtype=GRB.CONTINUOUS, lb=0, name = 'imports_period')
ceep          = ucp.addVars(periods, years           , vtype=GRB.CONTINUOUS, lb=0, name = 'ceep_period')
cap_inv       = ucp.addVars(units, years             , vtype=GRB.CONTINUOUS, lb=0, name = 'capacity_Investment')
extra_cap_inv = ucp.addVars(units, years             , vtype=GRB.CONTINUOUS, lb=0, name="extra_cap_inv")
noload_elec   = ucp.addVars(periods, years           , vtype=GRB.CONTINUOUS, lb=0, name = 'noload_elec')
noload_heat_ind   = ucp.addVars(periods, years       , vtype=GRB.CONTINUOUS, lb=0, name = 'noload_heat_ind')
noload_heat_dh   = ucp.addVars(chp_units, periods, years       , vtype=GRB.CONTINUOUS, lb=0, name = 'noload_heat_df')

CO2_total          = ucp.addVars(years, vtype=GRB.CONTINUOUS, lb=0, name = 'CO2_total_elect')
CO2_total_heat     = ucp.addVars(years, vtype=GRB.CONTINUOUS, lb=0, name = 'CO2_total_heat')
CO2_total_industry = ucp.addVars(years, vtype=GRB.CONTINUOUS, lb=0, name = 'CO2_total_industry')

#FuelCell variables
cap_inv_FC    = ucp.addVars(FC_units, years         , vtype=GRB.CONTINUOUS, lb=0, name = 'FC_capacity_Investment')
generation_FC = ucp.addVars(FC_units, periods, years, vtype=GRB.CONTINUOUS, lb=0, name = 'gen_output_FuelCell')

#Hydro Storage variables
storage_level = ucp.addVars(hydro_storage_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'Storage_level_Hydro')
sto_out_flow  = ucp.addVars(hydro_storage_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sto_out_flow_Hydro')

#Heat variables
heat_gen        = ucp.addVars(chp_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'heat_generation')
heat_input      = ucp.addVars(chp_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'heat_from_elect_storage_input')
heat_sto_level  = ucp.addVars(chp_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'heat_storage_level')

#Industry Heat Demand
Fuel_ind = ucp.addVars(industry_fuels, periods, years, vtype = GRB.CONTINUOUS, lb=0, name = "Industry fuel use")
H2tInd   = ucp.addVars(periods, years, vtype = GRB.CONTINUOUS, lb=0, name = "Industry H2 use")

#Heat pump (power to heat) and traditional boilers
heat_pump_out         = ucp.addVars(HeatPump_units,periods, years            , vtype= GRB.CONTINUOUS, lb=0, name = 'heat_pump_out')
heat_pump_dh          = ucp.addVars(HeatPump_units, chp_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'heat_pump_dh')
heat_pump_ind         = ucp.addVars(HeatPump_units, periods, years           , vtype= GRB.CONTINUOUS, lb=0, name = 'heat_pump_ind')
heat_pump_dh_cap      = ucp.addVars(HeatPump_units, chp_units, years         , vtype= GRB.CONTINUOUS, lb=0, name = 'Heat_pump_df_cap_inv')
heat_pump_ind_cap     = ucp.addVars(HeatPump_units, years                    , vtype= GRB.CONTINUOUS, lb=0, name = 'Heat_pump_ind_cap_inv')

#Heat pump for cooling
cooling_pump_dh   = ucp.addVars(HeatPump_units, chp_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'cooling_pump_dh') 
cooling_pump_ind  = ucp.addVars(HeatPump_units, periods, years           , vtype= GRB.CONTINUOUS, lb=0, name = 'cooling_pump_ind')

boiler_dh_generation    = ucp.addVars(thermal_units,chp_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'trad_boilers_Heat_dh')
boiler_ind_generation   = ucp.addVars(thermal_units, periods, years          , vtype= GRB.CONTINUOUS, lb=0, name = 'trad_boilers_Heat_ind')
trad_boiler_ind_cap_inv = ucp.addVars(thermal_units, years                   , vtype=GRB.CONTINUOUS, lb=0, name = 'capacity_Investment_ind_boiler')
trad_boiler_dh_cap_inv  = ucp.addVars(thermal_units,chp_units, years         , vtype=GRB.CONTINUOUS, lb=0, name = 'capacity_Investment_dh_boiler')

ResToHeat         = ucp.addVars(periods, years                , vtype= GRB.CONTINUOUS, lb=0, name = 'Res_to_Heat')
heat_pump_cap     = ucp.addVars(HeatPump_units, years         , vtype= GRB.CONTINUOUS, lb=0, name = 'Heat_pump_cap_inv')
heat_pump_sto_soc = ucp.addVars(HeatPump_units, periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'heatpump_sto_soc')

if NoResToHeatInv:
    no_inv_resToheat = ucp.addConstrs(heat_pump_cap[genco, year]<=0         for genco in chp_units for year in years)
    no_resToheat     = ucp.addConstrs(ResToHeat[genco, period, year]<=0     for genco in nondisp_units for period in periods for year in years)
    no_heat_pump     = ucp.addConstrs(heat_pump_out[genco, period, year]<=0 for genco in chp_units     for period in periods for year in years)

#EV VARIABLE STORAGE
ev_sto_in       = ucp.addVars(periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'ev_storage_in')
ev_sto_out      = ucp.addVars(periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'ev_storage_out')
ev_sto_soc      = ucp.addVars(periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'ev_sto_soc')

#STATIONARY STORAGE
stat_sto_in       = ucp.addVars(Stat_storage_unit,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'stat_storage_in')
stat_sto_out      = ucp.addVars(Stat_storage_unit,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'stat_storage_out')
stat_sto_soc      = ucp.addVars(Stat_storage_unit,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'stat_sto_soc')
stat_sto_cap_inv  = ucp.addVars(Stat_storage_unit, years        , vtype= GRB.CONTINUOUS, lb=0, name = 'stat_sto_cap_inv')

#power to H2
power_to_h2     = ucp.addVars(periods, years                , vtype= GRB.CONTINUOUS, lb=0, name = 'power_to_h2') 
h2_sto_soc      = ucp.addVars(H2_storage_unit,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'h2_sto_soc')
h2_sto_out      = ucp.addVars(H2_storage_unit,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'h2_storage_out')
h2_sto_max_cap  = ucp.addVars(H2_storage_unit,years         , vtype= GRB.CONTINUOUS, lb=0, name = 'h2_storage_cap') 
h2_electrolysis_max_cap = ucp.addVars(Elec_h2_units,years   , vtype= GRB.CONTINUOUS, lb=0, name = 'h2_electrolysis_cap') 
generation_H2   = ucp.addVars(Elec_h2_units,periods,years     , vtype= GRB.CONTINUOUS, lb=0, name = 'h2_electrolysis_gen') 

####################################################RESERVE VARIABLES###########################################################################################
#Balancing primary reserves
if PrimaryReserve == True:
    primary_hourly_reserve_up_units = ucp.addVars(primary_reserve_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_up_units')
    primary_hourly_reserve_down_units = ucp.addVars(primary_reserve_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_down_units')
    
    primary_hourly_stat_stoOUT_reserve_up_units = ucp.addVars(primary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_stoOUT_up_units')
    primary_hourly_stat_stoOUT_reserve_down_units = ucp.addVars(primary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_stoOUT_down_units')
    primary_hourly_stat_stoIN_reserve_up_units = ucp.addVars(primary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_stoIN_up_units')
    primary_hourly_stat_stoIN_reserve_down_units = ucp.addVars(primary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_stoIN_down_units')
    
    primary_hourly_FC_reserve_up = ucp.addVars(primary_reserve_FC_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_FC_up_units')
    primary_hourly_FC_reserve_down = ucp.addVars(primary_reserve_FC_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_FC_down_units')
    
    primary_hourly_ELY_reserve_down = ucp.addVars(primary_reserve_ELY_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_ELY_down_units')
    primary_hourly_ELY_reserve_up = ucp.addVars(primary_reserve_ELY_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_ELY_up_units')
    
    primary_hourly_HPind_reserve_down = ucp.addVars(primary_reserve_HP_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_HPind_down_units')
    primary_hourly_HPind_reserve_up = ucp.addVars(primary_reserve_HP_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'reserve_HPdh_up_units')
    
    tot_electric_load = ucp.addVars(periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'hourly_total_electric_load')
    max_cap_installed = ucp.addVars(years, vtype= GRB.CONTINUOUS, lb=0, name = 'max_installed_capacity')

#Balancing secondary reserves 
if SecondaryReserve == True:
    secondary_hourly_reserve_up_units = ucp.addVars(secondary_reserve_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_up_units')
    secondary_hourly_reserve_down_units = ucp.addVars(secondary_reserve_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_down_units')
    secondary_hourly_stat_stoOUT_reserve_up_units = ucp.addVars(secondary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_stoOUT_up_units')
    secondary_hourly_stat_stoOUT_reserve_down_units = ucp.addVars(secondary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_stoOUT_down_units')
    secondary_hourly_stat_stoIN_reserve_up_units = ucp.addVars(secondary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_stoIN_up_units')
    secondary_hourly_stat_stoIN_reserve_down_units = ucp.addVars(secondary_reserve_StatStorage_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_stoIN_down_units')
    
    secondary_hourly_FC_reserve_up = ucp.addVars(secondary_reserve_FC_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_FC_up_units')
    secondary_hourly_FC_reserve_down = ucp.addVars(secondary_reserve_FC_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_FC_down_units')
    
    secondary_hourly_ELY_reserve_down = ucp.addVars(secondary_reserve_ELY_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_ELY_down_units')
    secondary_hourly_ELY_reserve_up = ucp.addVars(secondary_reserve_ELY_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_ELY_up_units')
    
    secondary_hourly_HPind_reserve_down = ucp.addVars(secondary_reserve_HP_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_HPind_down_units')
    secondary_hourly_HPind_reserve_up = ucp.addVars(secondary_reserve_HP_units,periods, years, vtype= GRB.CONTINUOUS, lb=0, name = 'sec reserve_HPdh_up_units')



##################################################################EQUATIONS#######################################################################################

##################################################################################################################################################################
######################################################################## Primary Reserves ########################################################################
################################################################################################################################################################## 
if PrimaryReserve == True:       

    primary_reserve_up = ucp.addConstrs(gp.quicksum(primary_hourly_reserve_up_units[genco, period, year] for genco in primary_reserve_units) + gp.quicksum(primary_hourly_stat_stoOUT_reserve_up_units[genco, period, year] for genco in primary_reserve_StatStorage_units) + gp.quicksum(primary_hourly_stat_stoIN_reserve_up_units[genco, period, year] for genco in primary_reserve_StatStorage_units) + gp.quicksum(primary_hourly_FC_reserve_up[genco, period, year] for genco in primary_reserve_FC_units) + gp.quicksum(primary_hourly_ELY_reserve_up[genco, period, year] for genco in primary_reserve_ELY_units) + gp.quicksum(primary_hourly_HPind_reserve_up[genco, period, year] for genco in primary_reserve_HP_units) >= 0.2*gp.quicksum(generation[unit,period,year] for unit in nondisp_units) + max_cap_installed[year] + 0.1*tot_electric_load[period, year] for period in periods for year in years)

#    primary_reserve_up = ucp.addConstrs(gp.quicksum(primary_hourly_reserve_up_units[genco, period, year] for genco in primary_reserve_units) + gp.quicksum(primary_hourly_HPind_reserve_up[genco, period, year] for genco in primary_reserve_HP_units) + gp.quicksum(primary_hourly_FC_reserve_up[genco, period, year] for genco in primary_reserve_FC_units) + gp.quicksum(primary_hourly_stat_stoOUT_reserve_up_units[genco, period, year] for genco in primary_reserve_StatStorage_units) + gp.quicksum(primary_hourly_stat_stoIN_reserve_up_units[genco, period, year] for genco in primary_reserve_StatStorage_units) + gp.quicksum(primary_hourly_ELY_reserve_up[genco, period, year] for genco in primary_reserve_ELY_units)>= 0.5*gp.quicksum(generation[unit,period,year] for unit in nondisp_units) + max_cap_installed[year] + 0.1*tot_electric_load[period, year] for period in periods for year in years)

    primary_hourly_reserve_up_units_minimum1 = ucp.addConstrs(primary_hourly_reserve_up_units[genco, period, year] <= generation[genco, period, year] for genco in primary_reserve_units for period in periods for year in years)
    primary_hourly_reserve_up_units_minimum2 = ucp.addConstrs(primary_hourly_reserve_up_units[genco, period, year] <= max_load[genco] * round(((1 - decom_rate[genco]) ** (max(decom_start_old[genco] - 1, year - hor_yrs[0]) - (decom_start_old[genco] - 1))), 3) + 
                                                              cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3) - generation[genco, period, year]
                                                              for genco in primary_reserve_units for period in periods for year in years) #equation updated
    primary_hourly_reserve_up_units_minimum3 = ucp.addConstrs(primary_hourly_reserve_up_units[genco, period, year] <= (max_load[genco]* round(((1-decom_rate[genco]) ** (max(decom_start_old[genco] - 1 ,year - hor_yrs[0]) - (decom_start_old[genco]-1))), 3) + 
                                                                                                                       cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))*ramp_up_rate[genco] 
                                                              for genco in primary_reserve_units for period in periods for year in years) #equation updated
    primary_hourly_batteryOUT_reserve_up_min1 = ucp.addConstrs(primary_hourly_stat_stoOUT_reserve_up_units[genco, period, year] <= stat_sto_soc[genco,period, year] for genco in primary_reserve_StatStorage_units for period in periods for year in years)
    primary_hourly_batteryIN_reserve_up_min1 = ucp.addConstrs(primary_hourly_stat_stoIN_reserve_up_units[genco, period, year] <= stat_sto_in[genco,period, year] for genco in primary_reserve_StatStorage_units for period in periods for year in years)

    primary_hourly_FC_reserve_up_min1 = ucp.addConstrs(primary_hourly_FC_reserve_up[genco, period, year] <= FC_init_cap[genco] + cap_inv_FC_cumulative[genco, year] + cap_inv_FC[genco, year] - generation_FC[genco, period, year] for genco in primary_reserve_FC_units for period in periods for year in years) #equation updated  
    primary_hourly_FC_reserve_up_min2 = ucp.addConstrs(primary_hourly_FC_reserve_up[genco, period, year] <= gp.quicksum(h2_sto_soc[sto_unit,period, year] - gp.quicksum(H2_demand[sector, period, year] for sector in demand_sectors) for sto_unit in H2_storage_unit)*FC_eff[genco] for genco in primary_reserve_FC_units for period in periods for year in years)
    primary_hourly_FC_reserve_up_min3 = ucp.addConstrs(primary_hourly_FC_reserve_up[genco, period, year] <= generation_FC[genco, period, year] for genco in primary_reserve_FC_units for period in periods for year in years)

    primary_hourly_ELY_reserve_up_min1 = ucp.addConstrs(primary_hourly_ELY_reserve_up[genco, period, year] <= (generation_H2[genco, period, year])/H2_eff[genco] for genco in primary_reserve_ELY_units for period in periods for year in years)
    primary_hourly_ELY_reserve_up_min2 = ucp.addConstrs(primary_hourly_ELY_reserve_up[genco, period, year] <= gp.quicksum(H2_sto_init[sto_unit] + h2_sto_max_cap_cumulative[sto_unit,year] + h2_sto_max_cap[sto_unit,year] - h2_sto_out[sto_unit,period, year] for sto_unit in H2_storage_unit) /H2_eff[genco] for genco in primary_reserve_ELY_units for period in periods for year in years) #equation updated
    
    primary_hourly_HPind_reserve_up_min1 = ucp.addConstrs(primary_hourly_HPind_reserve_up[genco, period, year] <= heat_pump_ind[genco,period, year]/HP_COP_period[genco,period, year] for genco in primary_reserve_HP_units for period in periods for year in years)
    primary_hourly_HPind_reserve_up_min2 = ucp.addConstrs(primary_hourly_HPind_reserve_up[genco, period, year] <= (heat_pump_sto_soc[genco,period, year] - heat_pump_out[genco,period, year])/HP_COP_period[genco,period, year] for genco in primary_reserve_HP_units for period in periods for year in years)

    TOT_hourly_electricity_load = ucp.addConstrs(tot_electric_load[period, year] == power_to_h2[period, year] + ResToHeat[period, year] + gp.quicksum(demand[sector,period, year] for sector in demand_sectors) + ev_sto_in[period, year] for period in periods for year in years)
    yearly_max_cap_installed = ucp.addConstrs(max_cap_installed[year] >= max_load[genco]*round(((1-decom_rate[genco]) ** (max(decom_start_old[genco]-1 ,year- hor_yrs[0]) - (decom_start_old[genco]-1))), 3) + cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3) 
                                              for genco in disp_units for year in years) #equation updated
    primary_reserve_down = ucp.addConstrs(gp.quicksum(primary_hourly_reserve_down_units[genco, period, year] for genco in primary_reserve_units) + gp.quicksum(primary_hourly_stat_stoOUT_reserve_down_units[genco, period, year] for genco in primary_reserve_StatStorage_units) + gp.quicksum(primary_hourly_stat_stoIN_reserve_down_units[genco, period, year] for genco in Stat_storage_unit) + gp.quicksum(primary_hourly_FC_reserve_down[genco, period, year] for genco in primary_reserve_FC_units) + gp.quicksum(primary_hourly_ELY_reserve_down[genco, period, year] for genco in primary_reserve_ELY_units) + gp.quicksum(primary_hourly_HPind_reserve_down[genco, period, year] for genco in primary_reserve_HP_units) >= 0.2*gp.quicksum(generation[unit,period,year] for unit in nondisp_units) + 0.1*tot_electric_load[period, year] for period in periods for year in years)

    primary_hourly_reserve_down_units_minimum1 = ucp.addConstrs(primary_hourly_reserve_down_units[genco, period, year] <= generation[genco, period, year]*reserve_factor[genco] for genco in primary_reserve_units for period in periods for year in years)  
    primary_hourly_reserve_down_units_minimum2 = ucp.addConstrs(primary_hourly_reserve_up_units[genco, period, year] <= (max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year-hor_yrs[0])-(decom_start_old[genco]-1))),3) + cap_inv_cumulative[genco, year] + 
                                                                                                                         cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))*ramp_down_rate[genco] 
                                                                for genco in primary_reserve_units for period in periods for year in years) #equation updated
    primary_hourly_batteryOUT_reserve_down_min1 = ucp.addConstrs(primary_hourly_stat_stoOUT_reserve_down_units[genco, period, year] <= stat_sto_out[genco,period, year] for genco in primary_reserve_StatStorage_units for period in periods for year in years)
    primary_hourly_batteryIN_reserve_down_min1 = ucp.addConstrs(primary_hourly_stat_stoIN_reserve_down_units[genco, period, year] <= Sta_sto_init_cap[genco]*StaSto_Decom_ind[year]  + stat_sto_cap_inv_cumulative[genco, year] + stat_sto_cap_inv[genco, year]*round(((1-Sta_sto_decom_rate[genco])**(max(Sta_sto_decom_start_new[genco]-1 ,0)-(Sta_sto_decom_start_new[genco]-1))),3) - stat_sto_soc[genco,period, year] for genco in primary_reserve_StatStorage_units for period in periods for year in years) #equation updated
    
    primary_hourly_FC_reserve_down_minimum1 = ucp.addConstrs(primary_hourly_FC_reserve_down[genco, period, year] <= generation_FC[genco, period, year] for genco in primary_reserve_FC_units for period in periods for year in years)

    primary_hourly_ELY_reserve_down_minimum1 = ucp.addConstrs(primary_hourly_ELY_reserve_down[genco, period, year] <= H2_init_cap[genco] + h2_electrolysis_max_cap_cumulative[genco, year] + h2_electrolysis_max_cap[genco,year] - generation_H2[genco, period, year]*H2_eff[genco] for genco in primary_reserve_ELY_units for period in periods for year in years) #equation updated
    primary_hourly_ELY_reserve_down_minimum2 = ucp.addConstrs(primary_hourly_ELY_reserve_down[genco, period, year] <= gp.quicksum(H2_sto_init[sto_unit] + h2_sto_max_cap_cumulative[sto_unit,year] + h2_sto_max_cap[sto_unit,year] - h2_sto_soc[sto_unit,period, year] for sto_unit in H2_storage_unit)/H2_eff[genco] for genco in primary_reserve_ELY_units for period in periods for year in years) #equation updated

    primary_hourly_HPind_reserve_down_min1 = ucp.addConstrs(primary_hourly_HPind_reserve_down[genco, period, year] <= (HP_ind_init_cap[genco]*HP_Decom_ind[year] +  heat_pump_ind_cap_cumulative[genco, year] + heat_pump_ind_cap[genco, year]*round(((1-HP_decom_rate[genco])**(max(HP_decom_start_new[genco]-1, 0)-(HP_decom_start_new[genco]-1))),3) - heat_pump_ind[genco,period, year])/HP_COP_period[genco,period, year] for genco in primary_reserve_HP_units for period in periods for year in years) #equation updated
    primary_hourly_HPind_reserve_down_min2 = ucp.addConstrs(primary_hourly_HPind_reserve_down[genco, period, year] <= (HP_sto_init[genco] - heat_pump_sto_soc[genco,period, year])/HP_COP_period[genco,period, year] for genco in primary_reserve_HP_units for period in periods for year in years)



##################################################################################################################################################################
#################################################################### Secondary Reserves ########################################################################
##################################################################################################################################################################           
if SecondaryReserve == True:       

    secondary_reserve_up = ucp.addConstrs(gp.quicksum(secondary_hourly_reserve_up_units[genco, period, year] for genco in secondary_reserve_units) + gp.quicksum(secondary_hourly_stat_stoOUT_reserve_up_units[genco, period, year] for genco in secondary_reserve_StatStorage_units) + gp.quicksum(secondary_hourly_stat_stoIN_reserve_up_units[genco, period, year] for genco in secondary_reserve_StatStorage_units) + gp.quicksum(secondary_hourly_FC_reserve_up[genco, period, year] for genco in secondary_reserve_FC_units) + gp.quicksum(secondary_hourly_ELY_reserve_up[genco, period, year] for genco in secondary_reserve_ELY_units) + gp.quicksum(secondary_hourly_HPind_reserve_up[genco, period, year] for genco in secondary_reserve_HP_units) >= 0.2*gp.quicksum(generation[unit,period,year] for unit in nondisp_units) + max_cap_installed[year] + 0.1*tot_electric_load[period, year] for period in periods for year in years)


    secondary_hourly_reserve_up_units_minimum1 = ucp.addConstrs(secondary_hourly_reserve_up_units[genco, period, year] <= max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                                                cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3) - generation[genco, period, year] 
                                                                for genco in secondary_reserve_units for period in periods for year in years) #equation updated
    secondary_hourly_reserve_up_units_minimum2 = ucp.addConstrs(secondary_hourly_reserve_up_units[genco, period, year] <= (max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                                                                                                           cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))*ramp_up_rate[genco] 
                                                                for genco in secondary_reserve_units for period in periods for year in years) #equation updated
    secondary_hourly_batteryOUT_reserve_up_min1 = ucp.addConstrs(secondary_hourly_stat_stoOUT_reserve_up_units[genco, period, year] <= stat_sto_soc[genco,period, year] for genco in secondary_reserve_StatStorage_units for period in periods for year in years)
    secondary_hourly_batteryIN_reserve_up_min1 = ucp.addConstrs(secondary_hourly_stat_stoIN_reserve_up_units[genco, period, year] <= stat_sto_in[genco,period, year] for genco in secondary_reserve_StatStorage_units for period in periods for year in years)

    secondary_hourly_FC_reserve_up_min1 = ucp.addConstrs(secondary_hourly_FC_reserve_up[genco, period, year] <= FC_init_cap[genco] + cap_inv_FC_cumulative[genco, year] + cap_inv_FC[genco, year] - generation_FC[genco, period, year] for genco in secondary_reserve_FC_units for period in periods for year in years) #equation updated
    secondary_hourly_FC_reserve_up_min2 = ucp.addConstrs(secondary_hourly_FC_reserve_up[genco, period, year] <= gp.quicksum(h2_sto_soc[sto_unit,period, year] - gp.quicksum(H2_demand[sector, period, year] for sector in demand_sectors) for sto_unit in H2_storage_unit)*FC_eff[genco] for genco in secondary_reserve_FC_units for period in periods for year in years)

    secondary_hourly_ELY_reserve_up_min1 = ucp.addConstrs(secondary_hourly_ELY_reserve_up[genco, period, year] <= (generation_H2[genco, period, year])/H2_eff[genco] for genco in secondary_reserve_ELY_units for period in periods for year in years)
    secondary_hourly_ELY_reserve_up_min2 = ucp.addConstrs(secondary_hourly_ELY_reserve_up[genco, period, year] <= gp.quicksum(H2_sto_init[sto_unit] + h2_sto_max_cap_cumulative[sto_unit,year] + h2_sto_max_cap[sto_unit,year] - h2_sto_out[sto_unit,period, year] for sto_unit in H2_storage_unit)/H2_eff[genco] for genco in secondary_reserve_ELY_units for period in periods for year in years) #equation updated

    secondary_hourly_HPind_reserve_up_min1 = ucp.addConstrs(secondary_hourly_HPind_reserve_up[genco, period, year] <= heat_pump_ind[genco,period, year]/HP_COP_period[genco,period, year] for genco in secondary_reserve_HP_units for period in periods for year in years)
    secondary_hourly_HPind_reserve_up_min2 = ucp.addConstrs(secondary_hourly_HPind_reserve_up[genco, period, year] <= (heat_pump_sto_soc[genco,period, year] - heat_pump_out[genco,period, year])/HP_COP_period[genco,period, year] for genco in secondary_reserve_HP_units for period in periods for year in years)

    TOT_hourly_electricity_load = ucp.addConstrs(tot_electric_load[period, year] == power_to_h2[period, year] + ResToHeat[period, year] + gp.quicksum(demand[sector,period, year] for sector in demand_sectors) + ev_sto_in[period, year] for period in periods for year in years)
    yearly_max_cap_installed = ucp.addConstrs(max_cap_installed[year] >= max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3) for genco in disp_units for year in years) #equation updated
    
    secondary_reserve_down = ucp.addConstrs(gp.quicksum(secondary_hourly_reserve_down_units[genco, period, year] for genco in secondary_reserve_units) + gp.quicksum(secondary_hourly_stat_stoOUT_reserve_down_units[genco, period, year] for genco in secondary_reserve_StatStorage_units) + gp.quicksum(secondary_hourly_stat_stoIN_reserve_down_units[genco, period, year] for genco in secondary_reserve_StatStorage_units) + gp.quicksum(secondary_hourly_FC_reserve_down[genco, period, year] for genco in secondary_reserve_FC_units) + gp.quicksum(secondary_hourly_ELY_reserve_down[genco, period, year] for genco in secondary_reserve_ELY_units) + gp.quicksum(secondary_hourly_HPind_reserve_down[genco, period, year] for genco in secondary_reserve_HP_units) >= 0.2*gp.quicksum(generation[unit,period,year] for unit in nondisp_units) + 0.1*tot_electric_load[period, year] for period in periods for year in years)

    secondary_hourly_reserve_down_units_minimum1 = ucp.addConstrs(secondary_hourly_reserve_down_units[genco, period, year] <= generation[genco, period, year]*reserve_factor[genco] for genco in secondary_reserve_units for period in periods for year in years)  
    secondary_hourly_reserve_down_units_minimum2 = ucp.addConstrs(secondary_hourly_reserve_up_units[genco, period, year] <= (max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                                                                                                             cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))*ramp_down_rate[genco] 
                                                                  for genco in secondary_reserve_units for period in periods for year in years) #equation updated
    secondary_hourly_batteryOUT_reserve_down_min1 = ucp.addConstrs(secondary_hourly_stat_stoOUT_reserve_down_units[genco, period, year] <= stat_sto_out[genco,period, year] for genco in secondary_reserve_StatStorage_units for period in periods for year in years)
    secondary_hourly_batteryIN_reserve_down_min1 = ucp.addConstrs(secondary_hourly_stat_stoIN_reserve_down_units[genco, period, year] <= Sta_sto_init_cap[genco]*StaSto_Decom_ind[year] + stat_sto_cap_inv_cumulative[genco, year] + stat_sto_cap_inv[genco, year]*round(((1-Sta_sto_decom_rate[genco])**(max(Sta_sto_decom_start_new[genco]-1 ,0)-(Sta_sto_decom_start_new[genco]-1))),3) - stat_sto_soc[genco,period, year] for genco in secondary_reserve_StatStorage_units for period in periods for year in years) #equation updated
    
    secondary_hourly_FC_reserve_down_minimum1 = ucp.addConstrs(secondary_hourly_FC_reserve_down[genco, period, year] <= generation_FC[genco, period, year] for genco in secondary_reserve_FC_units for period in periods for year in years)

    secondary_hourly_ELY_reserve_down_minimum1 = ucp.addConstrs(secondary_hourly_ELY_reserve_down[genco, period, year] <= H2_init_cap[genco] + h2_electrolysis_max_cap_cumulative[genco, year] + h2_electrolysis_max_cap[genco,year] - generation_H2[genco, period, year]*H2_eff[genco] for genco in secondary_reserve_ELY_units for period in periods for year in years) #equation updated
    secondary_hourly_ELY_reserve_down_minimum2 = ucp.addConstrs(secondary_hourly_ELY_reserve_down[genco, period, year] <= gp.quicksum(H2_sto_init[sto_unit] + h2_sto_max_cap_cumulative[sto_unit,year] + h2_sto_max_cap[sto_unit,year] - h2_sto_soc[sto_unit,period, year] for sto_unit in H2_storage_unit)/H2_eff[genco] for genco in secondary_reserve_ELY_units for period in periods for year in years) #equation updated

    secondary_hourly_HPind_reserve_down_min1 = ucp.addConstrs(secondary_hourly_HPind_reserve_down[genco, period, year] <= (HP_ind_init_cap[genco]*HP_Decom_ind[year] + heat_pump_ind_cap_cumulative[genco, year] + heat_pump_ind_cap[genco, year]*round(((1-HP_decom_rate[genco])**(max(HP_decom_start_new[genco]-1, 0)-(HP_decom_start_new[genco]-1))),3) - heat_pump_ind[genco,period, year])/HP_COP_period[genco,period, year] for genco in secondary_reserve_HP_units for period in periods for year in years) #equation updated
    secondary_hourly_HPind_reserve_down_min2 = ucp.addConstrs(secondary_hourly_HPind_reserve_down[genco, period, year] <= (HP_sto_init[genco] - heat_pump_sto_soc[genco,period, year])/HP_COP_period[genco,period, year] for genco in secondary_reserve_HP_units for period in periods for year in years)

##################################################################################################################################################################
################################################################## IND DEMAND#####################################################################################
##################################################################################################################################################################

ind_demand_meet = ucp.addConstrs((Fuel_ind[fuel, period, year] <= 
                                 fuel_share_max[period,year,fuel]*(Industry_HT_demand[period, year] - H2tInd[period, year])) for fuel in industry_fuels for period in periods for year in years )

industry_demand_meet = ucp.addConstrs(gp.quicksum(Fuel_ind[fuel, period, year] for fuel in industry_fuels) +  H2tInd[period, year] == Industry_HT_demand[period, year] for period in periods for year in years)

##################################################################################################################################################################
#################################################################### H2 demand and storage########################################################################
##################################################################################################################################################################           
Power_to_h2 = ucp.addConstrs(gp.quicksum(generation_H2[flex_unit, period, year]/H2_eff[flex_unit] for flex_unit in Elec_h2_units) == power_to_h2[period, year]    for flex_unit in Elec_h2_units for period in periods for year in years)

h2_electrolysis  = ucp.addConstrs(generation_H2[flex_unit, period, year] <= H2_init_cap[flex_unit] + h2_electrolysis_max_cap_cumulative[flex_unit, year] + h2_electrolysis_max_cap[flex_unit, year]
                                  for flex_unit in Elec_h2_units for period in periods  for year in years) #equation updated

h2_storage_soc_0 = ucp.addConstrs(h2_sto_soc[sto_unit,1, year]      == 0.0*h2_sto_soc[sto_unit,1, year] - h2_sto_out[sto_unit,1, year] +
                                  gp.quicksum(generation_H2[flex_unit, 1, year] for flex_unit in Elec_h2_units)                                                   for sto_unit in H2_storage_unit for year in years) 
 
h2_storage_soc   = ucp.addConstrs(h2_sto_soc[sto_unit,period, year] == h2_sto_soc[sto_unit,period-1, year] - h2_sto_out[sto_unit,period, year] +
                                  gp.quicksum(generation_H2[flex_unit, period, year] for flex_unit in Elec_h2_units)                                              for sto_unit in H2_storage_unit for period in periods[1:] for year in years)

h2_storage_max   = ucp.addConstrs(h2_sto_soc[sto_unit,period, year] <= H2_sto_init[sto_unit] + h2_sto_max_cap_cumulative[sto_unit,year] + h2_sto_max_cap[sto_unit,year]  
                                  for sto_unit in H2_storage_unit for period in periods  for year in years) #equation updated

h2_storage_min   = ucp.addConstrs(h2_sto_soc[sto_unit,period, year] >= 0                                             for sto_unit in H2_storage_unit for period in periods  for year in years)

h2_storage_last  = ucp.addConstrs(h2_sto_soc[sto_unit,len(periods), year] <= 
                                  0.2*(H2_sto_init[sto_unit] + h2_sto_max_cap_cumulative[sto_unit,year] + h2_sto_max_cap[sto_unit,year]) 
                                  for sto_unit in H2_storage_unit for year in years) #equation updated

max_output_FCell = ucp.addConstrs(generation_FC[genco, period, year] <= 
                                   FC_init_cap[genco] + cap_inv_FC_cumulative[genco, year] + cap_inv_FC[genco, year] for genco in FC_units for period in periods for year in years) #equation updated

h2_demand        = ucp.addConstrs((gp.quicksum(h2_sto_out[genco,period, year] for genco in H2_storage_unit) == 
                                   gp.quicksum(H2_demand[sector, period, year] for sector in demand_sectors) + 
                                   gp.quicksum(generation_FC[genco, period, year]/FC_eff[genco] for genco in FC_units) +
                                   H2tInd[period, year])
                                           for period in periods for year in years)

max_FC_inv_cap           = ucp.addConstrs(cap_inv_FC[flex_unit, year] <= FC_max_cap[flex_unit]/ len(hor_yrs)   for flex_unit in FC_units for year in years) #equation updated
max_H2_storage_inv_cap   = ucp.addConstrs(h2_sto_max_cap[flex_unit, year] <= H2_sto_max_cap[flex_unit]/ len(hor_yrs)   for flex_unit in H2_storage_unit for year in years) #equation updated
max_Elec_h2_inv_cap      = ucp.addConstrs(h2_electrolysis_max_cap[flex_unit, year] <= H2_max_cap[flex_unit]/ len(hor_yrs)   for flex_unit in Elec_h2_units for year in years) #equation updated

##################################################################################################################################################################
####################################################################EV VARIABLE STORAGE###########################################################################
##################################################################################################################################################################
ev_storage_soc_0 = ucp.addConstrs(ev_sto_soc[1, year] == 
                                 0.0*ev_sto_max[year] - 
                                 ev_transp_load[1, year]*ev_Demand[year] - 
                                 ev_sto_out[1, year] + 
                                 ev_Grid_eff*ev_sto_in[1, year] for year in years)  

ev_storage_soc   = ucp.addConstrs(ev_sto_soc[period, year] == 
                                  ev_sto_soc[period-1, year] - 
                                  ev_transp_load[period, year]*ev_Demand[year] - 
                                  ev_sto_out[period, year]+ 
                                  ev_Grid_eff*ev_sto_in[period, year]     for period in periods[1:] for year in years)

ev_storage_max      = ucp.addConstrs(ev_sto_soc[period, year] <= ev_sto_max[year] for period in periods for year in years)
ev_storage_min      = ucp.addConstrs(ev_sto_soc[period, year] >= ev_sto_min*ev_sto_max[year] for period in periods for year in years)

ev_powerOUT_max     = ucp.addConstrs(ev_sto_out[period, year] <= ev_Grid_P_max[year]*ev_availability[period, year] for period in periods for year in years)
ev_powerIN_max      = ucp.addConstrs(ev_sto_in[period, year]  <= ev_Grid_P_max[year]*ev_availability[period, year] for period in periods for year in years)

##################################################################################################################################################################
####################################################################STATIOTNARY STORAGE###########################################################################
##################################################################################################################################################################
stat_storage_soc_0 = ucp.addConstrs(stat_sto_soc[unit,1, year] == 
                                 0.0*Sta_sto_max_cap[unit] -
                                 stat_sto_out[unit,1, year] + 
                                 stat_sto_in[unit,1, year]*Sta_sto_eff[unit]  for unit in Stat_storage_unit for year in years )  

stat_storage_soc   = ucp.addConstrs(stat_sto_soc[unit,period, year] == 
                                  stat_sto_soc[unit,period-1, year] - 
                                  stat_sto_out[unit,period, year]+ 
                                  stat_sto_in[unit,period, year]*Sta_sto_eff[unit]  for period in periods[1:] for year in years for unit in Stat_storage_unit)

stat_storage_max   = ucp.addConstrs(stat_sto_soc[unit,period, year]<= 
                                    Sta_sto_init_cap[unit]*StaSto_Decom_ind[year] + stat_sto_cap_inv_cumulative[unit, year] + 
                                    stat_sto_cap_inv[unit, year]*round(((1-Sta_sto_decom_rate[unit])**(max(Sta_sto_decom_start_new[unit]-1 ,0)-(Sta_sto_decom_start_new[unit]-1))),3)
                                    for unit in Stat_storage_unit for period in periods for year in years) #equation updated


stat_storage_inv_max   = ucp.addConstrs(stat_sto_cap_inv[unit, year] <= Sta_sto_max_cap[unit]/ len(hor_yrs)   for unit in Stat_storage_unit for year in years) #equation updated

stat_storage_last_max  = ucp.addConstrs(stat_sto_soc[unit, len(periods), year]  <= 0.2*Sta_sto_max_cap[unit]  for unit in Stat_storage_unit for year in years)

##################################################################################################################################################################
##################################Respect minimum and maximum output per generator type###########################################################################
##################################################################################################################################################################
max_output       = ucp.addConstrs(generation[genco, period, year] <= max_load[genco]*cap_factor[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year - hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                  cap_inv_cumulative[genco, year] *cap_factor[genco] + 
                                  cap_inv[genco, year] *cap_factor[genco] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3) 
                                  for genco in fossil_units  for period in periods for year in years) #equation updated

max_output_ncre  = ucp.addConstrs((generation[genco, period, year] == (max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                                                       cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))* aval_factor_plant[genco,period, year])  
                                  for genco in nondisp_units for period in periods for year in years) #equation updated



max_output_hphs  = ucp.addConstrs((generation[genco, period, year] <=  max_load[genco]*cap_factor[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                   cap_inv_cumulative[genco, year] *cap_factor[genco] + cap_inv[genco, year] *cap_factor[genco] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))
                                                                                                                              for genco in hphs_units    for period in periods for year in years) #equation updated



max_output_hdam  = ucp.addConstrs((generation[genco, period, year] <= 
                                   max_load[genco]*cap_factor[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                   cap_inv_cumulative[genco, year] *cap_factor[genco] + cap_inv[genco, year] *cap_factor[genco] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3)) 
                                  for genco in hdam_units for period in periods for year in years) #equation updated

max_output_hror  = ucp.addConstrs((generation[genco, period, year] == 
                                   (max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + 
                                    cap_inv_cumulative[genco, year] + cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3)) * aval_factor_plant[genco,period, year])  
                                  for genco in hror_units    for period in periods for year in years) #equation updated


max_inv_unit      = ucp.addConstrs((cap_inv[genco, year] <= max_inv_period[genco])                                             for year in years for genco in units)
extra_inv_unit    = ucp.addConstrs((extra_cap_inv[genco, year] >= cap_inv[genco, year] - 0.8 * max_inv_period[genco])          for year in years for genco in units)


if not fossil_dispatch:
	no_fossil_dispatch   = ucp.addConstrs((generation[genco, period, year]  == 0)            for year in years for period in periods  for genco in coal_gas_diesel_units)

################################################################################################################################################################    
####################################Meet demand#################################################################################################################
################################################################################################################################################################
meet_demand = ucp.addConstrs((    gp.quicksum(generation[genco, period, year]    for genco in fossil_units)  + 
                                  gp.quicksum(generation[genco, period, year]    for genco in hphs_units)    + 
                                  gp.quicksum(generation[genco, period, year]    for genco in hdam_units)    + 
                                  gp.quicksum(generation[genco, period, year]    for genco in hror_units)    + 
                                  gp.quicksum(generation_FC[genco, period, year] for genco in FC_units)      +
                                  gp.quicksum(generation[genco, period, year]    for genco in nondisp_units) +
                                  gp.quicksum(stat_sto_out[unit,period, year]*Sta_sto_eff[unit] for unit in Stat_storage_unit) +
                                  imports[period, year] +
                                  ev_Grid_eff*ev_sto_out[period, year]  + 
                                  noload_elec[period, year]
                                  == 
                                  gp.quicksum(demand[sector,period, year]    for sector in demand_sectors) +
                                  gp.quicksum(stat_sto_in[unit,period, year] for unit in Stat_storage_unit) +
                                  ev_sto_in[period, year] + 
                                  power_to_h2[period, year] +
                                  ResToHeat[period, year]  +
                                  ceep[period, year]) 
                                 for period in periods for year in years)
    
    
if ceep_limit: ceep_demand = ucp.addConstrs((gp.quicksum(ceep[period, year] for period in periods)
                                             <=
                                             ceep_parameter*(gp.quicksum(demand[sector,period, year]    for sector in demand_sectors for period in periods)+
                                                             gp.quicksum(stat_sto_in[unit,period, year] - stat_sto_out[unit,period, year]/Sta_sto_eff[unit]
                                                                         for unit in Stat_storage_unit  for period in periods)+
                                                             gp.quicksum(ResToHeat[period,year]         for period in periods )+
                                                             gp.quicksum(power_to_h2[period, year]      for period in periods)+
                                                             gp.quicksum(ev_sto_in[period, year] - ev_sto_out[period, year]/ev_Grid_eff for period in periods)))
                                            for year in years)

################################################################################################################################################################
####################################################################HEAT PUMP SYSTEM############################################################################
################################################################################################################################################################
if NoResToHeatInv == False:       
    heat_pump_ResGen = ucp.addConstrs((gp.quicksum((heat_pump_dh[genco,chp_market,period, year]+
                                                    cooling_pump_dh[genco,chp_market,period, year])/HP_COP_period[genco,period, year] 
                                                   for genco in HeatPump_units for chp_market in chp_units) +
                                       gp.quicksum(heat_pump_ind[genco,period, year]/HP_COP_period[genco,period, year] for genco in HeatPump_units)
                                       == 
                                       ResToHeat[period, year])                                                                                                    for period in periods for year in years)
    
    no_boiler_cooling_DH = ucp.addConstr(gp.quicksum(cooling_pump_dh['electric_boiler', chp_market,period, year] for genco in HeatPump_units for chp_market in chp_units for period in periods for year in years) == 0)
    no_boiler_cooling_ind = ucp.addConstr(gp.quicksum(cooling_pump_ind['electric_boiler', period, year] for period in periods for year in years) == 0)

    
    heat_pump_dh_max = ucp.addConstrs(heat_pump_dh[genco,chp_market,period, year] + cooling_pump_dh[genco,chp_market,period, year] <= 
                                      HP_Boiler_dh_init_cap[genco, chp_market]*HP_Decom_ind[year]  + heat_pump_dh_cap_cumulative[genco, chp_market, year] + 
                                      heat_pump_dh_cap[genco,chp_market, year]*round(((1-int(HP_decom_rate[genco]))**(max(HP_decom_start_new[genco]-1, 0)-(HP_decom_start_new[genco]-1))),3)      
                                      for genco in HeatPump_units for chp_market in chp_units for period in periods for year in years) #equation updated
    
    heat_pump_ind_max = ucp.addConstrs(heat_pump_ind[genco,period, year] <= 
                                       HP_ind_init_cap[genco]*HP_Decom_ind[year] + heat_pump_ind_cap_cumulative[genco, year] + 
                                       heat_pump_ind_cap[genco, year]*round(((1-int(HP_decom_rate[genco]))**(max(HP_decom_start_new[genco]-1, 0)-(HP_decom_start_new[genco]-1))),3) 
                                       for genco in HeatPump_units for period in periods for year in years) #equation updated
    
    heat_pump_soc     = ucp.addConstrs((heat_pump_sto_soc[genco,period, year] ==
                                        heat_pump_sto_soc[genco,period - 1, year] + 
                                        heat_pump_ind[genco,period, year] - 
                                        heat_pump_out[genco,period, year] - 
                                        cooling_pump_ind[genco,period, year])
                                       for genco in HeatPump_units for period in periods[1:] for year in years)
    
    heat_pump_soc_0   = ucp.addConstrs((heat_pump_sto_soc[genco,1, year] ==
                                        heat_pump_ind[genco,1, year] - 
                                        heat_pump_out[genco,1, year]- 
                                        cooling_pump_ind[genco,1, year])
                                       for genco in HeatPump_units for year in years)
    
    heat_pump_soc_max  = ucp.addConstrs((heat_pump_sto_soc[genco,period, year]       <= HP_sto_init[genco])                         for genco in HeatPump_units for period in periods for year in years)
    heat_pump_soc_last = ucp.addConstrs((heat_pump_sto_soc[genco,len(periods), year] <= 0.2*HP_sto_init[genco])                     for genco in HeatPump_units for year in years)

    boiler_max_prod_dh = ucp.addConstrs(boiler_dh_generation[boiler,chp_market,period,year] <=
                                        HP_Boiler_dh_init_cap[boiler, chp_market]*Thermal_Decom_DH[year]  + trad_boiler_dh_cap_inv_cumulative [boiler, chp_market, year] + 
                                        trad_boiler_dh_cap_inv[boiler,chp_market,year]*round(((1-int(Boiler_decom_rate[boiler]))**(max(Boiler_decom_start_new[boiler]-1,0)-(Boiler_decom_start_new[boiler]-1))),3) 
                                        for boiler in thermal_units for chp_market in chp_units for period in periods for year in years) #equation updated

    boiler_max_prod_ind = ucp.addConstrs(boiler_ind_generation[boiler,period,year] <= 
                                        Thermal_ind_init_cap[boiler]*Thermal_Decom_ind[year]  + trad_boiler_ind_cap_inv_cumulative [boiler, year] + 
                                        trad_boiler_ind_cap_inv[boiler,year] *round(((1- int(Boiler_decom_rate[boiler]))**(max(Boiler_decom_start_new[boiler]-1, 0)-( Boiler_decom_start_new[boiler]-1))),3) 
                                        for boiler in thermal_units for period in periods for year in years) #equation updated
                                          
    max_boiler_cap_inv  = ucp.addConstrs(trad_boiler_ind_cap_inv[boiler,year]  + 
                                         gp.quicksum(trad_boiler_dh_cap_inv[boiler,chp_market,year] for chp_market in chp_units)
                                         <= Thermal_max_cap[boiler]/ len(hor_yrs)     for boiler in thermal_units for year in years) #equation updated

    max_HP_cap_inv  = ucp.addConstrs(heat_pump_ind_cap[hpUnit,year]  + gp.quicksum(heat_pump_dh_cap[hpUnit,chp_market,year] for chp_market in chp_units)
                                          <= HP_max_cap[hpUnit]/ len(hor_yrs)    for hpUnit in HeatPump_units for year in years) #equation updated

            
################################################################################################################################################################
#################################################### CHP generation - storage constraints#######################################################################
################################################################################################################################################################

max_heat_production     = ucp.addConstrs((heat_input[genco, period, year]*CHPPowerToHeat[genco] <= generation[genco, period, year])  for genco in chp_units for period in periods for year in years)

gen_heat_extraction_max = ucp.addConstrs((generation[genco, period, year] <= 
                                          max_load[genco]*cap_factor[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) +  
                                          cap_inv_cumulative[genco, year] *cap_factor[genco] + cap_inv[genco, year] *cap_factor[genco] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3) - heat_input[genco, period, year]*CHPPowerLossFactor[genco])  
                                         for genco in chp_units for period in periods for year in years) #equation updated


heat_storage_level0     = ucp.addConstrs((heat_sto_level[genco, 1, year] == 0.5*STOCapacity_heat[genco] +
                                       heat_input[genco, 1, year] - heat_gen[genco, 1, year])                                        for genco in chp_units for year in years) 

heat_storage_level      = ucp.addConstrs((heat_sto_level[genco, period, year] == heat_sto_level[genco, period-1, year] +
                                      heat_input[genco, period, year] +
                                      gp.quicksum(heat_pump_dh[HPUnit,genco,period, year] for HPUnit in HeatPump_units)+
                                      gp.quicksum(boiler_dh_generation[boiler,genco,period,year] for boiler in thermal_units) -
                                      heat_gen[genco, period, year])                                                                 for genco in chp_units for period in periods[1:] for year in years)

max_heat_sto_level      = ucp.addConstrs((heat_sto_level[genco, period, year] <= STOCapacity_heat[genco])                            for genco in chp_units for period in periods for year in years)

max_heat_gen            = ucp.addConstrs((heat_input[genco, period, year] <= CHPMaxHeat[genco])                                        for genco in chp_units for period in periods for year in years)


################################################################################################################################################################
##################################################Meet Cooling and Heat demand #################################################################################
################################################################################################################################################################
meet_heat_demand        = ucp.addConstrs((heat_gen[genco, period, year] + noload_heat_dh[genco, period, year]== heat_demand[genco,period, year])
                                         for genco in chp_units for period in periods for year in years)

meet_heat_demand_gen    = ucp.addConstrs((gp.quicksum(heat_pump_out[genco,period, year] for genco in HeatPump_units) +
                                          gp.quicksum(boiler_ind_generation[boiler,period,year] for boiler in thermal_units) +
                                          noload_heat_ind[period, year] 
                                          ==
                                          heat_demand["general_demand",period, year] )
                                         for period in periods for year in years)

meet_cooling_demand_gen = ucp.addConstrs((gp.quicksum(cooling_pump_ind[genco,period, year] for genco in HeatPump_units) ==
                                          cooling_demand["general_cooling",period, year])
                                         for period in periods for year in years) 

meet_cooling_demand_dh = ucp.addConstrs((gp.quicksum(cooling_pump_dh[genco,chp_market,period, year] for genco in HeatPump_units) ==
                                          cooling_demand[chp_market,period, year])
                                         for period in periods for year in years for chp_market in chp_units) 



################################################################################################################################################################
##################################################Storage Hydro Dam constraints#################################################################################
################################################################################################################################################################
if hydro_storage:
    
    storage_level_min    = ucp.addConstrs((storage_level[genco, period, year] >= 0.5*hdam_max_storage[genco])             for genco in hdam_units for period in periods for year in years) 
    storage_level_max    = ucp.addConstrs((storage_level[genco, period, year] <= hdam_max_storage[genco])                 for genco in hdam_units for period in periods for year in years) 
    storage_level_end    = ucp.addConstrs((storage_level[genco, len(periods), year] >= 0.5*hdam_max_storage[genco])       for genco in hdam_units for year in years)
    
    storage_level_cap_0  = ucp.addConstrs((storage_level[genco, 1, year]      == 0.5*hdam_max_storage[genco] +
                                           hdam_inflow[genco, 1, year]*max_load[genco]*hdam_efficiency[genco] -
                                           generation[genco, 1, year]/hdam_efficiency[genco]- 
                                           sto_out_flow[genco, 1, year])                                                  for genco in hdam_units for year in years) 
    
    storage_level_balance= ucp.addConstrs((storage_level[genco, period, year] == storage_level[genco, period-1, year] +
                                           hdam_inflow[genco, period, year]*max_load[genco]*hdam_efficiency[genco] -
                                           generation[genco, period, year]/hdam_efficiency[genco] - 
                                           sto_out_flow[genco, period, year])                                             for genco in hdam_units for period in periods[1:] for year in years) 
    
    hdam_storage_gen_cap = ucp.addConstrs((generation[genco, period, year]/hdam_efficiency[genco] +
                                           sto_out_flow[genco, period, year] <= 
                                           storage_level[genco, period, year] +
                                           hdam_inflow[genco, period, year]*max_load[genco]*hdam_efficiency[genco])       for genco in hdam_units for period in periods for year in years)

################################################################################################################################################################         
#################################################Storage Hydro HPHS constraints################################################################################# 
################################################################################################################################################################
if hydro_storage:
    
    hphs_storage_level_min    = ucp.addConstrs((storage_level[genco, period, year] >= 0.5*hphs_max_storage[genco])        for genco in hphs_units for period in periods for year in years)
    hphs_storage_level_max    = ucp.addConstrs((storage_level[genco, period, year] <= hphs_max_storage[genco])            for genco in hphs_units for period in periods for year in years)
    hphs_storage_level_end    = ucp.addConstrs((storage_level[genco, len(periods), year] >= 0.5*hphs_max_storage[genco])  for genco in hphs_units for year in years)    

    hphs_storage_level_cap_0  = ucp.addConstrs((storage_level[genco, 1, year]      == 0.5*hphs_max_storage[genco] +
                                                hphs_inflow[genco, 1, year]*max_load[genco]*hphs_efficiency[genco] -
                                                generation[genco, 1, year]/hphs_efficiency[genco]-
                                                sto_out_flow[genco, 1, year])                                             for genco in hphs_units for year in years)
    
    hphs_storage_level_period = ucp.addConstrs((storage_level[genco, period, year] == storage_level[genco, period-1, year] +
                                                hphs_inflow[genco, period, year]*max_load[genco]*hphs_efficiency[genco] -
                                                generation[genco, period, year]/hphs_efficiency[genco]-
                                                sto_out_flow[genco, period, year])                                        for genco in hphs_units for period in periods[1:] for year in years)
    
    hphs_storage_gen_cap      = ucp.addConstrs((generation[genco, period, year]/hphs_efficiency[genco] +
                                                sto_out_flow[genco, period, year] <= 
                                                storage_level[genco, period, year] +
                                                hphs_inflow[genco, period, year]*max_load[genco]*hphs_efficiency[genco])  for genco in hphs_units for period in periods for year in years)

################################################################################################################################################################    
#Rump up constraints
rump_up   = ucp.addConstrs(generation[genco, period, year] <= generation[genco, period-1, year] + ramp_up_rate[genco]*(max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) +  cap_inv_cumulative[genco, year] + 
                                                                                                                       cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3)) for genco in disp_units for period in periods[1:] for year in years) #equation updated
rump_down = ucp.addConstrs(generation[genco, period, year] >= generation[genco, period-1, year] - ramp_down_rate[genco]*(max_load[genco]*round(((1-decom_rate[genco])**(max(decom_start_old[genco]-1 ,year- hor_yrs[0])-(decom_start_old[genco]-1))),3) + cap_inv_cumulative[genco, year] + 
                                                                                                                         cap_inv[genco, year] * round(((1 - decom_rate[genco]) ** (max(decom_start_new[genco] - 1, 0) - (decom_start_new[genco] - 1))), 3))  for genco in disp_units for period in periods[1:] for year in years) #equation updated

import_lim= ucp.addConstrs(imports[period, year] <= import_ntc[period]                 for period in periods for year in years)


 
if rps_inv:
    
    rps_lim       = ucp.addConstrs((gp.quicksum(generation[genco, period, year] for genco in nondisp_units for period in periods) +
                               gp.quicksum(generation[genco, period, year] for genco in biomass_units for period in periods)+
                               gp.quicksum(generation[genco, period, year] for genco in hydro_units for period in periods)
                               >=
                               rps[year]*(gp.quicksum(generation[genco, period, year] for genco in fossil_units for period in periods) + 
                                  gp.quicksum(generation[genco, period, year] for genco in hphs_units for period in periods)   + 
                                  gp.quicksum(generation[genco, period, year] for genco in hdam_units for period in periods)   + 
                                  gp.quicksum(generation[genco, period, year] for genco in hror_units for period in periods)   + 
                                  gp.quicksum(generation[genco, period, year] for genco in nondisp_units for period in periods)+
                                  gp.quicksum(noload_elec[period, year] for period in periods)+
                                  gp.quicksum(imports[period, year] for period in periods)))  for year in years)
                          
if not res_inv:
    non_inv  = ucp.addConstrs((gp.quicksum(cap_inv[genco, year]  for genco in nondisp_units) + gp.quicksum(cap_inv_FC[genco, year] for genco in FC_units) == 0) for year in years)
    
if carbonLimit:
    
    CO2_emissions_limit = ucp.addConstrs( CO2_total[year] + CO2_total_heat[year] + CO2_total_industry[year]   <= CO2_limit[year] for year in years)
    
CO2_emissions_power = ucp.addConstrs(gp.quicksum(CO2_factor[genco]*generation[genco,period, year] for genco in fossil_units for period in periods) ==
                                         CO2_total[year]                                                                                                        
                                         for year in years)


CO2_emissions_heat = ucp.addConstrs(gp.quicksum(Thermal_CO2[genco]*boiler_ind_generation[genco,period, year] for genco in thermal_units for period in periods) +
                                         gp.quicksum(Thermal_CO2[genco]*boiler_dh_generation[genco,chp_market,period,year] 
                                                     for genco in thermal_units for chp_market in chp_units for period in periods) 
                                         ==
                                         CO2_total_heat[year]                                                                                                   
                                         for year in years)


CO2_emissions_industry = ucp.addConstrs(gp.quicksum(Fuel_CO2[fuel]*Fuel_ind[fuel, period, year] for fuel in industry_fuels for period in periods) ==
                                        CO2_total_industry[year]                                                                                                        
                                        for year in years)



####################################################################################################################################
#Rump cost constraint
ramp_up_cost = ucp.addConstrs((rampup_cost[genco, period, year] >=
                                     ramp_cost[genco]*(generation[genco, period, year]-generation[genco, period-1, year]))  for genco in disp_units for period in periods[1:] for year in years)

ramp_down_cost = ucp.addConstrs((rampdown_cost[genco, period, year] >= 
                                     ramp_cost[genco]*(-generation[genco, period, year]+generation[genco, period-1, year])) for genco in disp_units for period in periods[1:] for year in years)
#####################################################################################################################################
# Objective: minimize total cost
Fossil_hydro       =  list(dict.fromkeys(disp_units + hydro_units))
per_mw             = gp.quicksum(NPV[year]*var_cost[genco,period, year]*generation[genco, period, year]              for genco in units for period in periods for year in years)
FUTURE_SIGNED_TRADE = True
import_cost        = gp.quicksum(NPV[year]*imports[period, year]*Import_Price*(1+Imp_price_inc/100)**(year-hor_yrs[0]) for period in periods for year in years)
ramp_up_cost_var   = gp.quicksum(NPV[year]*rampup_cost[genco, period, year]                                          for genco in disp_units for period in periods[1:] for year in years)
ramp_down_cost_var = gp.quicksum(NPV[year]*rampdown_cost[genco, period, year]                                        for genco in disp_units for period in periods[1:] for year in years)
inv_cost           = gp.quicksum(NPV[year]*cap_inv[genco, year]*cap_inv_cost[genco]*TechChange[genco,year]           for genco in nondisp_units for year in years)
inv_cost_fossil    = gp.quicksum(NPV[year]*cap_inv[genco, year]*cap_inv_cost[genco]                                  for genco in Fossil_hydro for year in years)

inv_cost_FuelCell  = gp.quicksum(NPV[year]*cap_inv_FC[genco, year]*FC_cap_costs_years[genco, year]                   for genco in FC_units for year in years)
per_mw_FC          = gp.quicksum(NPV[year]*FC_var_cost[genco]*generation_FC[genco, period, year]                     for genco in FC_units for period in periods for year in years)

heat_pump_cap_cost = (gp.quicksum(NPV[year]*heat_pump_ind_cap[genco, year]*HP_cap_costs_years[genco, year]           for genco in HeatPump_units for year in years) + #assuming a cost of 1500 USD per Kw
                      gp.quicksum(NPV[year]*heat_pump_dh_cap[genco,chp_market, year]*HP_cap_costs_years[genco, year] for genco in HeatPump_units for chp_market in chp_units for year in years))

ResToHeat_cost     = (gp.quicksum(NPV[year]*heat_pump_ind[genco,period, year]*HP_var_cost[genco]                     for genco in HeatPump_units for period in periods for year in years) +
                      gp.quicksum(NPV[year]*heat_pump_dh[genco,chp_market,period, year]*HP_var_cost[genco]           for genco in HeatPump_units for chp_market in chp_units for period in periods for year in years))

boiler_gen_cost    = (gp.quicksum(NPV[year]*boiler_ind_generation[genco,period, year]*heat_var_cost[genco,period, year]          for genco in thermal_units for period in periods for year in years) +
                     gp.quicksum(NPV[year]*boiler_dh_generation[genco,chp_market,period,year]*heat_var_cost[genco,period, year]  for genco in thermal_units for chp_market in chp_units for period in periods for year in years))
inv_cost_boiler    = (gp.quicksum(NPV[year]*trad_boiler_ind_cap_inv[genco, year]*boiler_cap_costs_years[genco,year]              for genco in thermal_units for year in years)+
                     gp.quicksum(NPV[year]*trad_boiler_dh_cap_inv[genco,chp_market,year]*boiler_cap_costs_years[genco,year]      for genco in thermal_units for chp_market in chp_units for year in years))

industry_fuel_cost = gp.quicksum(NPV[year]*Fuel_ind[fuel, period, year]*industry_fuels_price[period,year,fuel]                for period in periods for year in years for fuel in industry_fuels) 
CO2_cost_industry  = gp.quicksum(NPV[year]*CO2_price[year]*Fuel_CO2[fuel]*Fuel_ind[fuel, period, year]                        for period in periods for year in years for fuel in industry_fuels)

h2_storage_cost    = gp.quicksum(NPV[year]*h2_sto_max_cap[genco,year]*H2sto_costs_years[genco, year]                for genco in H2_storage_unit for year in years) 
h2_CAPEX_cost      = gp.quicksum(NPV[year]*h2_electrolysis_max_cap[genco,year]*Elec_cap_costs_years[genco, year]    for genco in Elec_h2_units for year in years) 
h2_OPEX_cost       = gp.quicksum(NPV[year]*generation_H2[genco,period,year]*H2_var_cost[genco]                      for genco in Elec_h2_units for period in periods for year in years) 

evToPowert_cost    = gp.quicksum(NPV[year]*ev_sto_out[period, year]*V2G_cost                                        for period in periods for year in years) 
statToPowert_cost  = gp.quicksum(NPV[year]*(stat_sto_out[unit,period, year]+stat_sto_in[unit,period, year])*Sta_sto_var_cost[unit]    for unit in Stat_storage_unit for period in periods for year in years) 
statCapInvCost     =gp.quicksum(NPV[year]*stat_sto_cap_inv[unit, year]*battery_costs_years[unit,year]               for unit in Stat_storage_unit for year in years)           

CO2_emissions_cost = gp.quicksum(NPV[year]*CO2_price[year]*CO2_factor[genco]*generation[genco,period, year]            for genco in fossil_units for period in periods for year in years)
spill_cost         = gp.quicksum(NPV[year]*sto_out_flow[genco,period, year]*450                                     for genco in hydro_storage_units for period in periods for year in years) 
ceep_cost          = gp.quicksum(NPV[year]*ceep[period, year]*ceep_value                                                   for period in periods for year in years)   

no_load_cost       = gp.quicksum(NPV[year]*(noload_elec[period, year]+noload_heat_ind[period, year])*LostLoad_Cost*(1+Imp_price_inc/100)**(year-hor_yrs[0])    for period in periods for year in years)   

no_load_cost_dh    =  gp.quicksum(NPV[year]*noload_heat_dh[chp_market,period, year]*LostLoad_Cost*(1+Imp_price_inc/100)**(year-hor_yrs[0])  for chp_market in chp_units for period in periods for year in years)

extra_investment_cost = gp.quicksum(NPV[year] * extra_cap_inv[genco, year] * 200 for genco in units for year in years)

if ceep_penalty == False:   
    ucp.setObjective(    (per_mw 
                          + import_cost 
                          + inv_cost
                          + inv_cost_fossil
                          + ramp_up_cost_var 
                          + ramp_down_cost_var
                          + heat_pump_cap_cost
                          + ResToHeat_cost
                          + boiler_gen_cost
                          + inv_cost_boiler
                          + CO2_emissions_cost
                          + evToPowert_cost
                          + statToPowert_cost
                          + statCapInvCost
                          + h2_storage_cost
                          + h2_CAPEX_cost
                          + h2_OPEX_cost 
                          + inv_cost_FuelCell 
                          + per_mw_FC
                          + industry_fuel_cost
                          + no_load_cost
                          + no_load_cost_dh
                          + spill_cost
                          + extra_investment_cost
                          )/1,GRB.MINIMIZE)
    
if ceep_penalty == True:   
    ucp.setObjective(    (per_mw 
                          + import_cost 
                          + inv_cost
                          + inv_cost_fossil
                          + ramp_up_cost_var 
                          + ramp_down_cost_var
                          + heat_pump_cap_cost
                          + ResToHeat_cost
                          + boiler_gen_cost
                          + inv_cost_boiler
                          + CO2_emissions_cost
                          + evToPowert_cost
                          + statToPowert_cost
                          + statCapInvCost
                          + h2_storage_cost
                          + h2_CAPEX_cost
                          + h2_OPEX_cost 
                          + inv_cost_FuelCell 
                          + per_mw_FC  
                          + industry_fuel_cost
                          + ceep_cost 
                          + no_load_cost
                          + no_load_cost_dh
                          + spill_cost
                          + extra_investment_cost
                          )/1,GRB.MINIMIZE)

if "fixed_capacity_lookup" in globals() and fixed_capacity_lookup:
    fixed_capacity_constraints = []

    def _fixed_capacity_value(variable, unit, year, unit_2=""):
        return fixed_capacity_lookup.get((variable, str(unit), str(unit_2 or ""), int(year)))

    for unit in units:
        for year in years:
            value = _fixed_capacity_value("cap_inv", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(cap_inv[unit, year] == value))

    for unit in FC_units:
        for year in years:
            value = _fixed_capacity_value("cap_inv_FC", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(cap_inv_FC[unit, year] == value))

    for unit in HeatPump_units:
        for year in years:
            value = _fixed_capacity_value("heat_pump_ind_cap", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(heat_pump_ind_cap[unit, year] == value))
            for chp in chp_units:
                value = _fixed_capacity_value("heat_pump_dh_cap", unit, year, chp)
                if value is not None:
                    fixed_capacity_constraints.append(ucp.addConstr(heat_pump_dh_cap[unit, chp, year] == value))

    for unit in thermal_units:
        for year in years:
            value = _fixed_capacity_value("trad_boiler_ind_cap_inv", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(trad_boiler_ind_cap_inv[unit, year] == value))
            for chp in chp_units:
                value = _fixed_capacity_value("trad_boiler_dh_cap_inv", unit, year, chp)
                if value is not None:
                    fixed_capacity_constraints.append(ucp.addConstr(trad_boiler_dh_cap_inv[unit, chp, year] == value))

    for unit in Stat_storage_unit:
        for year in years:
            value = _fixed_capacity_value("stat_sto_cap_inv", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(stat_sto_cap_inv[unit, year] == value))

    for unit in H2_storage_unit:
        for year in years:
            value = _fixed_capacity_value("h2_sto_max_cap", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(h2_sto_max_cap[unit, year] == value))

    for unit in Elec_h2_units:
        for year in years:
            value = _fixed_capacity_value("h2_electrolysis_max_cap", unit, year)
            if value is not None:
                fixed_capacity_constraints.append(ucp.addConstr(h2_electrolysis_max_cap[unit, year] == value))

    print(f"[CAPACITY_TRANSFER] Fixed {len(fixed_capacity_constraints)} capacity investment variables before solve.")

#ucp.Params.Method  = 3 # Concurrent: Several methods and chooses the one that finishes faster (higher memory usage) 
ucp.Params.Method    = 2 # Barrier: Faster but possible approximate results (less than 1% deviation from optimal solution)  
ucp.Params.Crossover = 0  
ucp.Params.ScaleFlag = 3
ucp.Params.presolve  = 2   
ucp.optimize() 
#print('*****************************')
#print('*******MODEL SOLVED**********')
#print('*****************************')

if ucp.Status == 2:
    print('******************************************************')
    print('*******MODEL SOLVED - Optimal solution found**********')
    print('******************************************************')
    
if ucp.Status == 13 :
    print('**********************************************')
    print('*******MODEL SOLVED - solution found**********')
    print('**********************************************')
    
if ucp.Status == 3:
    print('******************************************************')
    print('*******MODEL Is INFEASIBLE - Check input data*********')
    print('******************************************************')
    sys.exit()
    
if ucp.Status == 12 :
    print('*****************************************************************')
    print('*******MODEL with numeric issues - Try scaling input data********')
    print('*****************************************************************')
    sys.exit()
    
if ucp.Status != 13 and ucp.Status != 2 and ucp.Status != 3:
    print('*********************************************************************')
    print('*******MODEL infeasible, unbounded, or numeric issues found**********')
    print('*********************************************************************')
    sys.exit()

