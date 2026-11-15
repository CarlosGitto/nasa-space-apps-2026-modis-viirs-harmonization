"""Fair comparison: always evaluate on r6 cell-year totals; k comes from level r (global, r2..r6) or from a hierarchical shrinkage
global -> r2 -> r3 -> r4 -> r5 -> r6 (log k pulled to the parent with weight n/(n+lam), n = MODIS detections in the training years).
Train on one year parity, test on the other. Usage: python -I scripts/k_cell_research2.py <dir>"""
import sys, numpy as np, pandas as pd, h3
T=pd.read_parquet(f'{sys.argv[1]}/cellmonth.parquet'); LV=[2,3,4,5,6]
cells=T.cell.unique(); par={r:dict(zip(cells,[h3.cell_to_parent(c,r) if r<6 else c for c in cells])) for r in LV}
for r in LV: T[f'g{r}']=T.cell.map(par[r])
def run(metric,lams,stratum=None):
    m,v,n='m_'+metric,'v_'+metric,'m_n'
    A=T.groupby(['cell','year']).agg(m=(m,'sum'),v=(v,'sum'),mn=(n,'sum'),**{f'g{r}':(f'g{r}','first') for r in LV}).reset_index(); A['par']=A.year%2
    out={}
    for p in (0,1):
        tr=A[A.par!=p]; te=A[(A.par==p)&(A.m>0)&(A.v>0)].copy(); kg=np.log(tr.v.sum()/tr.m.sum()); est={'global':pd.Series(kg,index=te.index)}
        for r in LV:  # plain k at level r, fallback to the coarser plain k
            s=tr.groupby(f'g{r}')[['m','v']].sum(); k=np.log(s.v/s.m).where((s.m>0)&(s.v>0)); est[f'r{r}']=te[f'g{r}'].map(k)
        for r in LV: est[f'r{r}']=est[f'r{r}'].fillna(est['r'+str(r-1)] if r>2 else est['global']) if r>2 else est[f'r{r}'].fillna(est['global'])
        for lam in lams:  # hierarchical shrinkage
            kp={0:kg}; prev=None; res={}
            cur=pd.Series(kg,index=te.index)
            for r in LV:
                s=tr.groupby(f'g{r}').agg(m=('m','sum'),v=('v','sum'),n=('mn','sum')); lk=np.log(s.v/s.m).where((s.m>0)&(s.v>0))
                pk=tr.groupby(f'g{r}')[f'g{r-1}' if r>2 else f'g{r}'].first().map(res[r-1]) if r>2 else pd.Series(kg,index=s.index)
                w=s.n/(s.n+lam); res[r]=(w*lk+(1-w)*pk).where(lk.notna(),pk)
            est[f'shrink{lam}']=te.g6.map(res[6]).fillna(kg)
        for name,e in est.items():
            err=np.log(te.v/te.m)-e; out.setdefault(name,[]).append(pd.DataFrame({'e':err,'w':te.v,'mn':te.mn}))
    return {k:pd.concat(v) for k,v in out.items()}
for metric,lab in (('f','FRP'),('n','detection counts')):
    O=run(metric,[10,30,100,300,1000]); print(f'\n== {lab}: held-out r6 cell-years (V>0, M>0), error = ln(V/(k M)), k from the other parity')
    rows=[]
    for name,R in O.items():
        row=dict(k_from=name,n=len(R),rmse=np.sqrt((R.e**2).mean()),mad=R.e.abs().median(),w_rmse=np.sqrt((R.e**2*R.w).sum()/R.w.sum()),bias=R.e.mean())
        for lo,hi in ((1,5),(5,30),(30,1e9)):
            s=R[(R.mn>=lo)&(R.mn<hi)]; row[f'rmse M{lo}-{int(hi) if hi<1e9 else "+"}']=np.sqrt((s.e**2).mean())
        rows.append(row)
    print(pd.DataFrame(rows).round(3).to_string(index=False))
