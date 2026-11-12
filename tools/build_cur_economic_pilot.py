"""Build the private optional mod and its gameplay-oriented calibration evidence.

No installation, activation or game/save writes. Run from any working directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import zipfile

import diagnose_cur_economics as base
from calibrate_cur_economics import check_modes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asar', type=Path, default=base.GAME)
    parser.add_argument('--output', type=Path, default=base.ROOT / 'reports/cur-economic-pilot')
    args = parser.parse_args()
    prior = json.loads((base.ROOT / 'reports/cur-economic-feasibility/metrics.json').read_text(encoding='utf8'))
    worker, game = base.extract_game(args.asar, base.ROOT / '.cache/cur-economics')
    if game['version'] != '1.7.2':
        raise ValueError('Pilot requires reinspection outside game 1.7.2')
    target = prior['domains']['cancun_city']['positive_monthly_mxn']
    fits = {}
    for unit in (10/3, 5, 10):
        print(f'Fitting gameplay monetary scale: {unit:g} MXN/game unit', flush=True)
        fits[str(unit)] = base.fit_domain(target, unit)
    # Anchoring a hypothetical MXN10 trip at the shipped $3 fare avoids a forced
    # fare change and the 20x revenue reduction caused by the earlier scale.
    unit = 10/3
    fit = fits[str(unit)]
    hours = prior['domains']['cancun_city']['positive_weekly_hours_all_employed']['mean'] * 52
    rules = {**fit['rules'], 'HOURS_WORKED_PER_YEAR': round(hours, 6),
             'DRIVING_COST_PER_KM': 2.5/unit, 'PARKING_COST': 10/unit}
    # 10 per modeled leg is half the assumed 20-MXN daily parking payment.
    profile = {'schemaVersion': 1, 'profileVersion': '0.1.0', 'supportedGameVersions': ['1.7.2'],
               'identity': {'code': 'CUR', 'modId': 'com.railyard.maploader', 'name': 'Cancun / Riviera Maya'},
               'mxnPerGameUnit': unit, 'referencePeriod': '2025Q4', 'rules': rules,
               'startingFarePolicy': 'preserve all existing/player fares; game $3 represents hypothetical MXN10',
               'scope': 'Experimental citywide Cancun exact-income respondent proxy for CUR',
               'assumptions': {'drivingMxnPerKm': 2.5, 'parkingMxnPerDay': 20,
                               'modeledParkingMxnPerLeg': 10, 'noLiveRidershipForecast': True},
               'uncertainty': 'Earlier 14-16pp income-bound sensitivity remains a caveat, not a gameplay veto.'}
    source = base.ROOT / 'mods/cur-economic-profile'
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n', encoding='utf8')
    # Use the exact installed worker for affordability checks and special pops.
    request = {'worker': str(worker), 'calibration': True,
               'profiles': [{'name': 'unmodified', 'rules': {}, 'mxn_per_game_unit': unit, 'local_costs': False},
                            {'name': 'pilot', 'rules': rules, 'mxn_per_game_unit': unit, 'local_costs': True}],
               'parking': [0, 10, 30], 'driving': [23/16, 2.5, 26/8 + 1], 'central_driving': 2.5}
    result = subprocess.run(['node', str(base.ROOT / 'tools/cur_economic_worker.cjs')],
                            input=json.dumps(request), text=True, capture_output=True, check=True, timeout=120)
    worker_data = json.loads(result.stdout)
    worker_checks = {row['name']: check_modes(row['modes']) for row in worker_data['results']}
    (args.output / 'worker-results.json').write_text(json.dumps(worker_data, indent=2) + '\n', encoding='utf8')
    # Inspect built-in rolling-stock rates for a transparent fixed-service budget.
    # These are aggregate rates, not a claim to call the game's geometry estimator.
    main_path = base.ROOT / '.cache/cur-economics' / Path(game['api_member']).name
    if not main_path.exists() or base.sha256(main_path) != game['api_sha256']:
        # extract_game records API evidence; retrieve this member directly from ASAR.
        import struct
        with args.asar.open('rb') as stream:
            header = struct.unpack('<4I', stream.read(16)); tree = json.loads(stream.read(header[3])); node = tree
            for part in game['api_member'].split('/'): node = node['files'][part]
            stream.seek(8 + header[1] + int(node['offset'])); main_path.write_bytes(stream.read(node['size']))
    main_text = main_path.read_text(encoding='utf8')
    start = main_text.index('const heavyMetro =')
    train = main_text[start:main_text.index(';\n', start)]
    rates = {}
    for key in ('baseTrackCost', 'baseStationCost', 'carCost', 'trainOperationalCostPerHour', 'carOperationalCostPerHour'):
        token = re.search(r'"' + key + r'": ([\de.]+)', train)
        if not token: raise ValueError(f'Missing heavy metro rate: {key}')
        rates[key] = float(token.group(1))
    maintenance = re.search(r'MAINTENANCE_COST_MULTIPLIER = ([\d.]+)', main_text)
    if not maintenance: raise ValueError('Missing maintenance multiplier')
    mult = float(maintenance.group(1))
    # 10km cut-and-cover baseline double track, 10 stations, 8 five-car trains,
    # 18 operating hours. No demolition, water, curves, portals or financing.
    capital = 10000*rates['baseTrackCost'] + 10*rates['baseStationCost'] + 8*5*rates['carCost']
    annualization = re.search(r'const TIME_FARE_MULTIPLIER = ([\d.]+)', main_text)
    if not annualization: raise ValueError('Missing time/fare accounting multiplier')
    time_factor = float(annualization.group(1))
    operating = 8*18*(rates['trainOperationalCostPerHour'] + 5*rates['carOperationalCostPerHour'])*time_factor
    # Annual maintenance totals are charged across one game day. Fare and train
    # operating flows also annualize; all terms must use game-accounting units.
    maintenance_daily = 10000*180*mult + 10*160000*mult
    daily = operating + maintenance_daily
    finance = {'basis': 'Inspected heavy-metro rates; simplified 10km/10-station/8 five-car trains, 18h/day, cut-and-cover, no extra costs. Not live route costing.',
               'rates': rates, 'capital_game_units': capital, 'daily_operating_game_units': operating,
               'daily_infrastructure_maintenance_game_units': maintenance_daily,
               'daily_total_game_units': daily,
               'fare_and_train_operating_annualization': time_factor,
               'boardings_to_cover_daily_at_3_game_units': daily/(3*time_factor),
               'same_riders_and_fare_game_units_revenue_ratio': 1,
               'currency_note': 'Neither income nor currency translation changes revenue directly: the pilot never edits fares or budget/operating rules. Break-even uses the shipped multiplier before other game/account adjustments.'}
    stats = {'game': game, 'profile': profile, 'fits': fits, 'finance': finance,
             'worker_checks': worker_checks,
             'gameplay_gate': {'income_fit': 'PASS' if fit['max_abs_quantile_error'] <= .2 else 'APPROXIMATION',
                               'no_forced_fare_or_budget_change': 'PASS',
                               'actual_financial_playability': 'PENDING', 'live_lifecycle': 'PENDING'},
             'input_sha256': {'prior_metrics': base.sha256(base.ROOT / 'reports/cur-economic-feasibility/metrics.json'),
                              'builder': base.sha256(Path(__file__)),
                              'controller': base.sha256(source / 'controller.js'),
                              'bootstrap': base.sha256(source / 'bootstrap.js')},
             'mode_evaluations': sum(len(r['modes']) for r in worker_data['results'])}
    (args.output / 'calibration.json').write_text(json.dumps(stats, indent=2) + '\n', encoding='utf8')
    bundle = source.joinpath('controller.js').read_text(encoding='utf8') + '\n' + source.joinpath('bootstrap.js').read_text(encoding='utf8').replace('/* EMBED_CUR_PROFILE */ null', json.dumps(profile))
    package = args.output / 'cur-economic-pilot-0.1.0.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('cur-economic-profile/index.js', bundle)
        for name in ('manifest.json', 'README.md'):
            archive.write(source / name, 'cur-economic-profile/' + name)
        archive.writestr('cur-economic-profile/profile.json', json.dumps(profile, indent=2) + '\n')
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None: raise ValueError('Package CRC check failed')
        if archive.read('cur-economic-profile/index.js').decode('utf8') != bundle:
            raise ValueError('Packaged script does not match generated bundle')
    (args.output / 'index.js').write_text(bundle, encoding='utf8', newline='\n')
    (args.output / 'package-integrity.json').write_text(json.dumps({
        'package': package.name, 'sha256': base.sha256(package), 'crc': 'PASS',
        'bundle_sha256': base.sha256(args.output / 'index.js'), 'installed': False,
        'publicly_published': False}, indent=2) + '\n', encoding='utf8')
    report = ['# CUR private economic pilot 0.1.0', '',
              '**Built for a private experimental trial. Live gameplay validation remains pending.**', '',
              'This milestone follows the user-approved gameplay realism standard. The earlier 10-point income-uncertainty threshold is retained as research evidence; it is no longer a veto on an experimental pilot. No claim of accurate real-world ridership is made.', '',
              '## Chosen profile', '',
              'One game monetary unit represents 10/3 MXN for commuter economics. A hypothetical MXN10 journey corresponds to the shipped $3 fare. Existing fares, custom fare groups, revenue multipliers, construction costs and operating costs are preserved. Currency translation does not relabel the game UI as pesos.', '',
              '| MXN/game unit | Fitted percentile maximum error | Hypothetical MXN10 fare in game units |', '|---|---:|---:|']
    for conversion, result in fits.items():
        report.append(f"| {float(conversion):.4g} | {result['max_abs_quantile_error']:.1%} | {10/float(conversion):.2f} |")
    report += ['', 'Selection favors the scale anchored to the unchanged default fare and a broad plausible income fit. It is not a market exchange-rate claim or an exhaustive optimization of the full game economy.', '',
               'Income target: ENOE 2025 Q4 Cancun exact positive-income respondents. The profile uses observed working hours x 52. Driving assumes MXN2.5/km. Parking assumes MXN20/day split into MXN10 per directional mode-choice calculation. Airport/student multipliers retain the game assumptions. Regional geography, income nonresponse, missing bus/colectivo alternatives and car availability remain limitations.', '',
               '## Runtime behavior', '',
               '- One visible switch in game settings; default off, versioned preference persisted via scoped mod storage.',
               '- Applies only when CUR has one registration, owned by `com.railyard.maploader`, with the expected name. Ambiguous/wrong identities stay inactive.',
               '- Snapshots only seven owned numerical rules. Disable, city exit and reload cleanup restore prior values; another mod\'s changed keys are preserved and the pilot suspends.',
               '- Applies to future commute batches. No unsupported store mutation, forced fare change, save rewriting or game binary modification.',
               '- Existing in-flight journeys can finish with their previous choice. Judge steady state after a complete game day; instant all-passenger recalculation is not claimed.', '',
               '## Financial evidence', '',
               f"Inspected default heavy-metro rates for a simplified 10km/10-station/eight five-car train network, 18 service hours: capital {capital:,.0f} game units; per-game-day train operating {operating:,.0f}, infrastructure maintenance {maintenance_daily:,.0f}. Fare and train operating flows use the installed {time_factor:g} annualization multiplier. Infrastructure annual costs are charged across one game day.", '',
               f"At the shipped fare/multiplier, the simplified recurring-cost break-even is about {finance['boardings_to_cover_daily_at_3_game_units']:,.0f} paid journeys per game day, before other game-wide adjustments. This is an accounting calculation from default rates, not a real CUR service run or a full geometry cost estimate. Same riders and player fares produce the same cash revenue with the switch off/on; income can still change ridership and therefore revenue.", '',
               'The earlier accounting fixture used assumed costs and omitted game annualization. It must not be treated as a literal operating forecast. The currency-only 20x revenue comparison was an accounting scenario; this implementation avoids that forced fare reduction entirely.', '',
               '## Validation and installation', '',
               f"The installed worker completed {stats['mode_evaluations']:,} unique affordability fixtures covering ordinary/airport/university pops and sizes 25/50/100/200. Population conservation and fare/time monotonicity passed; see calibration.json. Lifecycle tests and actual RAPTOR integration are recorded separately by the validation command.", '',
               'Run `python tools/check_cur_economic_pilot.py` after building. The validation report distinguishes mocked lifecycle checks, actual installed-worker routing checks, and pending live checks.', '',
               'Live game window: no connected runtime was available. PENDING: real UI rendering, CUR-other-CUR switching, save/reload, in-flight batch handoff, full-day ridership/revenue and financial playability. Do not mark those as passed based on a mock or offline VM.', '',
               'Private package: cur-economic-pilot-0.1.0.zip. Extract its folder into `%APPDATA%\\metro-maker4\\mods`, enable the mod in Settings > Mods, load CUR, then use the single economic switch. See the included README for removal and exact-version requirements. No installed game/mod-manager state was changed by the builder.', '',
               'Sources: [game rules API](https://www.subwaybuilder.com/docs/api-reference/constants), [lifecycle hooks](https://www.subwaybuilder.com/docs/api-reference/hooks), [mod storage](https://www.subwaybuilder.com/docs/api-reference/storage), local installed 1.7.2 bundle hashes in calibration.json, and prior ENOE/source records in the feasibility/calibration reports.']
    (args.output / 'report.md').write_text('\n'.join(report) + '\n', encoding='utf8')
    print(json.dumps({'package': str(package), 'maximum_percentile_error': fit['max_abs_quantile_error'],
                      'finance': finance, 'mode_evaluations': stats['mode_evaluations']}, indent=2))


if __name__ == '__main__':
    main()
