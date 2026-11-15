"""MODIS bow-tie merge + size-corrected FRP, tagged with H3 r6, from the export of k_cell_export.py.
Usage: python -I scripts/k_cell_prep.py <out_dir>"""
import sys, h3, numpy as np, pandas as pd
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
src=open('scripts/series_area_threshold.py').read(); exec(src[src.index('INC=np.radians'):src.index("DEDUP=os.environ")])
out=sys.argv[1]
m=pd.read_parquet(f'{out}/modis_raw.parquet'); print('MODIS rows',len(m),flush=True)
d=dedup_modis(m); s=np.sqrt(d.scan*d.track); d['frp_c']=d.frp/np.select([s<1.2,s<1.6,s<2.2],[1.0,1.13,1.15],1.26)
d['h3']=[h3.latlng_to_cell(a,b,6) for a,b in zip(d.latitude,d.longitude)]
d.drop(columns=['t']).to_parquet(f'{out}/modis_dedup.parquet'); print('MODIS dedup',len(d),flush=True)
