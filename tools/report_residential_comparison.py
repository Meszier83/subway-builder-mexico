"""Summarize and plot the isolated residential comparisons (run under WSL)."""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sb_mexico.gravity import (prepare_polygon_geom, is_point_in_prepared_polygon,
                               is_point_in_exclusion_zone)


def residential_rows(frame, city, zones):
    polygon = prepare_polygon_geom(city.get('urban_core_polygon')) if city.get('restrict_demand_to_urban_core',True) else None
    inside = [not is_point_in_exclusion_zone(row.lon,row.lat,zones) and
              (not polygon or is_point_in_prepared_polygon(row.lon,row.lat,polygon))
              for row in frame.itertuples()]
    return frame.loc[inside].copy()


def summarize(root):
    baseline=json.loads((root/'legacy_report.json').read_text(encoding='utf-8'))
    candidate=json.loads((root/'official_blocks_report.json').read_text(encoding='utf-8'))
    old=pd.read_csv(root/'legacy_census.csv',dtype={'cve_mun_clean':str,'ageb_clean':str,'mza_clean':str})
    new=pd.read_csv(root/'official_blocks_census.csv',dtype={'cve_mun_clean':str,'ageb_clean':str,'mza_clean':str,'loc_clean':str})
    for frame in [old,new]: frame['mza_clean']=frame.mza_clean.str.zfill(3)
    # Legacy ingestion discards locality. Reconcile only unique abbreviated keys
    # and explicitly report anything requiring a manual cross-locality review.
    keys=['cve_mun_clean','ageb_clean','mza_clean']
    ambiguous_old=old.duplicated(keys,keep=False); ambiguous_new=new.duplicated(keys,keep=False)
    safe_keys=pd.concat([old.loc[ambiguous_old,keys],new.loc[ambiguous_new,keys]]).drop_duplicates()
    old=old.merge(safe_keys.assign(_ambiguous=True),on=keys,how='left')
    new=new.merge(safe_keys.assign(_ambiguous=True),on=keys,how='left')
    old=old.loc[old._ambiguous.isna()];new=new.loc[new._ambiguous.isna()]
    city=baseline['config']['city'];zones=baseline['config'].get('exclusion_zones')
    old=residential_rows(old,city,zones);new=residential_rows(new,city,zones)
    old_keys=set(map(tuple,old[keys].values));new_keys=set(map(tuple,new[keys].values))
    gained=new.loc[~new[keys].apply(tuple,axis=1).isin(old_keys)]
    lost=old.loc[~old[keys].apply(tuple,axis=1).isin(new_keys)]
    common=old.merge(new,on=keys,suffixes=('_old','_new'),validate='one_to_one')
    shared_delta=float((common.pea_real_new-common.pea_real_old).sum())
    delta=float(gained.pea_real.sum()-lost.pea_real.sum()+shared_delta)
    rounding_delta=candidate['grid']['rounding_delta_pea']-baseline['grid']['rounding_delta_pea']
    observed=candidate['commuters']-baseline['commuters']
    reconciliation=dict(gained_blocks=len(gained),lost_blocks=len(lost),
        gained_pea=float(gained.pea_real.sum()),lost_pea=float(lost.pea_real.sum()),
        common_pea_change=shared_delta,retained_pea_change=delta,rounding_change=rounding_delta,
        observed_commuter_change=observed,unexplained_commuters=observed-delta-rounding_delta,
        ambiguous_old_rows=int(ambiguous_old.sum()),ambiguous_new_rows=int(ambiguous_new.sum()))
    (root/'reconciliation.json').write_text(json.dumps(reconciliation,indent=2),encoding='utf-8')
    assert abs(reconciliation['unexplained_commuters'])<1e-6,reconciliation
    assert shared_delta==0, 'Projection changed for shared source blocks'
    assert baseline['estimated_jobs']==candidate['estimated_jobs'], 'Employment calibration changed'
    assert baseline['special_ids']==candidate['special_ids'], 'Protected POIs changed'
    rows=[]
    for label,key in [('Game points','points'),('Cohorts','cohorts'),('Commuters','commuters'),('Demand JSON bytes','bytes'),('Static total seconds','total_seconds'),('Peak RSS MB','peak_rss_mb')]:
        a,b=baseline[key],candidate[key]
        rows.append(f'| {label} | {a:,.2f} | {b:,.2f} | {(b/a-1)*100:+.2f}% |')
    text=f"# {city['name']}: residential placement comparison\n\n"+"| Metric | Legacy | Official blocks | Change |\n| --- | ---: | ---: | ---: |\n"+'\n'.join(rows)
    text+='\n\n'+f"Official matched block points outside their polygons: {candidate['geometry_check']['official_block_outside']}.\n\n"
    text+=f"Commuter change: {reconciliation['gained_pea']:.6f} regained PEA - {reconciliation['lost_pea']:.6f} filtered PEA + {rounding_delta:.6f} rounding change = {observed} commuters. Shared-block projections and estimated jobs are unchanged.\n\n"
    text+=f"Cross-zone commuters: {baseline['cross_zone_commuters']} -> {candidate['cross_zone_commuters']}. Final excluded points: {baseline['final_excluded_points']} -> {candidate['final_excluded_points']}. Protected POI IDs agree.\n\n"
    text+='Timing and memory describe isolated Python runs, not game performance. Both use the canonical driving fallback rather than OSRM. Game schema parsing and load/simulation/save/reload remain unverified. Legacy remains the default. The other held defects remain outside this delivery.\n'
    (root/'comparison.md').write_text(text,encoding='utf-8')
    plot(root,baseline,candidate)
    return reconciliation


