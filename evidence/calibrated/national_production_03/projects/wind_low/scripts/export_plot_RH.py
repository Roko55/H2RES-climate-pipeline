# -*- coding: utf-8 -*-
"""
Created on Fri Jan  8 20:13:08 2021

@author: felipe
"""
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import shutil
#%% Directory to save results

print('Exporting results--> ...')

if os.path.isdir('./results/'+scen):
    shutil.rmtree('./results/'+scen)
    os.makedirs('./results/'+scen)
else: os.makedirs('./results/'+scen)
#%% Export data and see results
generation_list = list()
imports_list = list()
No_load_elec= list()
for year in years:
    for genco, fuel in unit_fuel:
        for period in periods:
            fuel_name = fuel
            generation_list.append([genco, period, year,fuel_name,round(generation_results[genco,period,year],2)])

for year in years:
    for period in periods:
        imports_list.append(['imports', period,year,'imports',round(imports_results[period,year],2)])
        No_load_elec.append(['shed_load', period,year,'shed_load',round(noload_elec_results[period,year],2)])

            
 
results_import = pd.DataFrame(imports_list, columns = ['Unit', 'period', 'year', 'Fuel' ,'Dispatch'])
results_gen = pd.DataFrame(generation_list, columns = ['Unit', 'period', 'year', 'Fuel' ,'Dispatch'])
results_noload = pd.DataFrame(No_load_elec, columns = ['Unit', 'period', 'year', 'Fuel' ,'Dispatch'])

if results_noload.Dispatch.sum() >=1:
    plt.figure(figsize=(12,6))
    results_noload.Dispatch.plot()
    plt.ylabel('MW')
    plt.xlabel('Hour')
    plt.title(f'Unserved Demand Electricity (GWh): {round(results_noload.Dispatch.sum()/1000,2)}')
    plt.savefig('./results/'+scen+'/shed_load.png', bbox_inches='tight')
    plt.show() 
    
frames = [results_gen,results_import,results_noload]
result = pd.concat(frames)
results_gen=result


results_noload_year = results_noload.drop(['Fuel', 'Unit'], axis =1)

#%%
gen_tmp = results_gen[['Fuel','period','year','Dispatch']]
gen_fuel = gen_tmp.groupby(['year','period','Fuel']).sum()
gen_by_fuel = pd.DataFrame(gen_fuel)
gen_by_fuel=gen_by_fuel.reset_index()

gen_by_fuel['Dispatch']=gen_by_fuel['Dispatch']
gen = gen_by_fuel.pivot_table(index = ['year','period'], columns = 'Fuel', values = 'Dispatch')
if save_csv: gen.to_excel('./results/'+scen+'/gen_by_fuel_wide_GWh.xlsx', sheet_name='generation_wide', index=True) 

#%%
#Plots
col_name = list(gen.columns)
gen = gen[col_name]

#%%
inv_add = list()
for year in years:
    for inv in units:
        inv_add.append([year, inv,round(cap_inv_results[inv, year],2)])
    
inv_add_df = pd.DataFrame(inv_add, columns = ['Year','Unit', 'Investment'])
inv_add_df = pd.pivot_table(inv_add_df, values='Investment', index=['Unit'],columns=['Year'], aggfunc=np.sum)
plt.figure()
inv_add_df.plot(kind='bar',stacked =True)
plt.ylabel('Additional capacity (MW)')
plt.title('Total Capacity Investment')
plt.savefig('./results/'+scen+'/cap_inv.png', bbox_inches='tight')
plt.show() 



#%%

# sort_data = merged.sort_values(by = 'var_cost')
# sort_data=sort_data[(sort_data['period']==1)|(sort_data['period']==100)]
# sort_data['Cum_cap']=sort_data['cap_mw'].cumsum()
# groups = sort_data.groupby("fuel_type")
#%%
storage_total = list()
for year in years:
    for hdam in hydro_storage_units:
        for period in periods:
            storage_total.append([hdam,period,year,storage_level_results[hdam,period,year]])
storage_total_df = pd.DataFrame(storage_total, columns = ["unit", 'period','year', 'storage'])
storage_total_df = storage_total_df.pivot_table(index = ['year','period'], columns = 'unit', values = 'storage')
if save_csv: storage_total_df.to_excel('./results/'+scen+'/StorageLevel_Hydro.xlsx', sheet_name='HydroStorageLevel', index=True) 

