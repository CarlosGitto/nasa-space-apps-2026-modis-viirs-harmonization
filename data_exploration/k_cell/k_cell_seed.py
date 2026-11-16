"""Freeze k (VIIRS SNPP / MODIS Terra+Aqua, bow-tie merged, size-corrected) per H3 cell by hierarchical shrinkage:
global -> r2 -> r3 -> r4 -> r5 -> r6, log k of a cell pulled to its parent with weight n/(n+LAMBDA), n = MODIS detections
(complete-coverage cell-months, years 2013-2025). Chosen by year-parity cross-validation (k_cell_research2.py).
Usage: python -I scripts/k_cell_seed.py <dir with cellmonth.parquet> <out.csv>"""
import sys, numpy as np, pandas as pd, h3
LAMBDA=100; T=pd.read_parquet(f'{sys.argv[1]}/cellmonth.parquet'); LV=[2,3,4,5,6]
cells=T.cell.unique(); P={r:dict(zip(cells,[h3.cell_to_parent(c,r) if r<6 else c for c in cells])) for r in LV}
for r in LV: T[f'g{r}']=T.cell.map(P[r])
rows=[]; res={}
gk={m:np.log(T[f'v_{m}'].sum()/T[f'm_{m}'].sum()) for m in 'fn'}; rows.append(dict(h3='global',res=0,n_modis=int(T.m_n.sum()),n_viirs=int(T.v_n.sum()),k_frp=np.exp(gk['f']),k_count=np.exp(gk['n'])))
for r in LV:
    s=T.groupby(f'g{r}').agg(n=('m_n','sum'),vn=('v_n','sum'),mf=('m_f','sum'),vf=('v_f','sum'),mn=('m_n','sum'),vn_=('v_n','sum'))
    for m,(a,b) in {'f':('mf','vf'),'n':('mn','vn_')}.items():
        lk=np.log(s[b]/s[a]).where((s[a]>0)&(s[b]>0))
        if r==2: pk=pd.Series(gk[m],index=s.index)
        else:
            pa=pd.Series({g:P[r-1][c] for g,c in zip(T[f'g{r}'],T.cell)}) if False else None
            par=T.drop_duplicates(f'g{r}').set_index(f'g{r}')[f'g{r-1}']; pk=par.reindex(s.index).map(res[(r-1,m)])
        w=s.n/(s.n+LAMBDA); res[(r,m)]=(w*lk+(1-w)*pk).where(lk.notna(),pk)
    for g in s.index: rows.append(dict(h3=g,res=r,n_modis=int(s.n[g]),n_viirs=int(s.vn[g]),k_frp=float(np.exp(res[(r,'f')][g])),k_count=float(np.exp(res[(r,'n')][g]))))
S=pd.DataFrame(rows); S.to_csv(sys.argv[2],index=False,float_format='%.5g')
print(S.groupby('res').agg(cells=('h3','size'),k_frp_med=('k_frp','median'),k_frp_p5=('k_frp',lambda x:x.quantile(.05)),k_frp_p95=('k_frp',lambda x:x.quantile(.95)),k_cnt_med=('k_count','median'),k_cnt_p5=('k_count',lambda x:x.quantile(.05)),k_cnt_p95=('k_count',lambda x:x.quantile(.95))).round(2))
