"""Spatial representation of residential groups before any OD is assigned."""
import copy
import hashlib
import math
import numpy as np
from pyproj import Geod

MAX_GROUPING_DISPLACEMENT_M = 500.
GEOD = Geod(ellps='WGS84')


def resolve(points, size):
    if type(size) is not int or size<=0:
        raise ValueError('Fixed group size must be a positive integer')
    final = copy.deepcopy(points)
    pools = {}
    report = dict(size=size,partitions=[],groups=[],source_mapping={},
                  max_allowed_displacement_m=MAX_GROUPING_DISPLACEMENT_M,
                  distance_basis='WGS84 geodesic from each statistical source to its represented anchor',
                  basis='Statistical residential points retained separately; nearby travelers grouped within hard territories')
    original_mass = sum(p['commuters'] for p in points)
    original_population = sum(p['population'] for p in points)
    for p in final:
        budget = int(p['commuters'])
        if not budget: continue
        if p.get('is_special'):
            report['source_mapping'][p['id']] = [p['id']]
            continue
        p['statistical_commuters'] = budget
        p['statistical_population'] = p['population']
        p['commuters'] = p['pea_15ymas'] = 0
        report['source_mapping'][p['id']] = []
        if budget:
            # Transfer only represented travelers; other residents stay at their source.
            population_per_person = 1.
            p['population'] -= population_per_person*budget
            p['residents'] = p['population']
            key = (p['zone'],p.get('road_component'),p['municipality'])
            pools.setdefault(key,[]).append(dict(point=p,remaining=budget,population_per_person=population_per_person))
    for key,entries in sorted(pools.items(),key=lambda x:repr(x[0])):
        entries.sort(key=lambda e:(*e['point']['location'],e['point']['id']))
        coords = np.asarray([e['point']['location'] for e in entries],float)
        remaining = np.asarray([e['remaining'] for e in entries],dtype=np.int64)
        partition_mass = int(remaining.sum())
        partition_count = 0
        emitted = {}
        while remaining.any():
            first = int(np.flatnonzero(remaining)[0])
            # Keep the first existing source as anchor. Every contributor must
            # individually satisfy the radius; a chain of nearby sources cannot
            # pull the final anchor away from the original residence.
            distance = np.zeros(len(entries))
            if remaining[first]>=size:
                order = [first]
            else:
                _,_,distance = GEOD.inv(np.full(len(coords),coords[first,0]),
                    np.full(len(coords),coords[first,1]),coords[:,0],coords[:,1])
                order = sorted(np.flatnonzero((remaining>0) & (distance<=MAX_GROUPING_DISPLACEMENT_M)),
                               key=lambda i:(distance[i],entries[i]['point']['id']))
            contributions = []
            needed = size
            for i in order:
                take = min(needed,int(remaining[i]))
                contributions.append((int(i),take))
                remaining[i] -= take; needed -= take
                if not needed: break
            # An existing, allowed source location avoids placing a centroid across barriers.
            anchor = first
            p = entries[anchor]['point']
            budget = sum(take for _,take in contributions)
            provenance = [dict(id=entries[i]['point']['id'],commuters=take,
                               municipality=entries[i]['point']['municipality'],location=entries[i]['point']['location'],
                               zone=entries[i]['point']['zone'],road_component=entries[i]['point'].get('road_component'))
                          for i,take in contributions]
            signature = repr((key,size,sorted((r['id'],r['commuters']) for r in provenance))).encode()
            node = dict(p,id='dv2_fixed_'+hashlib.sha256(signature).hexdigest()[:16],
                        commuters=budget,pea_15ymas=budget,jobs=0,attraction=0,is_special=False,
                        population=sum(entries[i]['population_per_person']*take for i,take in contributions),
                        source_contributions=copy.deepcopy(provenance))
            node['residents'] = node['population']
            node.pop('statistical_population',None); node.pop('statistical_commuters',None)
            if node['id'] in emitted:
                prior = emitted[node['id']]
                for field in ('population','residents','commuters','pea_15ymas'): prior[field] += node[field]
                prior['source_contributions'] += provenance
            else:
                emitted[node['id']] = node
                final.append(node)
            for i,_ in contributions: report['source_mapping'][entries[i]['point']['id']].append(node['id'])
            shifts = [float(distance[i])/1000. for i,_ in contributions]
            if any(d>MAX_GROUPING_DISPLACEMENT_M/1000. for d in shifts):
                raise ValueError('Fixed group representation exceeds spatial limit')
            report['groups'].append(dict(id=node['id'],commuters=budget,location=node['location'],
                municipality=node['municipality'],zone=node['zone'],road_component=node.get('road_component'),contributions=provenance,
                max_displacement_km=max(shifts),person_km_displacement=sum(d*take for d,(_,take) in zip(shifts,contributions))))
            partition_count += 1
        report['partitions'].append(dict(zone=key[0],road_component=key[1],municipality=key[2],pooled_commuters=partition_mass,
                                         pooled_groups=partition_count,
                                         remainder_groups=sum(bool(p['commuters']%size) for p in emitted.values())))
    if sum(p['commuters'] for p in final) != original_mass:
        raise ValueError('Fixed group representation changed traveler mass')
    if not math.isclose(sum(p['population'] for p in final),original_population,rel_tol=1e-10,abs_tol=1e-6):
        raise ValueError('Fixed group representation changed population mass')
    if any(p['commuters'] > p['population']+1e-8 for p in final):
        raise ValueError('Fixed group representation violates residential capacity')
    report['expected_cohorts'] = sum(math.ceil(p['commuters']/size) for p in final if p['commuters'])
    report['full_groups'] = sum(p['commuters']//size for p in final)
    report['remainder_groups'] = sum(bool(p['commuters']%size) for p in final)
    report['total_person_km_displacement'] = sum(g['person_km_displacement'] for g in report['groups'])
    report['max_displacement_km'] = max((g['max_displacement_km'] for g in report['groups']),default=0.)
    from collections import Counter
    contributions = Counter({p['id']:p['commuters'] for p in final if p['id'] in {o['id'] for o in points}})
    for group in report['groups']:
        for source in group['contributions']: contributions[source['id']] += source['commuters']
    if contributions != Counter({p['id']:p['commuters'] for p in points}):
        raise ValueError('Fixed group representation changed source residential budgets')
    report['source_mapping'] = {key:sorted(set(values)) for key,values in report['source_mapping'].items()}
    original_municipal, final_municipal = Counter(),Counter()
    for p in points: original_municipal[p['municipality']] += p['commuters']
    for p in final: final_municipal[p['municipality']] += p['commuters']
    report['municipal_representation_delta'] = dict(final_municipal)
    for code in original_municipal: report['municipal_representation_delta'][code] -= original_municipal[code]
    if any(report['municipal_representation_delta'].values()):
        raise ValueError('Fixed group representation crosses municipal boundaries')
    for group in report['groups']:
        if any((r['municipality'],r['zone'],r['road_component']) !=
               (group['municipality'],group['zone'],group['road_component']) for r in group['contributions']):
            raise ValueError('Fixed group representation crosses a hard territory')
    return sorted(final,key=lambda p:p['id']),report
