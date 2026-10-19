"""Demand defaults shared by compilation, Wizard and reference preview."""

# Shared compilation default; the user confirmed the candidate works in-game.
# Leave unspecified modes absent so only explicit legacy choices are persisted.
DEFAULT_OD_ALLOCATION = 'balanced_integer_v1'


def validate_od_allocation_mode(value):
    if value not in ('legacy', 'balanced_integer_v1'):
        raise ValueError(f'Unknown O/D allocation mode: {value}')
    return value


def apply_demand_defaults(config):
    """Enable the current methods unless a project explicitly chooses another.

    Automatic CE inspection creates the source-bound historical contract only
    when usable detail exists; otherwise bounded DENUE priors and a notice apply.
    Geographic or vintage comparability is never inferred from project names.
    """
    city = config.setdefault('city', {})
    macro = config.setdefault('macroeconomics', {})
    demand = config.setdefault('demand', {})
    city.setdefault('residential_placement', 'official_blocks')
    macro.setdefault('residential_employment', 'census_employed')
    macro.setdefault('workplace_employment', 'auto')
    if 'engine' not in demand:
        if ('tasa_pea' in macro or 'til_1_state' in macro or 'default_growth_factor' in macro or macro.get('workplace_employment') == 'legacy'):
            demand['engine'] = 'legacy'
        else:
            demand['engine'] = 'v2'
    return config
