"""Bounded CUR economic feasibility gate; no changes to map/game/save files.

Requires numpy and scipy. Sources and copied installed worker stay in .cache;
only aggregates, candidate settings and the report are written to --output.
Run: python tools/diagnose_cur_economics.py --download
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import urllib.request
import zipfile

import numpy as np
import scipy
from scipy.optimize import differential_evolution
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = ('https://www.inegi.org.mx/contenidos/programas/enoe/15ymas/'
              'datosabiertos/2025/conjunto_de_datos_enoe_2025_4t_csv.zip')
SD = 'conjunto_de_datos_sdem_enoe_2025_4t/'
QUANTILES = np.array([.1, .25, .5, .75, .9, .95])
LABELS = ['p10', 'p25', 'p50', 'p75', 'p90', 'p95']
GAME = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Subway Builder\resources\app.asar')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def weighted_quantiles(values, weights, quantiles=QUANTILES):
    """Inverse empirical CDF, without interpolation across observed incomes."""
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    if (len(values) == 0 or len(values) != len(weights) or np.any(weights <= 0)
            or not np.all(np.isfinite(values)) or not np.all(np.isfinite(weights))):
        raise ValueError('Nonempty finite values and positive weights required')
    order = np.argsort(values, kind='stable')
    cdf = np.cumsum(weights[order]) / weights.sum()
    return values[order][np.minimum(np.searchsorted(cdf, quantiles), len(values) - 1)]


def stats(values, weights):
    if not values:
        return None
    x, w = np.array(values, float), np.array(weights, float)
    mean = np.average(x, weights=w)
    return dict(n=len(x), weighted_population=float(w.sum()), mean=float(mean),
                std=float(np.sqrt(np.average((x - mean)**2, weights=w))),
                kish_effective_n=float(w.sum()**2 / (w @ w)),
                **dict(zip(LABELS, map(float, weighted_quantiles(x, w)))))


def income_status(row):
    income, band = float(row['ingocup']), int(row['ing7c'])
    if income > 0 and income < 999998:
        if band in (0, 6, 7):
            raise ValueError('Positive income conflicts with IN G7C classification')
        return 'positive_exact'
    if income == 0 and band == 6:
        return 'reported_zero'
    if band in range(1, 6):
        return 'band_only_missing_exact'
    return 'unspecified_income'


def survey_domains(archive):
    domains = {name: [] for name in ('cancun_city', 'quintana_roo_urban')}
    with zipfile.ZipFile(archive) as z:
        catalog = z.read(SD + 'catalogos/cd_a.csv').decode('utf-8-sig')
        if '41,Cancún' not in catalog:
            raise ValueError('Cancun code must be verified against archive catalog')
        member = SD + 'conjunto_de_datos/conjunto_de_datos_sdem_enoe_2025_4t.csv'
        with z.open(member) as stream:
            # INEGI ships UTF-8 catalogs alongside a Windows-1252 SDem table.
            rows = csv.DictReader(io.TextIOWrapper(stream, encoding='cp1252', newline=''))
            for row in rows:
                if row['per'].strip() != '425':
                    raise ValueError('Refusing a period other than 2025 Q4')
                if (int(row['r_def']) != 0 or int(row['c_res']) not in (1, 3)
                        or not 15 <= int(row['eda']) <= 98 or int(row['clase2']) != 1):
                    continue
                weight = float(row['fac_tri'])
                if not math.isfinite(weight) or weight <= 0:
                    raise ValueError('Invalid quarterly weight')
                if int(row['cd_a']) == 41:
                    domains['cancun_city'].append(row)
                if int(row['cve_ent']) == 23 and int(row['t_loc_tri']) in (1, 2, 3):
                    domains['quintana_roo_urban'].append(row)
        metadata = {n: hashlib.sha256(z.read(SD + 'catalogos/' + n)).hexdigest()
                    for n in ('cd_a.csv', 't_loc_tri.csv', 'ing7c.csv')}
    output = {}
    for name, rows in domains.items():
        if not rows:
            raise ValueError(f'Empty survey domain: {name}')
        counts = {key: {'n': 0, 'weighted_population': 0.0} for key in
                  ('positive_exact', 'reported_zero', 'band_only_missing_exact', 'unspecified_income')}
        positive, positive_w, known, known_w, hours, hours_w = [], [], [], [], [], []
        for row in rows:
            weight = float(row['fac_tri'])
            status = income_status(row)
            counts[status]['n'] += 1
            counts[status]['weighted_population'] += weight
            if status == 'positive_exact':
                positive.append(float(row['ingocup']))
                positive_w.append(weight)
            if status in ('positive_exact', 'reported_zero'):
                known.append(float(row['ingocup']))
                known_w.append(weight)
            h = float(row['hrsocup'])
            if 0 < h <= 168:
                hours.append(h)
                hours_w.append(weight)
        total = sum(v['weighted_population'] for v in counts.values())
        for bucket in counts.values():
            bucket['weighted_share'] = bucket['weighted_population'] / total
        output[name] = {'employed_n': len(rows), 'employed_weighted': total,
                        'income_status': counts,
                        'positive_monthly_mxn': stats(positive, positive_w),
                        'known_including_zero_monthly_mxn': stats(known, known_w),
                        'positive_weekly_hours_all_employed': stats(hours, hours_w),
                        'hours_zero_or_missing_n': len(rows) - len(hours)}
    return output, metadata


def game_normal_quantile(probability):
    """Installed Acklam approximation; exact branches matter at redistribution."""
    p = np.clip(np.asarray(probability, float), .0001, .9999)
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.357751867269, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572, 1]
    c = [-.007784894002430293, -.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [.007784695709041462, .3224671290700398, 2.445134137142996, 3.754408661907416, 1]
    q = p - .5
    central = np.polyval(a, q*q) * q / np.polyval(b, q*q)
    tail = np.sqrt(-2 * np.log(np.where(p < .5, p, 1-p)))
    outside = np.polyval(c, tail) / np.polyval(d, tail)
    return np.where(p < .02425, outside, np.where(p > .97575, -outside, central))


def game_incomes(params, size=50, multiplier=1):
    """Replica of 1.7.2 generator PLUS the mode-choice caller's final clamp.

    Verified against actual installed JS by the harness; fixed 5000/100000
    adjustments are intentionally in game units, not currency-scaled here.
    """
    mean, std, floor, ceiling = params
    i = np.arange(size, dtype=float)
    z = game_normal_quantile(i / max(size - 1, 1))
    base = np.clip(mean * multiplier + z * std, floor, ceiling)
    jitter = (i * 362436069 % 1_000_000) / 1_000_000
    low = base <= floor + 5000
    alternative = mean * multiplier + game_normal_quantile(.1 + jitter * .85) * std
    bump = (i * 123456789 % 1_000_000) / 1_000_000 * 100000
    adjusted = np.where(low, alternative, np.where(jitter < .1, base + bump, base))
    return np.clip(adjusted, floor, ceiling)


def fit_domain(target, mxn_per_unit):
    observed = np.array([target[k] for k in LABELS]) * 12 / mxn_per_unit
    # Fit all four adjustable income parameters to positive-income respondents.
    # Mean term reduces quantile-only degeneracy. Bounds are search assumptions.
    median = observed[2]
    bounds = [(median * .2, median * 2), (median * .02, median * 2),
              (0, observed[1]), (observed[4], observed[5] * 4)]

    def residual(params):
        sample = game_incomes(params)
        q = weighted_quantiles(sample, np.ones(50))
        errors = (q - observed) / observed
        mean_error = (sample.mean() - target['mean'] * 12 / mxn_per_unit) / (target['mean'] * 12 / mxn_per_unit)
        return float(np.mean(errors**2) + .25 * mean_error**2)

    result = differential_evolution(residual, bounds, seed=42, maxiter=160, popsize=12,
                                    polish=False, tol=1e-8, workers=1)
    params = [round(float(v), 6) for v in result.x]
    rules = dict(zip(('INCOME_MEAN', 'INCOME_STD_DEV', 'MINIMUM_INCOME', 'MAXIMUM_INCOME'), params))
    sample = game_incomes(params) * mxn_per_unit / 12
    q = weighted_quantiles(sample, np.ones(50))
    errors = (q - np.array([target[k] for k in LABELS])) / np.array([target[k] for k in LABELS])
    return {'rules': rules, 'mxn_per_game_unit': mxn_per_unit,
            'fit_population': 'positive exact income respondents; no zero or missing income imputation',
            'objective': float(result.fun), 'optimizer_success': bool(result.success),
            'optimizer_message': str(result.message),
            'quantile_relative_errors': dict(zip(LABELS, map(float, errors))),
            'max_abs_quantile_error': float(np.max(np.abs(errors))),
            'generated_monthly_mxn': stats(list(sample), [1] * 50),
            'search_bounds_game_units': bounds}


def extract_game(asar, cache):
    """Read ASAR directly; copy only into local ignored diagnostic cache."""
    with Path(asar).open('rb') as stream:
        header = struct.unpack('<4I', stream.read(16))
        tree = json.loads(stream.read(header[3]))
        base = 8 + header[1]

        def read(parts):
            node = tree
            for part in parts:
                node = node['files'][part]
            stream.seek(base + int(node['offset']))
            return stream.read(node['size'])

        version = json.loads(read(['package.json']))['version']
        public = tree['files']['dist']['files']['renderer']['files']['public']['files']
        workers = [name for name in public if name.startswith('popCommuteWorker.worker-') and name.endswith('.js')]
        if len(workers) != 1:
            raise ValueError('Ambiguous worker bundle')
        worker_bytes = read(['dist', 'renderer', 'public', workers[0]])
        worker = cache / workers[0]
        worker.write_bytes(worker_bytes)
        main_candidates = []
        for name in public:
            if name.startswith('index-') and name.endswith('.js'):
                data = read(['dist', 'renderer', 'public', name])
                if b'"modifyPathfindingRules"' in data:
                    main_candidates.append((name, data))
        if len(main_candidates) != 1:
            raise ValueError('Ambiguous API bundle')
        name, main_bytes = main_candidates[0]
        main = main_bytes.decode('utf8')
        evidence = {}
        for token in ('"modifyPathfindingRules":', '"pathfindingRules": PATHFINDING_RULES',
                      'function triggerCityLoad(', 'function triggerGameLoaded(', '"getCityCode":'):
            index = main.find(token)
            if index < 0:
                raise ValueError(f'Game evidence missing: {token}')
            evidence[token] = {'line': main[:index].count('\n') + 1,
                               'excerpt': main[index:index + 850]}
        return worker, {'version': version, 'asar_path': str(asar), 'asar_sha256': sha256(asar),
                        'worker_member': 'dist/renderer/public/' + workers[0],
                        'worker_sha256': hashlib.sha256(worker_bytes).hexdigest(),
                        'api_member': 'dist/renderer/public/' + name,
                        'api_sha256': hashlib.sha256(main_bytes).hexdigest(), 'evidence': evidence}


def run_worker(worker, profiles):
    request = json.dumps({'worker': str(worker), 'profiles': profiles})
    result = subprocess.run(['node', str(ROOT / 'tools/cur_economic_worker.cjs')], input=request,
                            text=True, capture_output=True, check=True, timeout=90)
    return json.loads(result.stdout)


def sensitivity_checks(rows):
    fare, time = {}, {}
    for r in rows:
        key = (r['distance_m'], r['parking_mxn'], r['driving_mxn_per_km'])
        fare.setdefault(key + (r['perceived_transit_minutes'],), []).append((r['fare_mxn'], r['transit']))
        time.setdefault(key + (r['fare_mxn'],), []).append((r['perceived_transit_minutes'], r['transit']))
    def violations(groups):
        return sum(any(b[1] > a[1] for a, b in zip(sorted(v), sorted(v)[1:])) for v in groups.values())
    return {'fare_increase_violations': violations(fare), 'time_increase_violations': violations(time),
            'fare_series': len(fare), 'time_series': len(time),
            'passenger_conservation': all(r['driving'] + r['transit'] + r['walking'] == 50 for r in rows),
            'scenario_count': len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ROOT / '.cache/cur-economics/2025trim4_csv.zip')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--asar', type=Path, default=GAME)
    parser.add_argument('--city', type=Path, default=ROOT / 'cities/cancun_riviera_maya.yaml')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/cur-economic-feasibility')
    args = parser.parse_args()
    city = yaml.safe_load(args.city.read_text(encoding='utf8'))
    if (city['city']['code'] != 'CUR' or city.get('demand', {}).get('fixed_cohort_size') != 50
            or city.get('demand', {}).get('exact_cohort_size') is not True):
        raise ValueError('This pilot requires CUR with exact 50-person cohorts; reconfigure diagnostic for other maps')
    cache = ROOT / '.cache/cur-economics'
    cache.mkdir(parents=True, exist_ok=True)
    if args.download and not zipfile.is_zipfile(args.archive):
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            data = response.read()
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ValueError('Official URL did not return ZIP; no period substitution')
        args.archive.write_bytes(data)
    domains, metadata = survey_domains(args.archive)
    worker, game = extract_game(args.asar, cache)
    if game['version'] != '1.7.2':
        raise ValueError('Replica requires reinspection for installed versions other than 1.7.2')
    fits, profiles = {}, []
    for name, domain in domains.items():
        fits[name] = {}
        for conversion in (1, 20):
            print(f'Fitting {name}, MXN/game unit={conversion}', flush=True)
            fit = fit_domain(domain['positive_monthly_mxn'], conversion)
            hours = domain['positive_weekly_hours_all_employed']['mean'] * 52
            fit['rules']['HOURS_WORKED_PER_YEAR'] = round(hours, 6)
            fits[name][str(conversion)] = fit
            profiles.append({'name': f'{name}_mxn{conversion}', 'rules': fit['rules'],
                             'mxn_per_game_unit': conversion})
    exact = run_worker(worker, profiles)
    checks = {}
    for profile, row in zip([{'name': 'unmodified'}] + profiles, exact['results']):
        checks[row['name']] = sensitivity_checks(row['modes'])
        checks[row['name']]['offline_restore'] = row['offline_restore']
        checks[row['name']]['offline_reapply'] = row['offline_reapply']
        if row['name'] != 'unmodified':
            params = [profile['rules'][k] for k in ('INCOME_MEAN', 'INCOME_STD_DEV', 'MINIMUM_INCOME', 'MAXIMUM_INCOME')]
            errors = [float(np.max(np.abs(game_incomes(params, int(n)) - np.asarray(values))))
                      for n, values in row['distributions'].items()]
            checks[row['name']]['replica_max_abs_error_game_units'] = max(errors)
            if max(errors) > .01:
                raise AssertionError('Replica disagrees with installed worker')
        check = checks[row['name']]
        if (not check['passenger_conservation'] or not check['offline_restore'] or not check['offline_reapply']
                or check['fare_increase_violations'] or check['time_increase_violations']):
            raise AssertionError(f'Worker contract failed: {row["name"]}: {check}')
    if not exact['empty_batch_rules_propagation']:
        raise AssertionError('Worker batch did not receive settings')
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    candidate = {'status': 'diagnostic_only_not_approved_for_activation', 'map_code': 'CUR',
                 'geographic_scope': 'Cancun survey-domain proxy for CUR; not a measured Riviera Maya distribution',
                 'period': '2025Q4', 'mxn_per_game_unit': 1,
                 'currency_note': 'One MXN per game unit for commuter economics only; no construction-budget conversion',
                 'income': fits['cancun_city']['1'],
                 'cost_scenarios_mxn': {'fares': [5, 10, 20, 40, 80], 'parking': [0, 20, 60],
                                        'driving_per_km': [1.5, 3, 5]},
                 'cost_evidence': 'Sensitivity assumptions only; no local fuel/parking/fare calibration claimed',
                 'cost_source_lead': {
                     'url': 'https://combustibles.profeco.gob.mx/qqpgasolina/2025/QQPGASOLINA_102025.pdf',
                     'page': 5, 'period': '2025-10-06/2025-10-12',
                     'cancun_station_regular_mxn_per_litre': 24.78,
                     'limitation': 'Station in a high-margin ranking, not a city mean or vehicle cost per km'},
                 'activation_blockers': ['CUR-wide geographic justification', 'local cost evidence',
                                         'live city switch and save/reload verification',
                                         'fare/revenue/construction currency consistency']}
    summary = {'reference_period': '2025Q4', 'repository_commit': commit,
               'generated_at_utc': datetime.now(timezone.utc).isoformat(),
               'runtime_versions': {'numpy': np.__version__, 'scipy': scipy.__version__, 'pyyaml': yaml.__version__,
                                    'node': subprocess.check_output(['node', '--version'], text=True).strip()},
               'diagnostic_sha256': {name: sha256(ROOT / 'tools' / name) for name in
                                     ('diagnose_cur_economics.py', 'cur_economic_worker.cjs')},
               'city_configuration': {'path': str(args.city), 'sha256': sha256(args.city),
                                      'code': city['city']['code'], 'bbox': city['city']['bbox'],
                                      'demand': city['demand']},
               'source': {'url': SOURCE_URL, 'archive_sha256': sha256(args.archive),
                          'archive_bytes': args.archive.stat().st_size, 'catalog_sha256': metadata},
               'survey_definition': {'filter': 'r_def=00; c_res in [1,3]; eda=15..98; clase2=1; fac_tri>0; per=425',
                                     'cancun_city': 'cd_a=41 (verified archive catalog)',
                                     'quintana_roo_urban': 'cve_ent=23; t_loc_tri in [1,2,3] (>=2500 residents)',
                                     'weights': 'fac_tri; no confidence intervals; Kish n is not a design-based precision estimate'},
               'domains': domains, 'fits': fits, 'game': game, 'checks': checks,
               'empty_batch_rules_propagation': exact['empty_batch_rules_propagation'],
               'gates': {'data_suitability': 'YELLOW', 'income_model_fit': 'YELLOW', 'game_integration': 'YELLOW'},
               'live_runtime': {'status': 'PENDING', 'reason': 'No running game/connected runtime inspected; offline VM is not live validation',
                                'pending': ['CUR-other-CUR', 'save/reload', 'mod identity disambiguation',
                                            'first commute timing', 'UI/fare restoration', 'full nonempty route-worker recalculation']}}
    for filename, value in [('metrics.json', summary), ('candidate-profile.json', candidate), ('worker-results.json', exact)]:
        (output / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf8')
    write_report(output, summary, candidate, exact)
    print(json.dumps({'output': str(output), 'gates': summary['gates'], 'checks': checks}, indent=2))


def write_report(output, summary, candidate, exact):
    lines = ['# CUR economic-profile feasibility gate', '',
             '**Overall: YELLOW.** Proceed only to an opt-in engineering pilot after the prerequisites below; do not publish an economic-realism claim.', '',
             '| Gate | Verdict | Evidence |', '|---|---|---|',
             '| Data suitability | YELLOW | Official 2025 Q4 city and state-urban estimates exist; CUR is broader than Cancun and income nonresponse remains. |',
             '| Income-model fit | YELLOW | Measured conditional earnings fit and actual-worker comparison below; game shape and cohort size constrain fit. |',
             '| Game integration | YELLOW | Installed worker executes overrides offline; live city lifecycle and save/reload remain pending. |', '',
             '## Reproduce', '', '```powershell', 'python tools/diagnose_cur_economics.py --download',
             'python -m unittest discover -s tests -p test_cur_economics.py', '```', '',
             'Requires Python numpy/scipy/PyYAML and Node. Use --asar for a nonstandard installation and --city for the same CUR configuration at another path. The diagnostic requires exact 50-person CUR cohorts and fails on a mismatched configuration. No game launch, save mutation, map rebuild or package modification.', '',
             f"Repository base: `{summary['repository_commit']}`. Game: **{summary['game']['version']}**. Source and game hashes and exact bundle evidence locations are recorded in metrics.json.", '',
             '## Official data and geographic scope', '',
             f"[INEGI 2025 Q4 CSV archive]({SOURCE_URL}). The archive period and city catalog are validated, not inferred from the filename alone.", '',
             'Cancun = survey city code 41. Quintana Roo urban = state 23, localities >=2,500 residents (t_loc_tri 1-3). The domains overlap and must not be combined as independent samples. Neither is a measured distribution for the CUR map boundary. No municipal or neighborhood estimates are inferred.', '',
             '| Domain | Employed sample | Weighted employed | Exact positive income | Reported zero | Band only, exact missing | Unspecified |', '|---|---:|---:|---:|---:|---:|---:|']
    for name, domain in summary['domains'].items():
        bucket = domain['income_status']
        cols = [f"{bucket[k]['weighted_share']:.1%} (n={bucket[k]['n']})" for k in bucket]
        lines.append(f"| {name} | {domain['employed_n']} | {domain['employed_weighted']:,.0f} | " + ' | '.join(cols) + ' |')
    lines += ['', 'Monthly MXN below are conditional on **exact positive earnings responses**, not all-worker population estimates. Missing values are not filled with zero; genuine reported zeros are tabulated separately. Known-income including-zero statistics are retained in metrics.json. Nonresponse bias and the high-income tail are unresolved; Kish effective n does not account for survey clustering/strata.', '',
              '| Domain | Mean | Std dev | P10 | P25 | Median | P75 | P90 | P95 | Weekly hours* |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, domain in summary['domains'].items():
        s = domain['positive_monthly_mxn']
        lines.append('| ' + name + ' | ' + ' | '.join(f'{s[k]:,.0f}' for k in ['mean', 'std'] + LABELS)
                     + f" | {domain['positive_weekly_hours_all_employed']['mean']:.1f} |")
    lines += ['', '*Hours use all employed respondents reporting 1-168 weekly hours, not just income respondents. Annual hours = mean x 52 is a model assumption, not measured annual work. Zero/missing hours excluded and counted in metrics.json.', '',
              '## Installed generator and fitting', '',
              'The 1.7.2 worker generates normal quantiles by person index within each pop, clamps them, replaces values <= floor + 5,000 with another quantile, adds up to 100,000 to selected higher incomes, and clamps again in the mode-choice caller. Those fixed amounts cannot be changed through the income constants. Airport/college multipliers are not calibrated here.', '',
              'Therefore currency conversion changes distribution shape. We fit four income constants using deterministic seed 42 and the actual 50-person cohort size in the current CUR configuration. Objective: mean squared relative P10/P25/P50/P75/P90/P95 error plus 0.25 times squared relative mean error. This is an approximate constrained search, not proof of a global optimum.', '',
              '| Target domain | MXN/game unit | Max absolute quantile error | Mean generated MXN/month |', '|---|---:|---:|---:|']
    for name, conversions in summary['fits'].items():
        for conversion, fit in conversions.items():
            lines.append(f"| {name} | {conversion} | {fit['max_abs_quantile_error']:.1%} | {fit['generated_monthly_mxn']['mean']:,.0f} |")
    lines += ['', 'Candidate uses **1 MXN per game unit** for commuter economics, not a market exchange-rate claim. Fixed adjustments then represent 5,000/100,000 MXN yearly rather than 100,000/2,000,000 MXN. This choice keeps monetary inputs readable; it is NOT selected as the best fit: the optimized 20-MXN Cancun fit has a smaller maximum percentile error. Both fits are retained for review. It does not convert construction costs, budgets, fares in existing saves, or network finances. Those require a separate compatibility decision before activation.', '',
              '| Candidate income constant | Game units/year (hours excepted) |', '|---|---:|']
    for key, value in candidate['income']['rules'].items():
        lines.append(f'| {key} | {value:,.3f} |')
    fit = candidate['income']
    lines += ['', '| Percentile | Observed MXN/month | Generated MXN/month | Relative error |', '|---|---:|---:|---:|']
    for key in LABELS:
        lines.append(f"| {key} | {summary['domains']['cancun_city']['positive_monthly_mxn'][key]:,.0f} | {fit['generated_monthly_mxn'][key]:,.0f} | {fit['quantile_relative_errors'][key]:+.1%} |")
    lines += ['', f"Candidate mean: {fit['generated_monthly_mxn']['mean']:,.0f} versus observed {summary['domains']['cancun_city']['positive_monthly_mxn']['mean']:,.0f} MXN/month; standard deviation: {fit['generated_monthly_mxn']['std']:,.0f} versus {summary['domains']['cancun_city']['positive_monthly_mxn']['std']:,.0f}. Mean-matching alone does not establish a good distribution fit. The fitted INCOME_MEAN parameter is the pre-adjustment normal location, not the final population mean."]
    lines += ['', 'Cohort-size sensitivity (same constants; no refit):', '', '| Pop size | Generated median MXN/month | P90 |', '|---|---:|---:|']
    chosen = next(r for r in exact['results'] if r['name'] == 'cancun_city_mxn1')
    for n, values in chosen['distributions'].items():
        q = weighted_quantiles(np.asarray(values) / 12, np.ones(len(values)))
        lines.append(f'| {n} | {q[2]:,.0f} | {q[4]:,.0f} |')
    lines += ['', '## Offline worker verification and sensitivity', '',
              'The harness reads and executes the actual installed worker in an isolated Node VM. It does not redistribute the game code. Python generator outputs are checked against that worker at sizes 25, 50, 100, 200 and 10,000. The empty-batch entry point verifies override receipt/merge, not a nonempty RAPTOR journey or running-game message delivery.', '',
              '| Profile | Cases | Fare violations | Time violations | Conserves people | Replica max error (game units) |', '|---|---:|---:|---:|---|---:|']
    for name, check in summary['checks'].items():
        lines.append(f"| {name} | {check['scenario_count']} | {check['fare_increase_violations']} | {check['time_increase_violations']} | {check['passenger_conservation']} | {check.get('replica_max_abs_error_game_units', 0):.6f} |")
    lines += ['', 'Fixtures: 1/8/20 km; 30 km/h base driving, 1.25 driving multiplier; 15/30/45/60 perceived transit minutes; 5/10/20/40/80 MXN fares; 0/20/60 MXN parking; 1.5/3/5 MXN per km driving. These are deliberately broad assumptions, not verified Cancun tariffs or vehicle operating costs. Transit inputs are already perceived time, not raw train-only time.', '',
              'The unmodified baseline retains all shipped rules and uses the same numeric fares/times; parking/driving scenario labels are ignored for that baseline (repeated controls). Profile comparisons mix income, annual hours, and costs and cannot isolate one causal effect. worker-results.json contains all counts; no aggregate ridership or mode-share forecast is claimed.', '',
              'Cost-source check: [Profeco, 20 October 2025, page 5](https://combustibles.profeco.gob.mx/qqpgasolina/2025/QQPGASOLINA_102025.pdf) reports a Cancun station at 24.78 MXN/litre regular gasoline for 6-12 October. It is selected in a high-margin ranking, so it cannot establish a representative city price. A fuel price also needs a vehicle-efficiency assumption to become MXN/km. No representative parking schedule or full vehicle-cost estimate was established in this gate; those inputs remain sensitivity scenarios.', '',
              'Offline restoration/reapplication uses full detached default snapshots in a fresh VM and is asserted separately. It is not a test of an implemented mod or a real city switch.', '',
              '## Integration evidence and pending requirements', '',
              '[Supported constants API](https://www.subwaybuilder.com/docs/api-reference/constants#modifypathfindingrules) and [lifecycle hooks](https://www.subwaybuilder.com/docs/api-reference/hooks). The installed renderer merges overrides; commute-batch messages include PATHFINDING_RULES; the worker applies them before processing. onCityLoad and onGameLoaded callbacks exist. utils.getCityCode returns a code; identity/owner collision handling must be validated before targeting CUR.', '',
              'No live game was connected during this run. **PENDING:** CUR -> other city -> CUR; save/reload; first-commute ordering; automatic recalculation; nonempty routing batch; UI currency/fare consistency; restoring only owned settings without overwriting another mod. API existence and offline merging do not certify these behaviors.', '',
              '## Decision and next prerequisites', '',
              'Proceed to a small opt-in engineering pilot only after explicitly accepting the Cancun proxy for CUR or supplying a defensible regional estimate, and after inspecting local fare/fuel/parking evidence. Keep both domain fits separate. Retain original game defaults when disabled.', '',
              'Before distribution: verify real lifecycle/worker delivery in a disposable save; resolve monetary-unit effects on fares/revenue/construction; validate normal, airport and student populations; quantify the effect of income nonresponse and fixed generator shape. A richer distribution may require game support. Neighborhood inequality, private-car availability, buses/colectivos and real modal competition remain outside this model.', '',
              'Artifacts: metrics.json (provenance/statistics/checks), candidate-profile.json (inactive assumptions), worker-results.json (actual offline worker outputs).']
    lines += ['', '## Illustrative commuter response', '',
              '8-km case: profile assumptions are 20 MXN parking and 3 MXN/km driving. Each count is out of 50. Baseline keeps shipped parking/driving costs and reads fare numbers in its original game units. These examples are not predictions for a real CUR line.', '',
              '| Profile | Perceived transit minutes | Fare number | Transit | Driving | Walking |', '|---|---:|---:|---:|---:|---:|']
    for name in ('unmodified', 'cancun_city_mxn1'):
        result = next(r for r in exact['results'] if r['name'] == name)
        for row in result['modes']:
            if (row['distance_m'] == 8000 and row['parking_mxn'] == 20 and row['driving_mxn_per_km'] == 3
                    and row['perceived_transit_minutes'] in (30, 45) and row['fare_mxn'] in (10, 40, 80)):
                lines.append(f"| {name} | {row['perceived_transit_minutes']} | {row['fare_mxn']} | {row['transit']} | {row['driving']} | {row['walking']} |")
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
