import matplotlib.pyplot as plt
import pandas as pd

pal2       = ['peru', 'dimgray','silver', 'lightskyblue', 'blue','yellowgreen','seagreen', 'royalblue','red','darkorange','yellow','black']
col_name22 = ['Coal','Diesel','Oil','Gas','LPG','Biomass','Nuclear','Hydro','imports', 'Wind','Solar','shed_load']
col_name   = ['Coal','Diesel','Oil','Gas'      ,'Biomass','Nuclear','Hydro','imports', 'Wind','Solar','shed_load']

#gen=gen.drop('level_0',axis = 1)
gen = gen.reset_index()
if len(gen['year'].unique()) != 1:
    fig, axs = plt.subplots(len(gen['year'].unique()), sharex=False,figsize=(10,12))
    for i in range(len(gen['year'].unique())):
        data_plot = gen[gen['year']==years[i]]
        data_plot2 = pd.DataFrame(columns = col_name22)
        data_plot2 = data_plot2._append(data_plot).fillna(0)
        data_demand = pd.DataFrame(df_demand[df_demand['year']==years[i]])
        data_demand['total'] = data_demand[demand_sectors].sum(axis=1)
        data_demand=data_demand.set_index('period')
        axs[i].stackplot(data_plot2.period,data_plot2[col_name22].T, colors=pal2,alpha = 0.8, labels=col_name22)
        axs[i].set_title(years[i])
        axs[i].set(xlabel='Period', ylabel='[MWh]')
        axs[i].plot(data_demand['total'], color='purple',alpha = 0.2)
        handles, labels = axs[i].get_legend_handles_labels()
    
    fig.legend(handles, labels, loc='right', fontsize='large',bbox_to_anchor=(1.2, 0.4),labels=None, prop={'size': 12})
    plt.subplots_adjust(left=0,
                        bottom=0.1, 
                        right=0.9, 
                        top=0.85, 
                        wspace=1, 
                        hspace=0.45)
    plt.tight_layout()
    plt.savefig('./results/'+scen+'/generation_year.png',  bbox_inches='tight')
    plt.show()
    
    
    gen_plot = gen.drop('period',axis =1).groupby('year').sum()
    data_plot_cum = pd.DataFrame(columns = col_name22)
    data_plot_cum = data_plot_cum._append(gen_plot).fillna(0)
    #data_plot_cum = data_plot_cum.drop('index',axis = 1)
    plt.figure(figsize=(12,6))
    data_plot_cum.plot(kind='bar',stacked =True, color = pal2)
    plt.legend(col_name22, loc='right', fontsize='large',bbox_to_anchor=(1.35, 0.4), prop={'size': 12})
    plt.ylabel('Generation (MWh)')
    plt.title('Total Generation')
    plt.savefig('./results/'+scen+'/gen_year.png', bbox_inches='tight')
    plt.show()

    
if len(gen['year'].unique()) == 1:
        data_plot = gen[gen['year']==years[0]]
        data_plot2 = pd.DataFrame(columns = col_name22)
        data_plot2 = data_plot2._append(data_plot).fillna(0)
        data_demand = pd.DataFrame(df_demand[df_demand['year']==years[0]])
        data_demand['total'] = data_demand[demand_sectors].sum(axis=1)
        data_demand=data_demand.set_index('period')
        plt.figure(figsize=(12,6))
        plt.stackplot(data_plot2.period,data_plot2[col_name22].T, colors=pal2,alpha = 0.8, labels=col_name22)
        plt.legend(col_name22, loc='right', fontsize='large',bbox_to_anchor=(1.2, 0.4), prop={'size': 12})
        plt.xlabel('Period') 
        plt.ylabel('[MWh]')
        plt.plot(data_demand['total'], color='purple',alpha = 0.2)
        #plt.ylim(0, max(data_demand['total'])*1.5)

        plt.title(years[0])
        plt.tight_layout()
        plt.savefig('./results/'+scen+'/generation_year.png',  bbox_inches='tight')
        plt.show()
        
        gen_plot = gen.drop('period',axis =1).groupby('year').sum()
        data_plot_cum = pd.DataFrame(columns = col_name22)
        data_plot_cum = data_plot_cum._append(gen_plot).fillna(0)
        #data_plot_cum = data_plot_cum.drop('index',axis = 1)
        plt.figure(figsize=(12,6))
        data_plot_cum.plot(kind='bar',stacked =True, color = pal2)
        plt.legend(col_name22, loc='right', fontsize='large',bbox_to_anchor=(1.35, 0.4), prop={'size': 12})
        plt.ylabel('Generation (MWh)')
        plt.title('Total Generation')
        plt.savefig('./results/'+scen+'/gen_year.png', bbox_inches='tight')
        plt.show()