#%%
spillage_total = list()
for hdam in hdam_units:
    for period in periods:
        for year in years:
            spillage_total.append([hdam,period,year,sto_out_flow_results[hdam,period,year]])
for hdam in hphs_units:
    for period in periods:
        for year in years:
            spillage_total.append([hdam,period,year,sto_out_flow_results[hdam,period,year]])
spillage_total_df = pd.DataFrame(spillage_total, columns = ["unit", 'period','year', 'storage'])
spillage_total_df = spillage_total_df.pivot_table(index = ['year','period'], columns = 'unit', values = 'storage')
if save_csv: spillage_total_df.to_excel('./results/'+scen+'/spillage_hydro_storage.xlsx', sheet_name='Spill_HydroStorage', index=True) 

#%%

fuel_use_ind = list()
for fuel in industry_fuels:
    for period in periods:
        for year in years:
            fuel_use_ind.append([period,year,fuel, round(Fuel_ind_results[fuel, period, year],2)])
            
fuel_use_ind_df = pd.DataFrame(fuel_use_ind, columns = [ 'period','year', 'fuel' ,'Fuel_use'])
fuel_use_ind_df = fuel_use_ind_df.pivot(index = ['year', 'period'], columns = 'fuel', values = 'Fuel_use')


H2_use_ind = list()
for period in periods:
    for year in years:
        H2_use_ind.append([period,year, round(H2tInd_results[period, year],2)])

H2_use_ind_df = pd.DataFrame(H2_use_ind, columns = [ 'period','year', 'H2_ind']).set_index([ 'period','year'])
fuel_use_ind_df = fuel_use_ind_df.merge(H2_use_ind_df,left_index=(True), right_index=(True))
fuel_use_ind_df_year = fuel_use_ind_df.reset_index().drop(['period'],axis = 1).groupby('year').sum()

#%% RES TO POWER - HEAT - EV BATTERY
ResToHeat_l  = list()
ResToEV_l    = list()
ResToStat_Sto_l    = list()
ResToStat_Sto_out_l = list()
Heat_res_gen_ind = list()
Heat_res_gen_dh = list()

Heat_hp_ind =list()
Heat_hp_dh =list()  
Heat_noload_ind = list()     
Heat_noload_dh = list()     

for year in years:
    for period in periods:
        Heat_noload_ind.append([period,year, round(noload_heat_ind_results[period, year],2)])
        for unit in HeatPump_units:
            Heat_hp_ind.append([period,year,unit, round(heat_pump_ind_results[unit,period, year],2)])
            for chp_market in chp_units:
                Heat_hp_dh.append([period,year,chp_market,unit, round(heat_pump_dh_results[unit,chp_market,period, year],2)])  
                Heat_noload_dh.append([period,year,chp_market, round(noload_heat_dh_results[chp_market,period, year],2)])     

#Heat_noload_dh = [x for i, x in enumerate(Heat_noload_dh) if x not in Heat_noload_dh[:i]]



Heat_noload_dh_df  = pd.DataFrame(Heat_noload_dh, columns = [ 'period','year', 'DH Market' ,'NoLoad']).drop_duplicates()
Heat_noload_ind_df = pd.DataFrame(Heat_noload_ind, columns = [ 'period','year', 'NoLoad']).set_index(['year','period'])

Heat_no_load = Heat_noload_dh_df.pivot_table(index = ['year', 'period'], columns = 'DH Market', values = 'NoLoad')
Heat_noload_ind_df=Heat_noload_ind_df[~Heat_noload_ind_df.index.duplicated()]
Heat_no_load['Individual'] = Heat_noload_ind_df.NoLoad
No_load_year = Heat_no_load.reset_index().drop(['period'],axis = 1).groupby('year').sum()

No_load_year['Electricity'] = results_noload_year.groupby('year').sum().drop('period', axis =1)


Heat_hp_ind_df = pd.DataFrame(Heat_hp_ind, columns = [ 'period','year', 'technology' ,'ResToHeat'])
Heat_hp_ind_df = Heat_hp_ind_df.set_index(['year','period'])
Heat_hp_ind_df = Heat_hp_ind_df.pivot_table(index = ['year', 'period'], columns = 'technology', values = 'ResToHeat')

Heat_hp_dh_df  = pd.DataFrame(Heat_hp_dh, columns = [ 'period','year','DH Market', 'technology' ,'ResToHeat'])
Heat_hp_dh_df = Heat_hp_dh_df.drop('DH Market', axis = 1).set_index(['year','period'])
Heat_hp_dh_df = Heat_hp_dh_df.reset_index().groupby(['period','year','technology']).sum().reset_index().pivot_table(index = ['year', 'period'], columns = 'technology', values = 'ResToHeat')
Heat_hp_dh_df_year = Heat_hp_dh_df.reset_index().drop(['period'],axis = 1).groupby('year').sum()

