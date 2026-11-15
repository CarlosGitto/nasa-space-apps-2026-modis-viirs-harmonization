"""Composite reliability index from persistence and confidence, validated against confirmation by another sensor.
Targets (daytime, middle days, 2019 windows): MODIS Aqua pixel confirmed by VIIRS SNPP or NOAA-20 (<=2 km, +-60 min); VIIRS SNPP pixel confirmed by NOAA-20 (<=1 km, +-75 min).
Features: confidence, persistence flag (persist.py rule), graded persistence (number of neighbour pairs 1.5 km, 1-36 h), log FRP. Usage: python -I scripts/composite_index.py"""
import glob, re, numpy as np, pandas as pd
from scipy.spatial import cKDTree
exec(open('scripts/persist.py').read().split('def hit')[0])           # xyz, prep, analyse
def graded(d):
    pr=cKDTree(xyz(d)).query_pairs(1.5,output_type='ndarray'); n=np.zeros(len(d))
    if len(pr):
        dt=np.abs(d.t.values[pr[:,0]]-d.t.values[pr[:,1]]); ok=(dt>=60)&(dt<=36*60); n+=np.bincount(pr[ok,0],minlength=len(d))+np.bincount(pr[ok,1],minlength=len(d))
    return n
def label(A,B,rad,dt):
    A=A.sort_values('t').reset_index(drop=True); pas=(A.t.diff()>60).cumsum().values; hit=np.zeros(len(A),bool); cov=np.zeros(len(A),bool); vt=B.t.values; X=xyz(B); XA=xyz(A)
    for p in np.unique(pas):
        ii=np.where(pas==p)[0]; T=A.t.values[ii].mean(); sel=np.where(np.abs(vt-T)<=dt+10)[0]
        if len(sel)==0: continue
        d_=cKDTree(X[sel]).query(XA[ii],k=1)[0]; cov[ii]=d_<=100; hit[ii]=d_<=rad
    A['hit']=hit; A['cov']=cov; return A
rows=[]
for fv in sorted(glob.glob('firms/multi/*_VIIRS_SNPP_SP_2019-*.csv')):
    reg=re.match(r'.*/(.+?)_VIIRS_SNPP_SP_',fv).group(1); win=fv[-14:-4]
    try: S=prep(pd.read_csv(fv)); N=prep(pd.read_csv(fv.replace('SNPP','NOAA20'))); M=prep(pd.read_csv(fv.replace('VIIRS_SNPP_SP','MODIS_SP')))
    except Exception: continue
    if min(len(S),len(N),len(M))<50: continue
    days=sorted(set(S.acq_date)); mid=set(days[1:-1])
    for d in (S,N,M): d['persist']=analyse(d)[0]; d['ng']=graded(d)
    Sd=S[S.daynight=='D']; Nd=N[N.daynight=='D']
    # MODIS Aqua <- VIIRS (SNPP+NOAA-20)
    A=M[(M.satellite=='Aqua')&(M.daynight=='D')&(M.acq_date.isin(mid))]
    if len(A)>=20:
        A=label(A,pd.concat([Sd,Nd]),2.0,60); A=A[A['cov']]; rows.append(pd.DataFrame(dict(target='Aqua<-VIIRS',region=reg,conf=pd.to_numeric(A.confidence).values/100,persist=A.persist.values,ng=A.ng.values,lfrp=np.log(A.frp.values+1),hit=A.hit.values)))
    # VIIRS SNPP <- NOAA-20
    A=Sd[Sd.acq_date.isin(mid)]
    if len(A)>=50:
        A=label(A,Nd,1.0,75); A=A[A['cov']]; cm=A.confidence.astype(str).str.lower().map({'l':0.0,'n':0.5,'h':1.0}).values
        rows.append(pd.DataFrame(dict(target='SNPP<-NOAA20',region=reg,conf=cm,persist=A.persist.values,ng=A.ng.values,lfrp=np.log(A.frp.values+1),hit=A.hit.values)))
    print(reg,win,flush=True)
D=pd.concat(rows,ignore_index=True); D.to_csv('firms/multi/composite_index_data.csv',index=False); print(D.groupby('target').agg(n=('hit','size'),confirmed=('hit','mean'),persistent=('persist','mean')))
