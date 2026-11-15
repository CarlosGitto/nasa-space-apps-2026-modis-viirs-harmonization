"""Fit and freeze the composite reliability index (SUMMARY.md stage 14): P(an independent sensor confirms the detection)
from confidence, graded persistence and FRP, one logistic model per sensor.
Training data: firms/multi/composite_index_data.csv (composite_index.py; 2019 windows, 25 regions):
  Aqua<-VIIRS   MODIS Aqua day pixel confirmed by VIIRS SNPP / NOAA-20 within 2 km, +-60 min   -> model for `modis`
  SNPP<-NOAA20  VIIRS SNPP day pixel confirmed by NOAA-20 within 1 km, +-75 min                -> model for `viirs`
Features: conf (MODIS confidence/100; VIIRS l/n/h = 0/.5/1), log1p(n_pairs) with n_pairs = neighbours of the same sensor
within 1.5 km and 1-36 h away, ln(frp+1). Out-of-fold AUC by region. Usage: python -I scripts/reliability_fit.py <out.csv>"""
import sys, numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score
D=pd.read_csv('firms/multi/composite_index_data.csv'); rows=[]
for target,sensor in (('Aqua<-VIIRS','modis'),('SNPP<-NOAA20','viirs')):
    d=D[D.target==target].dropna(subset=['conf','ng','lfrp']); X=np.c_[d.conf,np.log1p(d.ng),d.lfrp]; y=d.hit.astype(int).values; g=d.region.values
    oof=np.zeros(len(d))
    for tr,te in GroupKFold(5).split(X,y,g): oof[te]=LogisticRegression(penalty=None,max_iter=2000).fit(X[tr],y[tr]).predict_proba(X[te])[:,1]
    m=LogisticRegression(penalty=None,max_iter=2000).fit(X,y); auc=roc_auc_score(y,oof)
    q=pd.qcut(oof,5,labels=False,duplicates='drop'); cal=pd.DataFrame({'q':q,'p':oof,'y':y}).groupby('q').agg(pred=('p','mean'),obs=('y','mean'))
    print(f'{sensor}: n={len(d):,} confirmed {y.mean():.2f} | AUC out-of-fold {auc:.3f} | coefs conf {m.coef_[0][0]:.3f} log1p(pairs) {m.coef_[0][1]:.3f} lnFRP {m.coef_[0][2]:.3f} intercept {m.intercept_[0]:.3f}')
    print('  calibration (out-of-fold quintiles) predicted/observed:',', '.join(f'{a:.2f}/{b:.2f}' for a,b in zip(cal.pred,cal.obs)))
    rows.append(dict(sensor=sensor,intercept=m.intercept_[0],b_conf=m.coef_[0][0],b_pairs=m.coef_[0][1],b_lfrp=m.coef_[0][2],auc_oof=auc,n_train=len(d),confirmed_rate=y.mean()))
pd.DataFrame(rows).to_csv(sys.argv[1],index=False,float_format='%.6g')