################################HYDRO STORAGE PLOT###################################################################
hydro_soc = storage_total_df.reset_index()

if len(hydro_soc['year'].unique()) != 1:
    fig, axs = plt.subplots(len(hydro_soc['year'].unique()), sharex=False,figsize=(10,12))
    for i in range(len(hydro_soc['year'].unique())):
        data_plot = hydro_soc[hydro_soc['year']==years[i]]
        data_plot2 = pd.DataFrame(columns = hydro_soc.columns[2:])
        data_plot2 = data_plot2._append(data_plot).fillna(0)
        axs[i].stackplot(data_plot2.period,data_plot2[hydro_soc.columns[2:]].T, colors=pal2,alpha = 0.8, labels=hydro_soc.columns[2:])
        axs[i].set_title(years[i])
        axs[i].set(xlabel='Period', ylabel='[MWh]')
        handles, labels = axs[i].get_legend_handles_labels()
    
    fig.legend(handles, labels, loc='right', fontsize='large',bbox_to_anchor=(1.3, 0.4),labels=None, prop={'size': 12})
    plt.subplots_adjust(left=0,
                        bottom=0.1, 
                        right=0.9, 
                        top=0.85, 
                        wspace=1, 
                        hspace=0.45)
    plt.tight_layout()
    plt.savefig('./results/'+scen+'/hydro_storage_level.png', bbox_inches='tight')
    plt.show()

if len(hydro_soc['year'].unique()) == 1:
        data_plot = hydro_soc[hydro_soc['year']==years[0]]
        data_plot2 = pd.DataFrame(columns = hydro_soc.columns[2:])
        data_plot2 = data_plot2._append(data_plot).fillna(0)
        plt.figure(figsize=(12,6))
        plt.stackplot(data_plot2.period,data_plot2[hydro_soc.columns[2:]].T, colors=pal2,alpha = 0.8, labels=hydro_soc.columns[2:])
        plt.legend(hydro_soc.columns[2:], loc='right', fontsize='large',bbox_to_anchor=(1.3, 0.4), prop={'size': 12})
        plt.xlabel('Period') 
        plt.ylabel('[MWh]')
        plt.title(years[0])
        plt.tight_layout()
        plt.savefig('./results/'+scen+'/hydro_storage_level.png',  bbox_inches='tight')
        plt.show()
################################Power To X###################################################################
# PtX_plot = P2X.reset_index()

# if len(PtX_plot['year'].unique()) != 1:
#     fig, axs = plt.subplots(len(PtX_plot['year'].unique()), sharex=False,figsize=(10,12))
#     for i in range(len(PtX_plot['year'].unique())):
#         data_plot = PtX_plot[PtX_plot['year']==years[i]]
#         data_plot2 = pd.DataFrame(columns = PtX_plot.columns[2:])
#         data_plot2 = data_plot2._append(data_plot).fillna(0)
#         axs[i].stackplot(data_plot2.period,data_plot2[PtX_plot.columns[2:]].T, alpha = 0.8, labels=PtX_plot.columns[2:]) #colors=pal2,
#         title ='Power to X in {}'.format(str(years[i]))
#         axs[i].set_title(title)
#         axs[i].set(xlabel='Period', ylabel='[MWh]')
#         handles, labels = axs[i].get_legend_handles_labels()
    