total_res_to_heat = 0
for year in years:
    for period in periods:
        ResToHeat_l.append([period,year, round(ResToHeat_results[period,year],2)])
        ResToEV_l.append([period,year, round(ev_sto_in_results[period,year],2)])
        for unit in Stat_storage_unit:
            ResToStat_Sto_l.append([period,year,unit, round(stat_sto_in_results[unit,period, year],2)])
            ResToStat_Sto_out_l.append([period,year,unit, round(stat_sto_out_results[unit,period, year],2)])


ResToHeat_df = pd.DataFrame(ResToHeat_l, columns = [ 'period','year', 'ResToHeat'])
ResToHeat_df = ResToHeat_df.set_index(['year','period'])
ResToEV_df = pd.DataFrame(ResToEV_l, columns = ['period','year', 'ResToEV'])
ResToEV_df = ResToEV_df.set_index(['year','period'])
ResToStat_Sto_df = pd.DataFrame(ResToStat_Sto_l, columns = ['period','year','unit', 'ResToStaSTO'])
ResToStat_Sto_df = ResToStat_Sto_df.groupby(['year','period']).sum()
ResToStat_Sto_out_df = pd.DataFrame(ResToStat_Sto_out_l, columns = ['period','year','unit', 'StaSTO_Out'])
ResToStat_Sto_out_df = ResToStat_Sto_out_df.groupby(['year','period']).sum()

PowerTo_Sto_Heat_EV = ResToHeat_df.merge(ResToEV_df,left_index=(True), right_index=(True))
PowerTo_Sto_Heat_EV = PowerTo_Sto_Heat_EV.merge(ResToStat_Sto_df,left_index=(True), right_index=(True))
PowerTo_Sto_Heat_EV = PowerTo_Sto_Heat_EV.merge(ResToStat_Sto_out_df,left_index=(True), right_index=(True))

if save_csv: Heat_hp_dh_df.to_excel('./results/'+scen+'/Heat_hp_dh_df.xlsx', sheet_name='Heat_hp_dh_df', index=True) 
if save_csv: Heat_hp_ind_df.to_excel('./results/'+scen+'/Heat_hp_ind_df.xlsx', sheet_name='Heat_hp_ind_df', index=True) 
if save_csv: PowerTo_Sto_Heat_EV.to_excel('./results/'+scen+'/PowerTo_Sto_Heat_EV.xlsx', sheet_name='PowerTo_Sto_Heat_EV', index=True) 

heatpump_ind_cap_inv     = list()
heatpump_dh_cap_inv     = list()
for year in years:
    for genco in HeatPump_units:
        heatpump_ind_cap_inv.append([year,  genco, round(heat_pump_ind_cap_results[genco, year],2)])
        for chp in chp_units:
            heatpump_dh_cap_inv.append([year, genco,chp, round(heat_pump_dh_cap_results[genco,chp,year],2)])

HeatPump_ind_inv_df = pd.DataFrame(heatpump_ind_cap_inv, columns = ['year', 'HeatPump','Cap_inv'])
HeatPump_ind_inv_df['Market'] = 'Individual'
HeatPump_ind_inv_df = HeatPump_ind_inv_df.pivot_table(index = ['year', 'Market'], columns = 'HeatPump', values = 'Cap_inv')

HeatPump_dh_inv_df = pd.DataFrame(heatpump_dh_cap_inv, columns = ['year', 'HeatPump','Market','Cap_inv'])
HeatPump_dh_inv_df = HeatPump_dh_inv_df.pivot_table(index = ['year', 'Market'], columns = 'HeatPump', values = 'Cap_inv')

HeatPump_dh_inv_df=HeatPump_dh_inv_df.reset_index()
HeatPump_ind_inv_df=HeatPump_ind_inv_df.reset_index()
HeatPump_inv = pd.concat([HeatPump_dh_inv_df,HeatPump_ind_inv_df]).groupby(['year','Market']).sum()


#%% Traditional boilers
boiler_l      = list()
boiler_dh     = list()
boiler_ind_cap_inv     = list()
boiler_dh_cap_inv     = list()

