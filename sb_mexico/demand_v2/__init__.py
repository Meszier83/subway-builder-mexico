"""Candidate demand engine. Statistical evidence never becomes game display state."""
from .engine import build_demand
from .request import DemandRequest, prepare_request

__all__ = ['DemandRequest', 'prepare_request', 'build_demand']
