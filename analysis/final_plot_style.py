"""Manuscript chart styles for the annual result plots."""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from analysis.final_plot_context import source
Y=np.array(source.YEARS);N=len(Y);LABELS=[str(y) for y in Y]
BLUE,ORANGE,DARK='#0284c7','#f97316','#243947'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10.5,
 'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':220,
 'axes.labelcolor':'black','text.color':'black'})
manifest={};OUT=None

def save(index,fig):
    name={88:'07',90:'08',95:'09',97:'10',102:'11',104:'12',110:'13',114:'14',118:'15a',120:'15b'}[index]
    png=OUT/('figure_'+name+'.png');pdf=OUT/('figure_'+name+'.pdf')
    fig.savefig(png,facecolor='white')
    with plt.rc_context({'pdf.fonttype':42}):fig.savefig(pdf,facecolor='white')
    plt.close(fig);manifest[str(index)]=str(png)

def comparison(channel,cases,labels,indices):
    fig,ax=plt.subplots(figsize=(8.5,2.7))
    totals={c:np.array([(source.ROWS[c,int(y)]['obj']-source.ROWS['average',int(y)]['obj'])/1e6 for y in Y]) for c in cases}
    for c,label,color,offset in zip(cases,labels,(BLUE,ORANGE),(9,-14)):
        v=totals[c]
        ax.plot(Y,v,'o-',color=color,label=label+' − Average',lw=1.8,ms=4)
        for j,(y,z) in enumerate(zip(Y,v)):
            actual_offset=offset
            if channel=='temperature':
                other=totals[cases[1] if c==cases[0] else cases[0]][j]
                actual_offset=9 if z>other else -16
            ax.annotate(f'{z:+.2f}',(y,z),xytext=(0,actual_offset),textcoords='offset points',ha='center',fontsize=8,color=color)
    ax.axhline(0,ls='--',color='#64748b',label='Average = 0')
    ax.set(xticks=Y,xticklabels=LABELS,xlabel='Planning window',ylabel='Cost difference\n(million EUR)')
    ax.grid(alpha=.12);ax.legend(ncol=3,fontsize=8);ax.margins(y=.28)
    fig.tight_layout();save(indices[0],fig)
    parts=[('generation_investment_cost','Generation investment','#7c3aed'),
           ('heat_sector_investment_cost','Heating investment',ORANGE),
           ('optimized_import_cost','Imports',BLUE),
           ('heat_sector_operation_cost','Heat operation','#eab308'),
           ('remaining','Remaining costs','#64748b')]
    values={}
    for c in cases:
        vals={g:np.array([(source.ROWS[c,int(y)]['components'].get(g,0)-source.ROWS['average',int(y)]['components'].get(g,0))/1e6 for y in Y]) for g,_,_ in parts[:-1]}
        vals['remaining']=totals[c]-sum(vals.values());values[c]=vals
    largest=max(np.abs(v).max() for vals in values.values() for v in vals.values())
    retained=[g for g,_,_ in parts if g=='remaining' or max(abs(values[c][g]).max() for c in cases)>=.02*largest]
    for c in cases:
        for g in list(values[c]):
            if g not in retained:values[c]['remaining']+=values[c].pop(g)
        assert np.max(np.abs(sum(values[c].values())-totals[c]))<1e-7
    fig,axs=plt.subplots(2,1,figsize=(8.5,5),sharex=True)
    fig.subplots_adjust(left=.13,right=.98,bottom=.18,top=.94,hspace=.35)
    def bars(ax,c,subset):
        pp=np.zeros(len(subset));nn=pp.copy()
        for g,label,col in parts:
            if g not in values[c]:continue
            v=values[c][g][subset]
            ax.bar(Y[subset],v,bottom=np.where(v>=0,pp,nn),width=3,color=col,label=label)
            pp+=np.maximum(v,0);nn+=np.minimum(v,0)
        ax.plot(Y[subset],totals[c][subset],'D-',c=DARK,ms=3,lw=1,label='Total difference')
        ax.axhline(0,c='#64748b',lw=.7)
    for ax,c,label in zip(axs,cases,labels):
        bars(ax,c,np.arange(N));ax.set(title=label+' − Average',ylabel='Million EUR',xticks=Y,xticklabels=LABELS)
        ax.grid(axis='y',alpha=.12);ax.set_axisbelow(True)
        if channel=='temperature' and c==cases[0]:
            zax=ax.inset_axes([.48,.12,.49,.48]);bars(zax,c,np.arange(1,N))
            zax.set_xticks(Y[1:]);zax.tick_params(labelsize=6);zax.set_title('2030–2050 enlarged',fontsize=7)
    axs[-1].set_xlabel('Planning window')
    fig.legend(*axs[0].get_legend_handles_labels(),loc='lower center',ncol=3,frameon=False,fontsize=8)
    save(indices[1],fig)
def hstacks(ax,series,colors,title,unit):
    left=np.zeros(N)
    for (label,values),color in zip(series.items(),colors):
        values=np.asarray(values);assert values.min()>-1e-7
        ax.barh(range(N),values,left=left,color=color,label=label,height=.6);left+=values
    for i,z in enumerate(left):ax.text(z+max(left)*.015,i,f'{z:.2f}',va='center',fontsize=8)
    ax.set_yticks(range(N),Y);ax.invert_yaxis();ax.set_xlim(0,max(left)*1.15)
    ax.set_xlabel(unit);ax.set_title(title,loc='left',pad=43)
    ax.legend(loc='lower left',bbox_to_anchor=(0,1.01),ncol=3,frameon=False,fontsize=7.5)
    ax.grid(axis='x',alpha=.1);ax.set_axisbelow(True)