for year in years:
    for period in periods:
        for genco in thermal_units:
            boiler_l.append([year, period, genco, round(boiler_ind_generation_results[genco,period, year],2)])
            for chp_market in chp_units:
                boiler_dh.append([year, period, genco,chp_market, round(boiler_dh_generation_results[genco,chp_market,period, year],2)])  
                    
boiler_ind_df = pd.DataFrame(boiler_l, columns = ['year','period', 'boiler','TraditionalBoilersHeat_ind'])
boiler_ind_df = boiler_ind_df.set_index(['year','period','boiler'])
boiler_ind_df = boiler_ind_df.pivot_table(index = ['year','period'], columns = 'boiler', values = 'TraditionalBoilersHeat_ind')
boiler_ind_df['Total_boiler_ind']=boiler_ind_df.sum(axis =1)

boiler_dh_df  = pd.DataFrame(boiler_dh, columns = ['year','period','boiler', 'DistHeating','TraditionalBoilersHeat_DH'])
boiler_dh_df_total = boiler_dh_df.drop(['DistHeating','boiler'],axis =1).groupby(['year','period']).sum().rename(columns = {'TraditionalBoilersHeat_DH':'Total_boiler_dh'})
boiler_gen_dh_ind_total = boiler_ind_df.merge(boiler_dh_df_total,left_index=(True), right_index=(True))

if save_csv: boiler_ind_df.to_excel('./results/'+scen+'/boiler_ind_df.xlsx', sheet_name='boiler_ind_df', index=True) 
if save_csv: boiler_dh_df.to_excel('./results/'+scen+'/boiler_dh_df.xlsx', sheet_name='boiler_dh_df', index=True) 

for year in years:
    for genco in thermal_units:
        boiler_ind_cap_inv.append([year,  genco, round(trad_boiler_ind_cap_inv_results[genco, year],2)])
        for chp in chp_units:
            boiler_dh_cap_inv.append([year, genco,chp, round(trad_boiler_dh_cap_inv_results[genco,chp,year],2)])

boiler_ind_inv_df = pd.DataFrame(boiler_ind_cap_inv, columns = ['year', 'boiler','Cap_inv'])
boiler_ind_inv_df['Market'] = 'Individual'
boiler_ind_inv_df = boiler_ind_inv_df.pivot_table(index = ['year', 'Market'], columns = 'boiler', values = 'Cap_inv')

boiler_dh_inv_df = pd.DataFrame(boiler_dh_cap_inv, columns = ['year', 'boiler','Market','Cap_inv'])
boiler_dh_inv_df = boiler_dh_inv_df.pivot_table(index = ['year', 'Market'], columns = 'boiler', values = 'Cap_inv')

boiler_dh_inv_df=boiler_dh_inv_df.reset_index()
boiler_ind_inv_df=boiler_ind_inv_df.reset_index()
boiler_inv = pd.concat([boiler_ind_inv_df,boiler_dh_inv_df]).groupby(['year','Market']).sum()


#%% CHP  - HEAT GENERATION FROM CHP
heat_gen_list    = list()
total_heat       = 0
total_heat_gen   = 0
for year in years:
    for period in periods:    
        for genco in chp_units:
            total_heat_gen   = total_heat_gen + round(heat_gen_results[genco,period,year],2)
            heat_gen_list.append([genco, period,year,round(heat_gen_results[genco,period,year],2)])
              
heat_gen_list_df = pd.DataFrame(heat_gen_list, columns = ["unit", 'period','year', 'heat'])
heat_CHP = heat_gen_list_df.pivot_table(index = ['year','period'], columns = 'unit', values = 'heat')
heat_CHP['Total_CHP']=heat_CHP.sum(axis =1)
heat_col_name = list(heat_CHP.columns)
if save_csv: 
    heat_CHP.to_excel('./results/'+scen+'/heat_CHP.xlsx', sheet_name='heat_CHP', index=True) 
    Heat_no_load.to_excel('./results/'+scen+'/Heat_no_load.xlsx', sheet_name='Heat_no_load', index=True) 


#%%
HeatGen_HP_Boiler_ind = boiler_ind_df.merge(Heat_hp_ind_df,left_index=(True), right_index=(True))
HeatGen_HP_Boiler_ind=HeatGen_HP_Boiler_ind.drop('Total_boiler_ind',axis = 1)
HeatGen_HP_Boiler_ind_year = HeatGen_HP_Boiler_ind.reset_index().drop('period',axis = 1).groupby('year').sum()

