"""Show final residential demand locations before/after the roadmap fixes."""
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import geopandas as gpd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def render(city):
    folder = ROOT/'reports/demand-roadmap/final'/city
    before = ROOT/'reports/demand-roadmap/baseline'/city
    report = json.loads((folder/'official_blocks_report.json').read_text())
    bbox = report['config']['city']['bbox']
    roads = gpd.read_file(ROOT/'dist'/city/'roads.geojson')
    fig, axes = plt.subplots(1,2,figsize=(14,7),layout='constrained')
    for ax, base, title in zip(axes,[before,folder],['Starting official placement','After demand fixes']):
        demand = json.loads((base/'official_blocks_demand.json').read_text())
        points = [p for p in demand['points'] if p['residents'] > 0]
        xy = np.array([p['location'] for p in points])
        values = np.array([p['residents'] for p in points])
        roads.plot(ax=ax,color='#bbbbbb',linewidth=.3,alpha=.5)
        ax.scatter(xy[:,0],xy[:,1],s=np.clip(np.sqrt(values)/2,2,25),
                   c='#146eab',alpha=.6,edgecolors='none')
        ax.set(xlim=(bbox[0],bbox[2]),ylim=(bbox[1],bbox[3]),title=title,
               xlabel='Longitude',ylabel='Latitude')
        ax.ticklabel_format(useOffset=False)
        ax.set_aspect(1/max(.1,np.cos(np.radians(xy[:,1].mean()))))
    fig.suptitle(f'{city}: final residential locations (marker area follows square root of commuters)')
    fig.savefig(folder/'final-location-comparison.png',dpi=140)
    plt.close(fig)
    print(folder/'final-location-comparison.png')


if __name__ == '__main__':
    for city in sys.argv[1:]:
        render(city)
