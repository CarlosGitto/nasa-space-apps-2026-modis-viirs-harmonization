"""k = VIIRS(SNPP)/MODIS per H3 cell: which resolution is stable enough?
Cell-month valid only if an area covers the cell with complete MODIS and SNPP downloads (bronze lineage windows).
Cross-validation by year parity: k from the other parity predicts this parity's annual cell total.
Usage: python -I scripts/k_cell_research.py <dir with parquet from k_cell_export/prep>"""
import sys, numpy as np, pandas as pd, h3
d=sys.argv[1]; Y0,Y1=2013,2025
cov=pd.read_parquet(f'{d}/coverage.parquet'); cov['ym']=pd.PeriodIndex(cov.acq_month,freq='M')
cov=cov[(cov.ym>=pd.Period(f'{Y0}-01'))&(cov.ym<=pd.Period(f'{Y1}-12'))&cov.modis_complete&cov.viirs_complete]
areas={a:set(g.ym) for a,g in cov.groupby('area')}; box={a:g.iloc[0][['west','south','east','north']].astype(float).tolist() for a,g in cov.groupby('area')}
print(len(areas),'areas with complete MODIS and SNPP months in',Y0,'-',Y1,{a:len(m) for a,m in areas.items()})
M=pd.read_parquet(f'{d}/modis_dedup.parquet',columns=['acq_date','frp_c','h3']).rename(columns={'h3':'cell'})
V=pd.read_parquet(f'{d}/snpp.parquet',columns=['acq_date','frp','h3_id']); V['cell']=V.h3_id.map(lambda x:format(int(x),'x')) if V.h3_id.iloc[0].isdigit() else V.h3_id
for t in (M,V): t['ym']=pd.PeriodIndex(t.acq_date.str[:7],freq='M')
cells=sorted(set(M.cell)|set(V.cell)); lat=dict(zip(cells,[h3.cell_to_latlng(c) for c in cells]))
def valid_months(c):
    la,lo=lat[c]; s=set()
    for a,ms in areas.items():
        w,s_,e,n=box[a]
        if w<=lo<=e and s_<=la<=n: s|=ms
    return s
vm={c:valid_months(c) for c in cells}
ok=pd.DataFrame([(c,p) for c,ps in vm.items() for p in ps],columns=['cell','ym']); print('valid cell-months',len(ok),'cells with any',ok.cell.nunique())
def tot(t,col,name): return t.merge(ok,on=['cell','ym']).groupby(['cell','ym']).agg(**{name+'_n':(col,'size'),name+'_f':(col,'sum')}).reset_index()
T=ok.merge(tot(M,'frp_c','m'),how='left',on=['cell','ym']).merge(tot(V,'frp','v'),how='left',on=['cell','ym']).fillna(0); T['year']=T.ym.dt.year
print('cell-months',len(T),'with MODIS',(T.m_n>0).sum(),'with SNPP',(T.v_n>0).sum())
T.to_parquet(f'{d}/cellmonth.parquet')
def parent(c,r): return h3.cell_to_parent(c,r) if r<6 else c
def cv(T,r,metric,min_train=1,shrink=None):
    X=T.assign(g=T.cell.map(lambda c:parent(c,r))).groupby(['g','year'])[[f'm_{metric}',f'v_{metric}']].sum().reset_index(); X.columns=['g','year','m','v']
    X['par']=X.year%2; res=[]
    for p in (0,1):
        tr=X[X.par!=p].groupby('g')[['m','v']].sum(); te=X[(X.par==p)&(X.m>0)&(X.v>0)]
        k=(tr.v/tr.m).where((tr.m>0)&(tr.v>0)); kg=tr.v.sum()/tr.m.sum()
        kk=te.g.map(k); fall=kk.isna(); kk=kk.fillna(kg)
        res.append(pd.DataFrame({'e':np.log(te.v/(kk*te.m)),'w':te.v,'fall':fall,'g':te.g}))
    R=pd.concat(res); return R
for metric in ('f','n'):
    print(f'\n== {"FRP" if metric=="f" else "detection counts"}: held-out annual cell total, ln(V/(k*M)); k trained on the other year parity')
    rows=[]
    # baselines
    Xg=T.groupby('year')[[f'm_{metric}',f'v_{metric}']].sum()
    for r,lab in [(0,'global'),(2,'r2'),(3,'r3'),(4,'r4'),(5,'r5'),(6,'r6')]:
        if r==0:
            X=T.groupby(['cell','year'])[[f'm_{metric}',f'v_{metric}']].sum().reset_index(); X.columns=['cell','year','m','v']; X['par']=X.year%2; res=[]
            for p in (0,1):
                tr=X[X.par!=p]; kg=tr.v.sum()/tr.m.sum(); te=X[(X.par==p)&(X.m>0)&(X.v>0)]; res.append(pd.DataFrame({'e':np.log(te.v/(kg*te.m)),'w':te.v,'fall':False}))
            R=pd.concat(res)
        else: R=cv(T,r,metric)
        rows.append(dict(level=lab,cell_years=len(R),rmse=np.sqrt((R.e**2).mean()),mad=R.e.abs().median(),w_rmse=np.sqrt((R.e**2*R.w).sum()/R.w.sum()),bias=R.e.mean(),fallback=R.fall.mean(),ncells=R.g.nunique() if 'g' in R else np.nan))
    print(pd.DataFrame(rows).round(3).to_string(index=False))