#     fig.legend(handles, labels, loc='right', fontsize='large',bbox_to_anchor=(1.3, 0.4),labels=None, prop={'size': 12})
#     plt.subplots_adjust(left=0,
#                         bottom=0.1, 
#                         right=0.9, 
#                         top=0.85, 
#                         wspace=1, 
#                         hspace=0.45)
#     plt.tight_layout()
#     plt.savefig('./results/'+scen+'/Power_to_X.png', bbox_inches='tight')
#     plt.show()

# if len(PtX_plot['year'].unique()) == 1:
#         data_plot = PtX_plot[PtX_plot['year']==years[0]]
#         data_plot2 = pd.DataFrame(columns = PtX_plot.columns[2:])
#         data_plot2 = data_plot2._append(data_plot).fillna(0)
#         plt.figure(figsize=(12,6))
#         plt.stackplot(data_plot2.period,data_plot2[PtX_plot.columns[2:]].T, alpha = 0.8, labels=PtX_plot.columns[2:])
#         plt.legend(PtX_plot.columns[2:], loc='right', fontsize='large',bbox_to_anchor=(1.3, 0.4), prop={'size': 12})
#         plt.xlabel('Period') 
#         plt.ylabel('[MWh]')
#         plt.title(years[0])
#         plt.tight_layout()
#         plt.savefig('./results/'+scen+'/Power_to_X.png',  bbox_inches='tight')
#         plt.show()
        
################################Industry###################################################################
zeros = (fuel_use_ind_df == 0).all().all()
if not zeros:
    
    fuel_use_ind_plot = fuel_use_ind_df.reset_index()
    
    if len(fuel_use_ind_plot['year'].unique()) != 1:
        fig, axs = plt.subplots(len(fuel_use_ind_plot['year'].unique()), sharex=False,figsize=(10,12))
        for i in range(len(fuel_use_ind_plot['year'].unique())):
            data_plot = fuel_use_ind_plot[fuel_use_ind_plot['year']==years[i]]
            data_plot2 = pd.DataFrame(columns = fuel_use_ind_plot.columns[2:])
            data_plot2 = data_plot2._append(data_plot).fillna(0)
            axs[i].stackplot(data_plot2.period,data_plot2[fuel_use_ind_plot.columns[2:]].T, alpha = 0.8, labels=fuel_use_ind_plot.columns[2:]) #colors=pal2,
            title ='Industry fuel use in {}'.format(str(years[i]))
            axs[i].set_title(title)
            axs[i].set(xlabel='Period', ylabel='[MWh]')
            handles, labels = axs[i].get_legend_handles_labels()
        
        fig.legend(handles, labels, loc='right', fontsize='large',bbox_to_anchor=(1.3, 0.4),labels=None, prop={'size': 12})
        plt.subplots_adjust(left=0,
                            bottom=0.1, 
                            right=0.9, 
                            top=0.85, 
                            wspace=1, 
                            hspace=0.45)
        plt.tight_layout()
        plt.savefig('./results/'+scen+'/Industry_fuel_use.png', bbox_inches='tight')
        plt.show()
    
    if len(fuel_use_ind_plot['year'].unique()) == 1:
            data_plot = fuel_use_ind_plot[fuel_use_ind_plot['year']==years[0]]
            data_plot2 = pd.DataFrame(columns = fuel_use_ind_plot.columns[2:])
            data_plot2 = data_plot2._append(data_plot).fillna(0)
            plt.figure(figsize=(12,6))
            plt.stackplot(data_plot2.period,data_plot2[fuel_use_ind_plot.columns[2:]].T,alpha = 0.8, labels=fuel_use_ind_plot.columns[2:])
            plt.legend(fuel_use_ind_plot.columns[2:], loc='right', fontsize='large',bbox_to_anchor=(1.3, 0.4), prop={'size': 12})
            plt.xlabel('Period') 
            plt.ylabel('[MWh]')
            title ='Industry fuel use in {}'.format(str(years[0]))
            plt.title(title)
            plt.tight_layout()
            plt.savefig('./results/'+scen+'/Industry_fuel_use.png',  bbox_inches='tight')
            plt.show()
            
            