HeatGen_HP_Boiler_ind_year['Shed_load'] = No_load_year.Individual
HeatGen_HP_Boiler_ind_year=HeatGen_HP_Boiler_ind_year.clip(lower=0)

plt.figure()
HeatGen_HP_Boiler_ind_year.plot(kind='bar', stacked= True)
plt.legend(HeatGen_HP_Boiler_ind_year.columns, bbox_to_anchor=(1, 1), loc='upper left', title='Technology')
plt.title('Individual heating generation')
plt.xlabel("Year")
plt.ylabel("Heat Generation (MWh)")
plt.savefig('./results/'+scen+'/HeatGen_HP_Boiler_ind_year.png', bbox_inches='tight')
plt.show()


boiler_dh_df['TraditionalBoilersHeat_DH']=boiler_dh_df['TraditionalBoilersHeat_DH'].round(decimals = 2) 
boiler_dh_df_year = boiler_dh_df.drop('DistHeating', axis = 1)
boiler_dh_df_year = boiler_dh_df_year.pivot_table(index = ['year', 'period'], columns = 'boiler', values = 'TraditionalBoilersHeat_DH')
boiler_dh_df_year = boiler_dh_df_year.reset_index().drop('period',axis = 1).groupby('year').sum()
HeatGen_HP_Boiler_dh_year = boiler_dh_df_year.merge(Heat_hp_dh_df_year,left_index=(True), right_index=(True))
heat_CHP_year = heat_CHP.reset_index().drop(['period','Total_CHP'],axis = 1).groupby('year').sum()
HeatGen_HP_Boiler_CHP_dh_year= HeatGen_HP_Boiler_dh_year.merge(heat_CHP_year,left_index=(True), right_index=(True))
HeatGen_HP_Boiler_CHP_dh_year = HeatGen_HP_Boiler_CHP_dh_year.round(2)


No_load_dh= No_load_year.drop(['Individual', 'Electricity'], axis =1)
No_load_dh.columns = ["Shed_load" + col for col in No_load_dh.columns]
HeatGen_HP_Boiler_CHP_dh_year = pd.concat([HeatGen_HP_Boiler_CHP_dh_year, No_load_dh], axis=1)


zeros = (HeatGen_HP_Boiler_CHP_dh_year == 0).all().all()
if not zeros:
    plt.figure()
    HeatGen_HP_Boiler_CHP_dh_year.plot(kind='bar', stacked= True)
    plt.legend(HeatGen_HP_Boiler_CHP_dh_year.columns, bbox_to_anchor=(1, 1), loc='upper left', title='Technology')
    plt.title('District heating generation')
    plt.xlabel("Year")
    plt.ylabel("Heat Generation (MWh)")
    plt.savefig('./results/'+scen+'/HeatGen_HP_Boiler_CHP_dh_year.png', bbox_inches='tight')
    plt.show()


#%%
ev_storage_level = list()
evToPower = list()
evToHeat = list()
for year in years:
    for period in periods:
        ev_storage_level.append([period,year,round(ev_sto_soc_results[period,year],2)])
        evToPower.append([period,year,round(ev_sto_out_results[period,year],2)])
        #evToHeat.append([period,year,round(ev_heat[period,year].x,2)])


ev_storage_level_df = pd.DataFrame(ev_storage_level, columns = ['period','year', 'soc'])
ev_storage_level_df = ev_storage_level_df.set_index(['year','period'])

evToPower_df = pd.DataFrame(evToPower, columns = ['period','year', 'evToPower'])
evToPower_df = evToPower_df.set_index(['year','period'])

ev_df = ev_storage_level_df.merge(ResToEV_df,left_index=(True), right_index=(True))
ev_df = ev_df.merge(evToPower_df,left_index=(True), right_index=(True))
if save_csv: ev_df.to_excel('./results/'+scen+'/ev_df.xlsx', sheet_name='ev_df', index=False) 

ev_df_plot = ev_df.reset_index().drop(['year','period'], axis =1) 

zeros = (ev_df_plot == 0).all().all()
if not zeros:
    ev_df_plot = ev_df_plot.reset_index()
    plt.figure()
    plt.stackplot(ev_df_plot['index'],ev_df_plot['soc'],ev_df_plot['ResToEV'],ev_df_plot['evToPower'],
                  colors =['r', 'c','b'])
    plt.legend(['soc','RestoEV','EVToPower'])
    plt.show()
#%%

