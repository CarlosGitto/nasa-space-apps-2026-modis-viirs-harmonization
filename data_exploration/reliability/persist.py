import glob, re, numpy as np, pandas as pd
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
R=6371.0; RAD=1.5
xyz=lambda d:(lambda a,b:np.c_[np.cos(a)*np.cos(b),np.cos(a)*np.sin(b),np.sin(a)])(np.radians(d.latitude.values),np.radians(d.longitude.values))*R
def prep(d):
    d=d.copy(); d['t']=(pd.to_datetime(d.acq_date)+pd.to_timedelta(d.acq_time//100,'h')+pd.to_timedelta(d.acq_time%100,'m')).sub(pd.Timestamp('1970-01-01')).div(pd.Timedelta(minutes=1)); return d.reset_index(drop=True)
def analyse(d):
    """persist flag per detection (another pass of same sensor family 1h-36h away within 1.5 km); event component id"""
    n=len(d); pr=cKDTree(xyz(d)).query_pairs(RAD,output_type='ndarray'); t=d.t.values
    persist=np.zeros(n,bool); comp=np.arange(n)
    if len(pr):
        dt=np.abs(t[pr[:,0]]-t[pr[:,1]]); ok=(dt>=60)&(dt<=36*60); persist[pr[ok,0]]=True; persist[pr[ok,1]]=True
        e=pr[dt<=36*60]; g=coo_matrix((np.ones(len(e)),(e[:,0],e[:,1])),shape=(n,n)); _,comp=connected_components(g,directed=False)
    return persist,comp
def hit(a,b,rad=1.0,dt=30):
    if len(a)==0 or len(b)==0: return np.zeros(len(a),bool)
    ta,tb=a.t.values,b.t.values; L=cKDTree(xyz(b)).query_ball_point(xyz(a),r=rad); return np.array([any(abs(tb[j]-ta[i])<=dt for j in js) for i,js in enumerate(L)])
reg_rows=[]; aq=[]
for fv in sorted(glob.glob('firms/multi/*_VIIRS_SNPP_SP_2019-*.csv')):
    reg=re.match(r'.*/(.+?)_VIIRS_SNPP_SP_',fv).group(1); win=fv[-14:-4]; fm=fv.replace('VIIRS_SNPP_SP','MODIS_SP')
    v=prep(pd.read_csv(fv)); m=prep(pd.read_csv(fm)); 
    if len(v)<50 or len(m)<50: continue
    days=sorted(set(v.acq_date)); mid=set(days[1:-1])
    pv,cv=analyse(v); pm,cm=analyse(m); v['persist']=pv; v['comp']=cv; m['persist']=pm; m['comp']=cm
    vm=v[v.acq_date.isin(mid)]; mmid=m[m.acq_date.isin(mid)]
    # events: components touching the middle days, lasting >1 pass
    ev=lambda d: d.groupby('comp').agg(npass=('t',lambda x:x.round(-1).nunique()),first=('t','min'),last=('t','max'))
    evv=ev(v[v.comp.isin(vm.comp)]); evm=ev(m[m.comp.isin(mmid.comp)])
    # Aqua daytime middle-day pixels: persistence + hit by VIIRS (1 km, 30 min) in VIIRS-covered area
    a=m[(m.satellite=='Aqua')&(m.daynight=='D')&(m.acq_date.isin(mid))].copy()
    vd=v[v.daynight=='D']
    if len(a)>=20 and len(vd)>=20:
        a['hit']=hit(a,vd); a['cov']=hit(a,vd,100,30); a['region']=reg; a['win']=win; aq.append(a[a['cov']][['region','win','persist','hit','frp','confidence']])
    reg_rows.append(dict(region=reg,win=win,v_pix=len(vm),m_pix=len(mmid),v_persist=vm.persist.mean(),m_persist=mmid.persist.mean(),v_ev=len(evv),m_ev=len(evm),v_ev_multi=int((evv.npass>=2).sum()),m_ev_multi=int((evm.npass>=2).sum()),
        v_ev_day=int(((evv['last']-evv['first'])>=12*60).sum()),m_ev_day=int(((evm['last']-evm['first'])>=12*60).sum())))
    print(reg,win,flush=True)
r=pd.DataFrame(reg_rows); r.to_csv('firms/multi/persistence_by_window.csv',index=False)
A=pd.concat(aq); A.to_csv('firms/multi/aqua_persistence_hits.csv',index=False)
print('\nSHARE OF DETECTIONS THAT PERSIST (another pass of the same sensor within 1.5 km, 1-36 h later/earlier), middle days only')
print('VIIRS',(r.v_persist*r.v_pix).sum()/r.v_pix.sum(),'MODIS',(r.m_persist*r.m_pix).sum()/r.m_pix.sum())
print('\nAqua day detections: VIIRS hit-rate (1 km, 30 min) by persistence'); print(A.groupby('persist').hit.agg(['size','mean']).round(3).rename(columns={'mean':'seen by VIIRS'}))
print(A.groupby(['persist',pd.cut(A.frp,[0,10,20,50,1e9],labels=['<10','10-20','20-50','>50'])],observed=True).hit.agg(['size','mean']).round(2).unstack(0).to_string())
for c,nm in [('ev','events'),('ev_multi','events seen on >=2 passes'),('ev_day','events lasting >=12 h')]:
    s=r[(r.v_ev>=20)&(r.m_ev>=20)].copy(); s['ratio']=s[f'v_{c}']/s[f'm_{c}'].replace(0,np.nan); s=s.replace([np.inf],np.nan).dropna(subset=['ratio'])
    print(f'{nm:28s} VIIRS/MODIS ratio across {len(s)} region-windows: median {s.ratio.median():.2f}  IQR {s.ratio.quantile(.25):.2f}-{s.ratio.quantile(.75):.2f}  CV {s.ratio.std()/s.ratio.mean():.2f}')
s=r[(r.v_pix>=50)&(r.m_pix>=50)].copy(); s['ratio']=s.v_pix/s.m_pix; print(f'{"pixels (middle days, all passes)":28s} ratio: median {s.ratio.median():.2f}  IQR {s.ratio.quantile(.25):.2f}-{s.ratio.quantile(.75):.2f}  CV {s.ratio.std()/s.ratio.mean():.2f}  n {len(s)}')
