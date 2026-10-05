"""Demand defaults shared by compilation, Wizard and reference preview."""


def apply_demand_defaults(config):
    """Enable the current methods unless a project explicitly chooses another.

    Automatic CE inspection creates the source-bound historical contract only
    when usable detail exists; otherwise bounded DENUE priors and a notice apply.
    Geographic or vintage comparability is never inferred from project names.
    """
    city = config.setdefault('city', {})
    macro = config.setdefault('macroeconomics', {})
    city.setdefault('residential_placement', 'official_blocks')
    macro.setdefault('residential_employment', 'census_employed')
    macro.setdefault('workplace_employment', 'auto')
    return config