def plot(root,baseline,candidate):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator, FormatStrFormatter
    config=baseline['config'];code=config['city']['code']
    if code=='CUR':
        regions=[('Cancun centre',[-86.92,21.10,-86.80,21.22]),('Cancun outskirts',[-87.05,21.12,-86.89,21.27]),
                 ('Playa del Carmen',[-87.13,20.58,-87.02,20.71]),('Cozumel',[-87.0,20.44,-86.88,20.56])]
    else:
        regions=[('Merida centre',[-89.68,20.92,-89.55,21.04]),('Northern outskirts',[-89.70,21.04,-89.48,21.22]),
                 ('Eastern outskirts',[-89.53,20.94,-89.34,21.10]),('Progreso',[-89.74,21.23,-89.55,21.35])]
    figure,axes=plt.subplots(len(regions),2,figsize=(12,13),layout='constrained')
    roads_path=ROOT/'dist'/root.name/'roads.geojson'
    import geopandas as gpd
    roads=gpd.read_file(roads_path) if roads_path.exists() else None
    for col,mode in enumerate(['legacy','official_blocks']):
        frame=pd.read_csv(root/f'{mode}_census.csv')
        for row,(name,b) in enumerate(regions):
            ax=axes[row,col];view=frame.loc[frame.lon.between(b[0],b[2])&frame.lat.between(b[1],b[3])]
            if roads is not None:
                streets=roads.cx[b[0]:b[2],b[1]:b[3]]
                if len(streets): streets.plot(ax=ax,color='#aaa',linewidth=.35,alpha=.5)
            points=ax.scatter(view.lon,view.lat,s=np.clip(np.sqrt(view.pobtot_adj),2,24),alpha=.35,color='#246bb2',linewidths=0)
            ax.set(xlim=[b[0],b[2]],ylim=[b[1],b[3]],title=f'{name} | {mode} | {len(view):,} blocks',xlabel='Longitude',ylabel='Latitude')
            ax.set_aspect(1/np.cos(np.deg2rad((b[1]+b[3])/2)))
            ax.xaxis.set_major_locator(MaxNLocator(3))
            ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
            ax.tick_params(axis='x',labelsize=9)
            ax.grid(alpha=.2)
    figure.suptitle(f"{config['city']['name']}: census source placement\nMarker size reflects projected population; these are source blocks, before grid aggregation.",fontsize=13)
    figure.savefig(root/'placement-comparison.png',dpi=130)
    plt.close(figure)


if __name__=='__main__':
    root=Path(sys.argv[1])
    print(json.dumps(summarize(root),indent=2))
