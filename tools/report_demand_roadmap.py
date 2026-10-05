"""Summarize isolated demand checkpoints against the captured starting baseline."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def destination_tiles(demand):
    locations = {p['id']: p['location'] for p in demand['points']}
    result = defaultdict(int)
    for pop in demand['pops']:
        lon, lat = locations[pop['jobId']]
        # A stable geography comparison independent of regenerated point IDs.
        import math
        result[(math.floor(lon/.01), math.floor(lat/.01))] += pop['size']
    return result


def summarize(baseline, candidate):
    rows = []
    for city in ['cancun_riviera_maya', 'merida']:
        for mode in ['legacy', 'official_blocks']:
            if not (candidate/city/f'{mode}_report.json').exists():
                continue
            a = json.loads((baseline/city/f'{mode}_report.json').read_text())
            b = json.loads((candidate/city/f'{mode}_report.json').read_text())
            da = json.loads((baseline/city/f'{mode}_demand.json').read_text())
            db = json.loads((candidate/city/f'{mode}_demand.json').read_text())
            ta, tb = destination_tiles(da), destination_tiles(db)
            tile_shift = sum(abs(ta.get(k, 0)/a['commuters']-tb.get(k, 0)/b['commuters'])
                             for k in ta.keys() | tb.keys())/2
            row = dict(city=city, mode=mode,
                       input_hashes_equal=a['inputs'] == b['inputs'],
                       source_jobs_delta=b['estimated_jobs']-a['estimated_jobs'],
                       poi_ids_equal=a['special_ids'] == b['special_ids'],
                       destination_tile_share_shift=tile_shift,
                       baseline={k: a[k] for k in ['points','cohorts','commuters','bytes','total_seconds','peak_rss_mb']},
                       candidate={k: b[k] for k in ['points','cohorts','commuters','bytes','total_seconds','peak_rss_mb']},
                       cross_zone_commuters=b['cross_zone_commuters'],
                       final_excluded_points=b['final_excluded_points'])
            rows.append(row)
    (candidate/'checkpoint.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    lines = ['# Demand checkpoint comparison', '',
             '| City / mode | Points | Cohorts | Commuters | Bytes | Time (s) | Destination tile share shift |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        a, b = r['baseline'], r['candidate']
        cells = [f"{a[k]} → {b[k]}" for k in ['points','cohorts','commuters','bytes']]
        lines.append(f"| {r['city']} / {r['mode']} | {' | '.join(cells)} | "
                     f"{a['total_seconds']:.1f} → {b['total_seconds']:.1f} | "
                     f"{r['destination_tile_share_shift']:.2%} |")
    lines += ['', 'Destination tiles are 0.01-degree bins; share shift is half the L1',
              'difference in commuter shares. It measures change, not accuracy or game FPS.',
              'See checkpoint.json for source invariants and spatial results.', '']
    (candidate/'checkpoint.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    summarize(args.baseline, args.candidate)