h2_storage  = list()
h2_in_out   = list()
h2_out      = list()
h2          = list()
h2_gen      = list()
h2_sto_inv  = list()
h2_elec_inv = list()
P_to_H2     = list()
H2_gen_tech = list()
h2_cum      = 0

for year in years:
    for period in periods:
        P_to_H2.append([year,period,round(power_to_h2_results[period,year],2)])
    
P_to_H2_df = pd.DataFrame(P_to_H2, columns = ['year','period','ResToH2'])
P_to_H2_df=P_to_H2_df.set_index(['year','period'])
P2X = PowerTo_Sto_Heat_EV.merge(P_to_H2_df,left_index=(True), right_index=(True))
if save_csv: P2X.to_excel('./results/'+scen+'/P2X.xlsx', sheet_name='P2X', index=False) 

for year in years:
    for period in periods:
        for unit in Elec_h2_units:
            H2_gen_tech.append([year, period, unit, round(generation_H2_results[unit, period, year],2)])
            
H2_gen_tech_df = pd.DataFrame(H2_gen_tech, columns = ['year','period','Electrolyzer_tech','H2Gen'])
H2_gen_tech_df = H2_gen_tech_df.pivot_table(index  = ['year','period'], columns = 'Electrolyzer_tech', values = 'H2Gen')
H2_gen_tech_df['TotalH2']=H2_gen_tech_df.sum(axis =1)

for year in years:
    for period in periods:
        for unit in H2_storage_unit:
            h2.append([year,period,unit,round(power_to_h2_results[period,year],2),round(h2_sto_out_results[unit,period,year],2),round(h2_sto_soc_results[unit,period,year],2) ])         

for year in years:
    for unit in H2_storage_unit:
        h2_sto_inv.append([year,unit,round(h2_sto_max_cap_results[unit,year],2)])

h2_sto_inv_df = pd.DataFrame(h2_sto_inv, columns = ['year','unit', 'H2_sto_inv']).set_index('year')  
h2_sto_inv_df = h2_sto_inv_df.pivot_table(index  = ['year'], columns = 'unit', values = 'H2_sto_inv')
       
for year in years:
    for unit in Elec_h2_units:
        h2_elec_inv.append([year,unit,round(h2_electrolysis_max_cap_results[unit,year],2)])
        
h2_elec_inv_df = pd.DataFrame(h2_elec_inv, columns = ['year','unit', 'H2_Elect_inv']).set_index('year')  
h2_elec_inv_df = h2_elec_inv_df.pivot_table(index  = ['year'], columns = 'unit', values = 'H2_Elect_inv')    
            
h2_df = pd.DataFrame(h2, columns = ['year','period','unit', 'PtoH2', 'h2_sto_out', 'h2_soc']) 
h2_df = h2_df.set_index(['year','period'])
h2_df = h2_df.merge(H2_gen_tech_df,left_index=(True), right_index=(True))   
if save_csv: h2_df.to_excel('./results/'+scen+'/h2_gen_df.xlsx', sheet_name='h2', index=True) 
if save_csv: h2_sto_inv_df.to_excel('./results/'+scen+'/h2_sto_inv_df.xlsx', sheet_name='h2_sto_inv_df', index=True) 
if save_csv: h2_elec_inv_df.to_excel('./results/'+scen+'/h2_elec_inv_df.xlsx', sheet_name='h2_elec_inv_df', index=True) 


#%%
FC_inv= list()
FC_gen= list()
for year in years:
    for period in periods:
        for unit in FC_units:
            FC_gen.append([year,period,unit,round(generation_FC_results[unit,period,year],2)])
            
for year in years:
    for unit in FC_units:
        FC_inv.append([year,unit,round(cap_inv_FC_results[unit,year],2)])      
        
FC_gen_df = pd.DataFrame(FC_gen, columns = ['year','period','unit', 'FC_gen']).set_index(['year','period'])    
FC_gen_df = FC_gen_df.pivot_table(index = ['year','period'], columns = 'unit', values = 'FC_gen')

FC_gen = pd.DataFrame(FC_gen, columns = ['year','period','unit', 'FC_gen']).set_index(['year','unit'])
FC_inv = pd.DataFrame(FC_inv, columns = ['year','unit', 'FC_inv']).set_index(['year','unit'])
FC_gen=FC_gen.drop('period',axis = 1).groupby(['year','unit']).sum()
FC_data = FC_gen.merge(FC_inv, left_index=True, right_index=True)
if save_csv: FC_gen_df.to_excel('./results/'+scen+'/FC_gen.xlsx', sheet_name='FC_gen', index=True) 
if save_csv: FC_inv.to_excel('./results/'+scen+'/FC_inv.xlsx', sheet_name='FC_inv', index=True) 
if save_csv: FC_data.to_excel('./results/'+scen+'/FC_data_agg.xlsx', sheet_name='FC_data_agg', index=True) 

