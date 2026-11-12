"""Milestone A: interval-aware, source-labeled CUR economics calibration.

Run after diagnose_cur_economics.py. Never activates a profile or edits game/maps.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import subprocess
import zipfile

import numpy as np

import diagnose_cur_economics as base

OUT = base.ROOT / 'reports/cur-economic-calibration'
CACHE = base.ROOT / '.cache/cur-economics'
KINDS = ['ordinary', 'airport', 'university']
INCOME_KEYS = ['INCOME_MEAN', 'INCOME_STD_DEV', 'MINIMUM_INCOME', 'MAXIMUM_INCOME']


def quantile_bounds(lower, upper, weights, probabilities=base.QUANTILES):
    """Sharp marginal infimum/supremum quantiles; infinity stays unbounded."""
    def q(values):
        values, w = np.asarray(values, float), np.asarray(weights, float)
        if (len(values) != len(w) or len(w) == 0 or np.any(np.isnan(values))
                or np.any(w <= 0) or not np.all(np.isfinite(w))):
            raise ValueError('Invalid intervals or weights')
        idx = np.argsort(values, kind='stable')
        return values[idx][np.minimum(np.searchsorted(np.cumsum(w[idx]) / w.sum(), probabilities), len(w)-1)]
    if np.any(np.asarray(lower) > np.asarray(upper)):
        raise ValueError('Reversed interval')
    return [{'lower': float(a), 'upper': None if math.isinf(b) else float(b)}
            for a, b in zip(q(lower), q(upper))]


def income_interval(row):
    status = base.income_status(row)
    if status in ('positive_exact', 'reported_zero'):
        return float(row['ingocup']), float(row['ingocup'])
    if status == 'unspecified_income':
        return 0., math.inf
    wage = float(row['salario'])
    if not np.isclose(wage, 278.8 * 30):
        raise ValueError('Unexpected 2025 general wage basis; reinspect source')
    band = int(row['ing7c'])
    factors = {1: (0, 1), 2: (1, 2), 3: (2, 3), 4: (3, 5), 5: (5, math.inf)}
    lo, hi = factors[band]
    return lo * wage, hi * wage


def load_rows(archive):
    domains = {'cancun_city': [], 'quintana_roo_urban': []}
    with zipfile.ZipFile(archive) as z:
        catalog = z.read(base.SD + 'catalogos/ing7c.csv').decode('utf-8-sig')
        if 'No recibe ingresos' not in catalog or 'No especificado' not in catalog:
            raise ValueError('Income classification catalog changed')
        member = base.SD + 'conjunto_de_datos/conjunto_de_datos_sdem_enoe_2025_4t.csv'
        for row in csv.DictReader(io.TextIOWrapper(z.open(member), encoding='cp1252')):
            if row['per'] != '425':
                raise ValueError('Wrong survey period')
            if not (int(row['r_def']) == 0 and int(row['c_res']) in (1, 3)
                    and 15 <= int(row['eda']) <= 98 and int(row['clase2']) == 1):
                continue
            if int(row['cd_a']) == 41:
                domains['cancun_city'].append(row)
            if int(row['cve_ent']) == 23 and int(row['t_loc_tri']) in (1, 2, 3):
                domains['quintana_roo_urban'].append(row)
    return domains


def analyze_domain(rows):
    weights = [float(r['fac_tri']) for r in rows]
    intervals = [income_interval(r) for r in rows]
    lo, hi = zip(*intervals)
    # The all-employed interval distribution includes true zero and unknowns.
    # Sensitivity endpoints are bounds, not imputations or estimated salaries.
    bounds = quantile_bounds(lo, hi, weights)
    specified = [i for i, r in enumerate(rows) if base.income_status(r) != 'unspecified_income']
    specified_bounds = quantile_bounds([lo[i] for i in specified], [hi[i] for i in specified],
                                      [weights[i] for i in specified])
    band_shares = {}
    for band in range(1, 8):
        indices = [i for i, r in enumerate(rows) if int(r['ing7c']) == band]
        band_shares[str(band)] = {'n': len(indices), 'weighted_population': sum(weights[i] for i in indices),
                                  'share': sum(weights[i] for i in indices) / sum(weights)}
    probs = (np.arange(50) + .5) / 50
    all_q = quantile_bounds(lo, hi, weights, probs)
    specified_q = quantile_bounds([lo[i] for i in specified], [hi[i] for i in specified],
                                  [weights[i] for i in specified], probs)
    scenarios = {}
    for prefix, q in [('all_employed', all_q), ('specified_only', specified_q)]:
        for endpoint in ('lower', 'upper'):
            scenarios[prefix + '_' + endpoint] = [None if x[endpoint] is None else x[endpoint] * 12 for x in q]
    muni = Counter()
    for row in rows:
        muni[row['cve_ent'] + row['cve_mun']] += float(row['fac_tri'])
    return {'n': len(rows), 'weighted_employed': sum(weights),
            'weighted_band_shares': band_shares, 'all_employed_quantile_bounds_monthly_mxn': dict(zip(base.LABELS, bounds)),
            'specified_only_quantile_bounds_monthly_mxn': dict(zip(base.LABELS, specified_bounds)),
            'salary_basis_values_mxn_month': sorted({float(r['salario']) for r in rows}),
            'sample_municipality_weighted_totals_not_boundary_coverage': dict(muni),
            'income_scenarios_annual_mxn_50_quantiles': scenarios,
            'bound_semantics': 'Marginal infima/suprema of empirical weighted intervals; open edges closed only for limits. Null upper = unbounded. No confidence intervals.'}


def transport_costs():
    return {
        'period': '2025Q4',
        'fuel': {'status': 'scenario_range_only', 'mxn_per_litre': [23, 24, 26],
                 'efficiency_km_per_litre': [8, 12, 16],
                 'sample_coverage': {'observed_local_station_n': 1, 'dates': '2025-10-06/2025-10-12',
                                     'price_mxn_per_litre': 24.78, 'selection': 'high-margin ranking; not representative'},
                 'representative_station_panel': 'Unavailable: official datos.gob.mx resource API returned HTTP 403',
                 'source': 'https://combustibles.profeco.gob.mx/qqpgasolina/2025/QQPGASOLINA_102025.pdf',
                 'resource_attempt': 'https://www.datos.gob.mx/api/3/action/resource_show?id=c56dc345-cb64-457a-864e-316fe8995a40'},
        'driving': {'formula': 'fuel MXN/litre / efficiency km/litre + nonfuel MXN/km',
                    'central_scenario_mxn_per_km': 24 / 12 + .5,
                    'nonfuel_mxn_per_km': [0, .5, 1], 'nonfuel_status': 'assumed, not a measured CUR vehicle fleet'},
        'fares': {'pilot_mxn': [5, 10, 15, 20], 'stress_mxn': [40, 80],
                  'status': 'hypothetical new subway fares; not observed local subway tariffs',
                  'bus_context': {'urban_mxn': 9.5, 'hotel_zone_mxn': 12,
                                  'amount_source_date': '2024-08-23',
                                  'amount_source': 'https://www.cancun.gob.mx/cancun/noticias/leer/1408',
                                  'later_no_increase_statement_date': '2025-11-27',
                                  'later_source': 'https://qroo.gob.mx/mara-lezama-descarta-aumento-al-transporte-publico-hasta-que-se-brinde-un-beneficio-tangible-a-la-ciudadania/',
                                  'limitation': 'Official historical amounts plus later no-increase statement; not a complete observed Q4 fare schedule'}},
        'parking': {'ordinary_scenarios_mxn_per_leg': [0, 20, 60],
                    'status': 'ordinary-city tariff coverage unavailable; assumed scenarios',
                    'airport_reference': {'source': 'https://sidof.segob.gob.mx/notas/docFuente/5749041',
                                          'effective': '2025-02-16', 'employee_per_event_mxn_before_vat': 43.10,
                                          'first_hour_mxn_before_vat': 42.24,
                                          'limitation': 'Airport-specific published tariff, excludes VAT; continued Q4 validity not independently verified'},
                    'game_semantics': 'PARKING_COST added per getModeChoice call in both commute directions; no daily parking-memory state in this worker path. Airport multiplies it by 5. Do not equate it with one daily real parking payment.'},
        'conversion': 'All profile income, fares, fuel-derived driving and parking divided by the same MXN/game-unit value; construction and operating money unchanged.'}


def check_modes(rows):
    fare, time = {}, {}
    for row in rows:
        if sum(row[k] for k in ('transit', 'driving', 'walking')) != row['population']:
            raise AssertionError('Passenger conservation failed')
        key = tuple(row[k] for k in ('distance_m', 'parking_mxn', 'driving_mxn_per_km', 'kind', 'population'))
        fare.setdefault(key + (row['perceived_transit_minutes'],), []).append((row['fare_mxn'], row['transit']))
        time.setdefault(key + (row['fare_mxn'],), []).append((row['perceived_transit_minutes'], row['transit']))
    for groups in (fare, time):
        if any(any(b[1] > a[1] for a, b in zip(sorted(v), sorted(v)[1:])) for v in groups.values()):
            raise AssertionError('Fare/time monotonicity failed')
    identities = [tuple(r[k] for k in ('distance_m', 'perceived_transit_minutes', 'fare_mxn', 'parking_mxn',
                                     'driving_mxn_per_km', 'kind', 'population')) for r in rows]
    if len(identities) != len(set(identities)):
        raise AssertionError('Duplicated fixture controls')
    return {'evaluations': len(rows), 'conservation': True, 'fare_monotonicity': True,
            'time_monotonicity': True, 'duplicate_cases': 0}


def validate_fit(fit, result, observed):
    unit = fit['mxn_per_game_unit']
    generated = result['distributions']['ordinary']
    reference = base.weighted_quantiles(generated['50'], np.ones(50))
    drift = {}
    for n in (25, 100, 200):
        quantiles = base.weighted_quantiles(generated[str(n)], np.ones(n))
        drift[str(n)] = {key: float(quantiles[i] / reference[i] - 1) for key, i in [('median', 2), ('p90', 4)]}
    mean_error = abs(fit['generated_monthly_mxn']['mean'] / observed['mean'] - 1)
    median_error = abs(fit['quantile_relative_errors']['p50'])
    sensitivity = {}
    for row in result['uncertainty']:
        key = (row['distance_m'], row['perceived_transit_minutes'], row['fare_mxn'])
        sensitivity.setdefault(key, {})[row['scenario']] = row['transit'] / 50
    spreads = {scope: max(abs(v[scope + '_upper'] - v[scope + '_lower']) for v in sensitivity.values())
               for scope in ('all_employed', 'specified_only')}
    # Also measure group-size effects on the same trips; do not treat them as
    # failure of the income quantile gate, which has its own explicit threshold.
    sizes = {}
    for row in result['modes']:
        key = tuple(row[k] for k in ('distance_m', 'parking_mxn', 'driving_mxn_per_km', 'kind',
                                     'perceived_transit_minutes', 'fare_mxn'))
        sizes.setdefault(key, {})[row['population']] = row['transit'] / row['population']
    mode_size_drift = max(abs(v[n] - v[50]) for v in sizes.values() for n in (25, 100, 200))
    criteria = {'mean_error_le_10pct': mean_error <= .1, 'median_error_le_10pct': median_error <= .1,
                'each_quantile_error_le_20pct': fit['max_abs_quantile_error'] <= .2,
                'cohort_quantile_drift_le_15pct': all(abs(v) <= .15 for d in drift.values() for v in d.values()),
                'income_uncertainty_transit_spread_le_10pp': max(spreads.values()) <= .1 + 1e-12}
    exact_error = 0.
    for kind, multiplier in [('ordinary', 1), ('airport', 1.5), ('university', .6)]:
        for n, values in result['distributions'][kind].items():
            expected = base.game_incomes([fit['rules'][k] for k in INCOME_KEYS], int(n), multiplier)
            exact_error = max(exact_error, float(np.max(np.abs(expected - np.array(values)))))
    if exact_error > .01:
        raise AssertionError('Replica no longer matches installed worker')
    return {'criteria': criteria, 'eligible': all(criteria.values()), 'cohort_quantile_drift': drift,
            'income_uncertainty_max_transit_spread': spreads, 'cohort_max_transit_share_drift': mode_size_drift,
            'replica_max_error_game_units': exact_error, 'mean_relative_error': mean_error,
            'median_relative_error': median_error, 'mxn_per_game_unit': unit}


def finance_fixture(results):
    """Transparent accounting fixture, not the game's geometry cost estimator."""
    records = []
    for unit in (1, 20):
        result = next(r for r in results if r['name'] == f'combined_{unit}')
        row = next(r for r in result['modes'] if r['distance_m'] == 8000 and r['perceived_transit_minutes'] == 30
                   and r['fare_mxn'] == 10 and r['parking_mxn'] == 20 and r['kind'] == 'ordinary'
                   and r['population'] == 50 and abs(r['driving_mxn_per_km'] - 2.5) < 1e-9)
        # Fixed service and count assumption: 100 identical OD groups, two legs.
        # Cost totals are deliberately held constant, not rescaled with currency.
        revenue = row['transit'] * 100 * 2 * 10 / unit
        records.append({'mxn_per_game_unit': unit, 'transit_per_50': row['transit'],
                        'fare_game_units': 10 / unit, 'daily_revenue_game_units': revenue,
                        'capital_game_units_assumed': 100_000_000,
                        'daily_operating_cost_game_units_assumed': 50_000,
                        'operating_balance_game_units': revenue - 50_000,
                        'farebox_operating_ratio': revenue / 50_000})
    return {'network_fixture': '10 km, 10 stations, 8 trains, fixed service; 100 identical 50-person OD groups, 2 charged transit legs/day',
            'cost_status': 'Assumed fixed accounting totals, NOT actual game construction/maintenance costing or a live network run',
            'currency_only_fixed_riders_revenue_ratio_20_to_1': .05,
            'records': records,
            'remaining': 'Actual same-network construction/operating cost and service simulation remains pending; income fit cannot certify financial balance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--archive', type=Path, default=CACHE / '2025trim4_csv.zip')
    parser.add_argument('--asar', type=Path, default=base.GAME)
    args = parser.parse_args()
    previous = json.loads((base.ROOT / 'reports/cur-economic-feasibility/metrics.json').read_text(encoding='utf8'))
    if base.sha256(args.archive) != previous['source']['archive_sha256']:
        raise ValueError('Reference archive changed')
    city_path = Path(previous['city_configuration']['path'])
    if base.sha256(city_path) != previous['city_configuration']['sha256']:
        raise ValueError('CUR configuration changed; refresh the original feasibility gate first')
    worker, game = base.extract_game(args.asar, CACHE)
    if game['version'] != '1.7.2':
        raise ValueError('Reinspection required for changed game version')
    rows = load_rows(args.archive)
    domains = {name: analyze_domain(data) for name, data in rows.items()}
    for name, data in rows.items():
        positive = [r for r in data if base.income_status(r) == 'positive_exact']
        observed = base.stats([float(r['ingocup']) for r in positive], [float(r['fac_tri']) for r in positive])
        if observed != previous['domains'][name]['positive_monthly_mxn']:
            raise ValueError('Exact-response target no longer reconciles to original gate')
        if (domains[name]['n'] != previous['domains'][name]['employed_n']
                or domains[name]['weighted_employed'] != previous['domains'][name]['employed_weighted']):
            raise ValueError('Domain counts/weights no longer reconcile')
    costs = transport_costs()
    fits = {}
    profiles = []
    for unit in (1, 20):
        print(f'Fitting revised calibration comparison, MXN/game unit={unit}', flush=True)
        fits[str(unit)] = base.fit_domain(previous['domains']['cancun_city']['positive_monthly_mxn'], unit)
        income = fits[str(unit)]['rules']
        hours = previous['domains']['cancun_city']['positive_weekly_hours_all_employed']['mean'] * 52
        for experiment in ('unmodified', 'income_only', 'hours_only', 'cost_only', 'combined'):
            rules = dict(income) if experiment in ('income_only', 'combined') else {}
            if experiment in ('hours_only', 'combined'):
                rules['HOURS_WORKED_PER_YEAR'] = round(hours, 6)
            profile = {'name': f'{experiment}_{unit}', 'rules': rules, 'mxn_per_game_unit': unit,
                       'local_costs': experiment in ('cost_only', 'combined')}
            if experiment == 'combined':
                profile['income_scenarios'] = domains['cancun_city']['income_scenarios_annual_mxn_50_quantiles']
            profiles.append(profile)
    # Three linked representative corners plus the central scenario. Full fuel
    # price/efficiency/nonfuel cross-product remains available in cost artifact.
    driving = [23/16, 2.5, 26/8 + 1]
    request = {'worker': str(worker), 'calibration': True, 'profiles': profiles,
               'parking': [0, 20, 60], 'driving': driving, 'central_driving': 2.5}
    process = subprocess.run(['node', str(base.ROOT / 'tools/cur_economic_worker.cjs')],
                             input=json.dumps(request), text=True, capture_output=True, timeout=180, check=True)
    exact = json.loads(process.stdout)
    checks = {r['name']: check_modes(r['modes']) for r in exact['results']}
    acceptance = {}
    for unit in (1, 20):
        result = next(r for r in exact['results'] if r['name'] == f'combined_{unit}')
        acceptance[str(unit)] = validate_fit(fits[str(unit)], result,
                                            previous['domains']['cancun_city']['positive_monthly_mxn'])
    rank = sorted((1, 20), key=lambda unit: (fits[str(unit)]['max_abs_quantile_error'],
                                           acceptance[str(unit)]['mean_relative_error']))
    eligible = [unit for unit in rank if acceptance[str(unit)]['eligible']]
    selected = eligible[0] if eligible else None
    decision = {'milestone': 'A', 'decision': 'PROCEED_TO_PRIVATE_ENGINEERING_PILOT' if selected else 'HOLD_RESEARCH_ONLY',
                'selected_conversion_mxn_per_game_unit': selected,
                'best_conditional_fit_conversion': rank[0],
                'failed_criteria': {unit: [k for k, v in a['criteria'].items() if not v] for unit, a in acceptance.items()},
                'runtime_status': 'PENDING; no live game or save modifications',
                'geographic_status': 'Cancun proxy only; current official ENOE boundary not established; no CUR coverage percentage',
                'financial_status': 'Accounting sensitivity only; actual fixed-network costing/service pending'}
    manifest_path = CACHE / 'sources-a/manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf8')) if manifest_path.exists() else {}
    summary = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'reference_period': '2025Q4',
               'reference_archive_sha256': base.sha256(args.archive),
               'prior_metrics_sha256': base.sha256(base.ROOT / 'reports/cur-economic-feasibility/metrics.json'),
               'diagnostic_sha256': base.sha256(Path(__file__)),
               'base_diagnostic_sha256': base.sha256(base.ROOT / 'tools/diagnose_cur_economics.py'),
               'city_configuration': previous['city_configuration'],
               'runtime_versions': previous['runtime_versions'],
               'worker_harness_sha256': base.sha256(base.ROOT / 'tools/cur_economic_worker.cjs'),
               'game': game, 'source_manifest': manifest, 'domains': domains, 'fits': fits,
               'acceptance': acceptance, 'worker_checks': checks, 'finance_fixture': finance_fixture(exact['results']),
               'decision': decision}
    candidate = {'profile_version': '0.2-research', 'enabled': False, 'status': decision['decision'],
                 'map_code': 'CUR', 'period': '2025Q4', 'selected_conversion': selected,
                 'income_rules': fits[str(selected)]['rules'] if selected else None,
                 'research_alternatives': fits, 'activation_blockers': decision['failed_criteria'],
                 'note': 'No qualifying active profile is produced when the uncertainty or fit gate fails.'}
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, value in [('metrics.json', summary), ('worker-results.json', exact),
                            ('transport-cost-assumptions.json', costs), ('candidate-profile.json', candidate)]:
        (args.output / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf8')
    write_reports(args.output, summary, costs, exact)
    print(json.dumps({'output': str(args.output), 'decision': decision, 'acceptance': acceptance}, indent=2))


def write_reports(output, summary, costs, exact):
    decision = summary['decision']
    lines = ['# CUR economic calibration — Milestone A', '',
             f"**Decision: {decision['decision']}.** No mod is activated.", '',
             '## Data and uncertainty', '',
             'The exact-positive-response target is retained for comparability. Income-band responses are now incorporated as intervals, and unspecified income stays [0, infinity). These are identification bounds, not estimates, imputations or confidence intervals.', '',
             'Monthly band basis is the archive salary field, 8,364 MXN = 278.80 x 30. [CONASAMI 2025 daily wage](https://www.gob.mx/conasami/es/articulos/incremento-a-los-salarios-minimos-para-2025?idiom=es). Use the survey basis, not a different monthly conversion.', '',
             '| Domain | Quantile | Lower MXN/month | Upper MXN/month |', '|---|---|---:|---:|']
    for domain, values in summary['domains'].items():
        for name, bounds in values['all_employed_quantile_bounds_monthly_mxn'].items():
            upper = 'Unbounded' if bounds['upper'] is None else f"{bounds['upper']:,.0f}"
            lines.append(f"| {domain} | {name} | {bounds['lower']:,.0f} | {upper} |")
    lines += ['', 'Weighted income bands (% of all employed; categories 1-5 are increasing minimum-wage bands, 6 = no income, 7 = unspecified):', '',
              '| Domain | <=1 minimum | >1-2 | >2-3 | >3-5 | >5 | Zero | Unspecified |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for domain, values in summary['domains'].items():
        lines.append('| ' + domain + ' | ' + ' | '.join(f"{values['weighted_band_shares'][str(i)]['share']:.1%}" for i in range(1, 8)) + ' |')
    lines += ['', '## Calibration acceptance', '',
              'Thresholds are the engineering limits stated in the approved plan: mean/median errors <=10%, fitted percentile errors <=20%, median/P90 cohort drift <=15%, uncertainty transit-share spread <=10 percentage points. No empirical confidence level is implied.', '',
              '| MXN/game unit | Largest fitted percentile error | Maximum income-bound transit spread | Eligible |', '|---|---:|---:|---|']
    for unit, result in summary['acceptance'].items():
        lines.append(f"| {unit} | {summary['fits'][unit]['max_abs_quantile_error']:.1%} | {max(result['income_uncertainty_max_transit_spread'].values()):.1%} | {result['eligible']} |")
    lines += ['', 'Largest uncertainty spreads are shown below. All employed includes completely unspecified income; specified only retains exact, zero and income-band records. These ranges are adversarial endpoint tests conditional on the game income clamps, not probabilistic error bars.', '',
              '| Conversion | All-employed spread | Specified-only spread | Largest cohort-size mode-share change |', '|---|---:|---:|---:|']
    for unit, result in summary['acceptance'].items():
        spread = result['income_uncertainty_max_transit_spread']
        lines.append(f"| {unit} | {spread['all_employed']:.1%} | {spread['specified_only']:.1%} | {result['cohort_max_transit_share_drift']:.1%} |")
    lines += ['', f"Best fit among exact positive respondents: {decision['best_conditional_fit_conversion']} MXN/game unit. Selected qualifying profile: {decision['selected_conversion_mxn_per_game_unit']}. Failed criteria: `{json.dumps(decision['failed_criteria'])}`.", '',
              'For uncertainty sensitivity only, the VM temporarily substitutes 50 weighted income quantiles from lower/upper survey endpoints, then executes the installed mode-choice function with the candidate clamps. Unbounded upper incomes are capped by that candidate maximum. This is a stress test conditional on game bounds, not a live per-pop income feature or a probability statement. Compare all-employed and specified-only spreads in metrics.json. Zero incomes are subject to the game floor; they are not represented as genuine no-income households after clamping.', '',
              '## Controlled worker comparisons', '',
              'Separate unmodified, income-only, hours-only, cost-only and combined variants are tested at both conversions. Each variant includes ordinary/airport/university pops at sizes 25/50/100/200. Baseline costs are the shipped rules interpreted under each declared currency convention; combined profiles use identical real-MXN costs. Special-pop multipliers remain game assumptions, not measured tourist/student earnings.', '',
              '| Variant | Unique fixture evaluations | Conservation | Fare/time monotonic |', '|---|---:|---|---|']
    for name, result in summary['worker_checks'].items():
        lines.append(f"| {name} | {result['evaluations']} | pass | pass |")
    lines += ['', 'Cohort mode-share changes and exact worker/replica errors are in metrics.json. The minimum-transit-group rule can produce larger share changes for smaller groups even when the income distributions are close.', '',
              'Illustrative 8-km trip, 30 perceived transit minutes, MXN 10 fare, 50 ordinary commuters. Local-cost variants use MXN 20 parking and 2.5/km driving; other variants retain shipped costs. Each within-conversion ablation changes only the named rules.', '',
              '| Variant | Transit | Driving | Walking |', '|---|---:|---:|---:|']
    for result in exact['results']:
        candidates = [r for r in result['modes'] if r['distance_m'] == 8000 and r['perceived_transit_minutes'] == 30
                      and r['fare_mxn'] == 10 and r['kind'] == 'ordinary' and r['population'] == 50]
        row = next((r for r in candidates if r['parking_mxn'] == 20 and abs(r['driving_mxn_per_km'] - 2.5) < 1e-9), candidates[0])
        lines.append(f"| {result['name']} | {row['transit']} | {row['driving']} | {row['walking']} |")
    lines += ['', '## Costs, parking and game finances', '',
              'No representative Q4 station panel was obtained: the official resource API returned HTTP 403. The inspected Profeco station price is selected in a high-margin ranking. Fuel price scenarios 23/24/26 MXN/litre and efficiencies 8/12/16 km/litre are explicit assumptions, with nonfuel additions 0/0.5/1 MXN/km. The three worker cost cases span 1.4375, 2.5 and 4.25 MXN/km. They do not estimate a CUR fleet average.', '',
              '[Official municipal fare amounts, August 2024](https://www.cancun.gob.mx/cancun/noticias/leer/1408): 9.50 urban and 12 hotel-zone MXN. [State no-increase statement, November 2025](https://qroo.gob.mx/mara-lezama-descarta-aumento-al-transporte-publico-hasta-que-se-brinde-un-beneficio-tangible-a-la-ciudadania/) supplies later context, not a full observed fare schedule. New-subway fares 5/10/15/20 MXN remain hypothetical.', '',
              '[Airport parking tariff effective February 2025](https://sidof.segob.gob.mx/notas/docFuente/5749041): employee automobile event 43.10 MXN before VAT. This is not an ordinary-city or traveler daily tariff, nor proof that it remained unchanged throughout Q4. Ordinary parking remains 0/20/60 MXN per leg as a scenario.', '',
              'The worker adds parking cost on each directional mode-choice calculation, including the return commute; the airport factor multiplies it again. Real daily parking must not be inserted twice without an explicit conversion. Both fixture directions otherwise use the same commuter assumptions.', '',
              '| Conversion | Daily ticket revenue (game units) | Assumed daily operating cost | Balance |', '|---|---:|---:|---:|']
    for r in summary['finance_fixture']['records']:
        lines.append(f"| {r['mxn_per_game_unit']} | {r['daily_revenue_game_units']:,.0f} | {r['daily_operating_cost_game_units_assumed']:,.0f} | {r['operating_balance_game_units']:,.0f} |")
    lines += ['', 'Financial fixture: fixed 10-km/10-station/8-train service, 100 identical 50-person OD groups and two charged legs. Assumed capital 100 million and daily operating cost 50,000 game units stay constant. This is accounting sensitivity, not actual geometry construction costing or service simulation. At fixed riders and real-MXN fare, conversion 20 produces 1/20 of conversion-1 game revenue, while unchanged game expenses remain constant. Actual same-network financial validation remains pending.', '',
              '## Geographic applicability and decision', '',
              'Current official ENOE boundary coverage was not established. A historical 2002 coverage document is too old to certify Q4 2025 geography. The current Cancun sample includes Benito Juarez and Isla Mujeres records, with blank locality identifiers; those records do not define a polygon. No percentage of CUR coverage is claimed. Cancun and Quintana Roo urban are overlapping comparison domains, never blended.', '',
              'Milestone A has produced an evidence-backed decision, not an activated profile. When the approved uncertainty threshold fails, retain research-only artifacts and do not start the runtime adapter. Next work must narrow/justify the income uncertainty or explicitly revise the modeling objective; improved point-fit accuracy alone does not fix this limitation. Live switching, save/reload, nonempty route recalculation and financial playability remain pending.', '',
              'Reproduce: `python tools/calibrate_cur_economics.py`. Requires the original feasibility metrics/archive, installed game 1.7.2, numpy/scipy/PyYAML and Node. Detailed observations, source retrieval manifest, checksums and fixture outputs accompany this report.']
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf8')
    assumptions = ['# Data assumptions', '',
                   'Reference: ENOE 2025 Q4; quarterly weights; complete-interview usual/new residents aged 15-98, employed. Exact filter and archive hash are inherited from the original feasibility report and checked before use.', '',
                   'Exact positive income is a conditional baseline. Income-band-only records carry the closed interval limits of the official category, with the highest band open above. Unspecified records have no finite upper bound. Lower/upper scenarios are extreme support allocations, not fitted values or estimated nonresponse corrections.', '',
                   'Salary conversion uses the record salary=8,364 (278.80 x 30), not annual days/12. Working hours x 52 is a model assumption; the default-hour ablation is retained. The worker applies final minimum/maximum income clamps.', '',
                   'Cancun is a named proxy for CUR. No verified current survey polygon or CUR residential coverage percentage is available. Sample municipalities are evidence of sampled records, not a spatial extent. The historical INEGI coverage document and another survey (ENSU) cannot establish current ENOE boundaries.', '',
                   'Travel-cost values are either dated official context or explicit scenarios; see transport-cost-assumptions.json. The game cannot represent neighborhood earnings, car access, household income sharing, or bus/colectivo competition through these constants.']
    (output / 'data-assumptions.md').write_text('\n'.join(assumptions) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
