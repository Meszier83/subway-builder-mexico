"""Collapse colocated ordinary game sites, retaining the statistical OD authority."""
import copy
from collections import defaultdict


def collapse(points, pops, model_points):
    model = {p['id']:p for p in model_points}
    groups = defaultdict(list)
    mapping = {p['id']:p['id'] for p in model_points}
    for point in points:
        authority = model[point['id']]
        # Special IDs are part of the quota and game contracts.
        if authority.get('is_special'):
            key = ('special', point['id'])
        else:
            key = ('ordinary', tuple(point['location']), authority['municipality'],
                   authority['zone'], authority.get('road_component'))
        groups[key].append(point)
    sites = []
    for group in groups.values():
        chosen = min(group, key=lambda p:(model[p['id']]['attraction'] <= 0, p['id']))
        site = copy.deepcopy(chosen)
        site.update(residents=sum(p['residents'] for p in group),
                    jobs=sum(p['jobs'] for p in group), popIds=[])
        for point in group:
            mapping[point['id']] = chosen['id']
        sites.append(site)
    remapped = copy.deepcopy(pops)
    by_id = {p['id']:p for p in sites}
    for pop in remapped:
        pop['residenceId'] = mapping[pop['residenceId']]
        pop['jobId'] = mapping[pop['jobId']]
        by_id[pop['residenceId']]['popIds'].append(pop['id'])
        by_id[pop['jobId']]['popIds'].append(pop['id'])
    locations = defaultdict(list)
    for site in sites:
        locations[tuple(site['location'])].append(site['id'])
    return sites, remapped, mapping, dict(
        merged_points=len(points)-len(sites),
        remaining_colocated_locations=sum(len(ids)>1 for ids in locations.values()),
        semantics='Game sites merged only at identical coordinates and territory; statistical points and OD cells retained')