#%%

CO2_power= list()
CO2_heat= list()
CO2_industry= list()
for year in years:
    CO2_power.append([year,round(CO2_total_results[year],2)])
    CO2_heat.append([year,round(CO2_total_heat_results[year],2)])
    CO2_industry.append([year,round(CO2_total_industry_results[year],2)])


CO2_power_df = pd.DataFrame(CO2_power, columns = ['year','CO2_power']).set_index(['year'])
CO2_heat_df  = pd.DataFrame(CO2_heat, columns = ['year','CO2_heat']).set_index(['year'])
CO2_industry_df  = pd.DataFrame(CO2_industry, columns = ['year','CO2_industry']).set_index(['year'])

CO2_total_df    = CO2_power_df.merge(CO2_heat_df,left_index=(True), right_index=(True))
CO2_total_df    = CO2_total_df.merge(CO2_industry_df,left_index=(True), right_index=(True))


#%%
Stat_sto_inv= list()
for year in years:
    for unit in Stat_storage_unit:
        Stat_sto_inv.append([year, unit , round(stat_sto_cap_inv_results[unit, year],2)])
Stat_sto_inv_df = pd.DataFrame(Stat_sto_inv, columns = ['year','Stat_sto_unit','Investment']).set_index(['year','Stat_sto_unit'])
#%%
ceep_total   = 0 
demand_total = 0
ceep_list    = list()
for year in years:
    for period in periods:
        ceep_total   = ceep_total + ceep_results[period,year]
        demand_total = demand_total + demand['Industry',period,year]  + demand['Buildings',period,year] + demand['Transport',period,year] + demand['Power',period,year] 
        ceep_list.append([year, period,round(ceep_results[period,year],2),round(demand['Industry',period,year]  + demand['Buildings',period,year] + demand['Transport',period,year] + demand['Power',period,year] ,2)])
ceep_df = pd.DataFrame(ceep_list, columns = ['year', 'period', 'CEEP','Demand'])
ceep_df = ceep_df.set_index(['year', 'period'])
ceep_df = ceep_df.merge(PowerTo_Sto_Heat_EV, left_index=True, right_index=True)
ceep_df = ceep_df.merge(ev_df['evToPower'], left_index=True, right_index=True)

#ceep_df = ceep_df.drop('StaSTO_Out', axis = 1)


#%%
table = pd.pivot_table(gen_by_fuel, values='Dispatch', index=['year'],columns=['Fuel'], aggfunc=np.sum)
table['Total']=table.sum(axis = 1)
table['%-RES']=round((table['Wind']+table['Solar']+table['Hydro']+table['Biomass'])/table['Total']*100,2)
table = pd.DataFrame(table['%-RES'])
h2_df = h2_df.reset_index().drop(['period','unit'], axis = 1).groupby('year').sum()
ceep_year = ceep_df.reset_index().groupby('year').sum()
####WE NEED TO CALCULATE THE DELTA IN STORAGES PER HOUR AND THEN AGGREGATE. DO NOT AGGREGATE BEFORE DOING THIS AS IT IS HAPPENING HERE AND BELOW.
ceep_year = ceep_year.merge(h2_df['PtoH2'], left_index=True, right_index=True).drop('period', axis =1)
ceep_year['%-CEEP'] = round(ceep_year['CEEP']/(ceep_year['Demand'] + ceep_year['ResToHeat'] + ceep_year['ResToEV'] - ceep_year['evToPower']/ev_Grid_eff + ceep_year['PtoH2'] + ceep_year['ResToStaSTO'] - ceep_year['StaSTO_Out'])*100,2)


print('Done printing and ploting')
######################################################################################################################
######################################################################################################################
print('####################################################################################')
print('###############################*REPORT*#############################################')
print('####################################################################################')
print('____________________________________________________________________________________')
print('*Investment in Power Sector*')
print(inv_add_df.to_string())
print('____________________________________________________________________________________')
print('*Shed Load*')
print(No_load_year.clip(lower=0).to_string())
print('____________________________________________________________________________________')
print('*Percentage Energy Mix - RES (solar, wind, biomass, hydro) and CO2*')
print(table.to_string())
print('')
print('CO2 Emissions')
print(CO2_total_df.to_string())
print('____________________________________________________________________________________')
print('*Critical Excess of Electricity Production*')
print('CEEP by year (GWh) ')
for index, row in ceep_year.iterrows():
  print('Year {}: CEEP = {} MWh ({}%).'.format(index,round(row['CEEP'],2), row['%-CEEP']))
