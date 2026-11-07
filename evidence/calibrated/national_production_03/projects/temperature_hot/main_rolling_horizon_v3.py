# -*- coding: utf-8 -*-
"""
Created on Wed Oct 23 22:00:28 2024

@author: abhi
"""
##1176 line, 1732
import os

import gurobipy as gp


def _prefer_existing_path(*paths):
    """Return the first existing path, otherwise keep the primary path."""
    for path in paths:
        if os.path.exists(path):
            return path
    return paths[0]

#MAIN
###############################################################################
##################SENARIO NAME and single parameters#######################
###############################################################################

# SCENARIO DESIGN
scen                                = 'model_Test_3_Myopic'
rps_inv                             = False    # Temporary H2-off debug run: skip RPS policy constraints while testing feasibility.
carbonLimit                         = False   # Temporary H2-off run: 2040+ CO2 caps require an H2/zero-carbon industry route.
res_inv                             = True     # Options: True / False
ceep_limit                          = True     # Options: True / False
ceep_penalty                        = True     # Options: True / False
fossil_dispatch                     = True     # Options: True / False
hydro_storage                       = True     # Options: True / False
NoResToHeatInv                      = False    # Options: True / False
PrimaryReserve                      = False    # Options: True / False
SecondaryReserve                    = False    # Options: True / False
save_csv                            = False    # Solver-only debug run; avoids slow/incomplete export while testing feasibility.
Rolling_Horizon                     = True     # Options: True / False
 




#### Time horizon Paramenters ####
periods                 = list(range(1, 8761))  # Full year (8760 hours)
years                   = list([2020, 2025, 2030, 2035, 2040, 2045, 2050])
window                  = 2 #enter a number between 1 and len(years)

# Policy Paramenters
rps                     = list([0.5, 0.6, 0.6, 0.7, 0.8, 0.9, 0.9])  # Temporary H2-off run: avoid 100% RPS numeric instability in 2050.
CO2_limit               = list([8261526*1,8261526*0.8,8261526*0.6,8261526*0.4,8261526*0.3,8261526*0.2,8261526*0.1])
carbon_price            = list([20, 40, 60, 70, 80, 90, 100])        # Carbon price in $/tCO2
ceep_parameter          = 0.1    # Note that H2RES takes 0.1 = 10% not as 0.1%.
ceep_value              = 100    # penalty for CEEP
LostLoad_Cost           = 1000000   # Cost for Unserved demand (heat (dh, ind) and electricity) 

# General Paramenters
NPV                     = list([1,0.78,0.61,0.48,0.38,0.3,0.23])   #IR 5%
TechChangeSolar         = list([1, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5])
TechChangeWind          = list([1, 0.95, 0.9, 0.8, 0.7, 0.6, 0.5])
ThermalDecomInd         = list([1, 0.6, 0.3, 0, 0, 0, 0])
HeatPumpDecomInd        = list([1, 0.6, 0.3, 0, 0, 0, 0])
ThermalDecomDH          = list([1, 0.6, 0.3, 0, 0, 0, 0])
StaStoDecomInd          = list([1, 0.6, 0.3, 0, 0, 0, 0])
Import_Price            = 45       # $/MWh
Imp_price_inc           = 0.05     # 5% increase in import price pear year. 


#### V2G
V2G_cost                = 25       # Vehicle to grid price $/MWh 
ev_Demand_year          = list([1, 1, 1, 1, 1, 1, 1])  # Total yearly demand per year (MWh)
ev_sto_min              = 0
ev_Grid_eff             = 0.9
number_of_veh           = 1670000   #Number of total vehicles
average_ch_rate         = 7         #kW
average_bat             = 50        #kWh
charging_veh            = number_of_veh*average_ch_rate/1000    #MW
stor_veh                = number_of_veh*average_bat/1000        #MWh
ev_Grid_P               = list([0.075*charging_veh, 0.2*charging_veh, 0.4*charging_veh, 0.4*charging_veh, 0.4*charging_veh, 0.4*charging_veh, 0.4*charging_veh])
ev_stor                 = list([0.122*stor_veh, 0.143*stor_veh, 0.163*stor_veh,0.184*stor_veh,0.204*stor_veh,0.225*stor_veh,0.25*stor_veh])

###############################################################################
### GENCO/Demand data (enter genco data file name with extension)##############
###############################################################################
genco_dat               = './data/SC/genco_data_HR_sdewes.csv'
demand_dat              = './data/SC/demand_2020_2050_sdewes.csv'
fuel_price_dat          = './data/SC/fuel_cost_2020_2050_sdewes.csv'
avl_factor_plant_dat    = './data/SC/ncre_aval_factor_HR_2020_2050_sdewes.csv'
inflows_dat             = './data/SC/scaled_inflows_HR_2020_2050_sdewes.csv' 
import_export           = './data/SC/import_export_HR_2020_2050_sdewes.csv' 
heat_demand_dat         = _prefer_existing_path(
    './data/SC/heat_demand_HR_2020_2050_sdewes_NONE.csv',
    './data/SC/heat_demand_HR_2020_2050_sdewes.csv',
)
cooling_demand_dat      = _prefer_existing_path(
    './data/SC/cooling_demand_HR_2020_2050_sdewes_NONE.csv',
    './data/SC/cooling_demand_HR_2020_2050_sdewes.csv',
)
ev_transpload_dat       = _prefer_existing_path(
    './data/SC/ev_transp_load_NONE.csv',
    './data/SC/ev_transp_load.csv',
)
h2_demand_dat           = _prefer_existing_path(
    './data/SC/demand_H2_2020_2050_sdewes_NONE.csv',
    './data/SC/demand_H2_2020_2050_sdewes.csv',
)
flex_tech_dat           = './data/SC/flex_tech_HR_2020_2050_sdewes_limited.xlsx'



###############################################################################
### GENCO/Demand data (enter genco data file name with extension)##############
###############################################################################
print('Reading and processing data')
exec(open("scripts/read_process_data.py").read())
exec(open("scripts/modeling_approaches.py").read())