print('____________________________________________________________________________________')
print('*Heat Generaiong District Heating*') 
print(HeatGen_HP_Boiler_CHP_dh_year.clip(0).to_string())    
print(' ') 
print('*Heat Generaiong Individual*') 
print(HeatGen_HP_Boiler_ind_year.to_string())    
print(' ') 
print('Capacity Investment in boilers') 
print(boiler_inv.to_string()) 
print(' ') 
print('Capacity Investment in HeatPump') 
print(HeatPump_inv.to_string()) 
print('____________________________________________________________________________________')
print('*Hydrogen generation and capacity*')
print(' ')
print('Hydrogen Gen')
print(h2_df.to_string())
print(' ')
print('H2 storage investment')
print(h2_sto_inv_df.to_string())
print(' ')
print('H2 electrolizer investment')
print(h2_elec_inv_df.to_string())
print('____________________________________________________________________________________')
print('*Fuel cells generation and capacity*')
print(FC_data.to_string())
print('____________________________________________________________________________________')
print('*Stationary Storage Investment*')
print(Stat_sto_inv_df.to_string())
print('____________________________________________________________________________________')
print('*Industry Fuel Use*')
print(fuel_use_ind_df_year.to_string())
print('____________________________________________________________________________________')

File_object = open(r'./results/'+scen+'/report.txt',"w")
File_object.write("#################################################################################### \n")
File_object.write("###############################*REPORT*############################################# \n") 
File_object.write("#################################################################################### \n") 
File_object.write("*Investment in Power Sector* \n") 
File_object.write(str(inv_add_df)+"\n") 
File_object.write("____________________________________________________________________________________\n") 
File_object.write('*Shed Load*')
File_object.write(str(No_load_year)+"\n") 
File_object.write("____________________________________________________________________________________\n") 
File_object.write("*Energy Mix - NCRE (solar, wind, biomass, hror) and CO2 Emissions*\n")
File_object.write(str(table)+"\n")  
File_object.write("CO2 Emissions \n") 
File_object.write(str(CO2_total_df)+"\n")  
File_object.write("____________________________________________________________________________________\n") 
File_object.write("*Critical Excess of Electricity Production* \n") 
for index, row in ceep_year.iterrows():
    File_object.write(str('Year {}: CEEP = {} MWh ({}%).'.format(index,round(row['CEEP'],2), row['%-CEEP'])+"\n"))
File_object.write("____________________________________________________________________________________\n")  
File_object.write("*Heat generation District Heating* \n") 
File_object.write(str(HeatGen_HP_Boiler_CHP_dh_year.to_string())+"\n")  
File_object.write("*Heat generation Individual Heating* \n") 
File_object.write(str(HeatGen_HP_Boiler_ind_year.to_string())+"\n")  
File_object.write("Capacity Investment in boilers \n")
File_object.write(str(boiler_inv.to_string())+"\n")  
File_object.write("Capacity Investment in HeatPumps \n")
File_object.write(str(HeatPump_inv.to_string())+"\n")
File_object.write("____________________________________________________________________________________\n") 
File_object.write('*Hydrogen generation and capacity*\n')
File_object.write("Hydrogen Gen \n") 
File_object.write(str(h2_df.to_string())+"\n")  
File_object.write("H2 storage investment \n") 
File_object.write(str(h2_sto_inv_df.to_string())+"\n")  
File_object.write("H2 electrolizer investment \n") 
File_object.write(str(h2_elec_inv_df.to_string())+"\n") 
File_object.write("____________________________________________________________________________________\n") 
File_object.write('*Fuel cells generation and capacity*\n')
File_object.write(str(FC_data.to_string())+"\n") 
File_object.write("____________________________________________________________________________________\n") 
File_object.write('*Stationary Storage Investment*\n')
File_object.write(str(Stat_sto_inv_df.to_string())+"\n") 
File_object.write("____________________________________________________________________________________\n") 
File_object.write('*Industry Fuel Use*\n')
File_object.write(str(fuel_use_ind_df_year.to_string())+"\n") 
File_object.write("____________________________________________________________________________________\n") 
File_object.close() 
###############

