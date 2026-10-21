"""Every dataset the optimizer loads, one module per subject. Each declares its row schemas and `Dataset`s; the SQL they
run is in `sql/`. `MARKET` and `PER_PORTFOLIO` list what a run reads, `load` loads them, and `optim.prep` combines them
into what a solve sees.

TODO(port): these datasets are placeholders, with made-up tables and columns. Replace them with the old system's
queries, keeping one `Dataset` per query, and delete what the port does not use."""

import importlib
import pkgutil

from optim.data.dataset import Dataset
from optim.datasets.benchmarks import BENCHMARKS
from optim.datasets.controls import CONTROLS
from optim.datasets.portfolios import CASH_FLOWS, HOLDINGS, SLEEVES
from optim.datasets.securities import SECURITIES, UNIVERSE
from optim.datasets.signals import SIGNALS

# TODO(port): the old system's per-security datasets join this tuple, and are merged in `optim.prep.market`.
MARKET = (SECURITIES, UNIVERSE, SIGNALS, BENCHMARKS)  # loaded once a run, for its business date
# TODO(port): the old system's per-portfolio datasets join this tuple, are loaded in `load._load_batch`, and are fields
# of `Portfolio`, named as the dataset is.
PER_PORTFOLIO = (SLEEVES, HOLDINGS, CONTROLS, CASH_FLOWS)  # each portfolio's rows, beside its `PORTFOLIOS` row


def all_datasets() -> list[Dataset]:
    """Every `Dataset` declared in this package, so `optim warm` finds a new one without a list to keep in step."""
    modules = [importlib.import_module(f"{__name__}.{info.name}") for info in pkgutil.iter_modules(__path__)]
    return list(dict.fromkeys(value for module in modules for value in vars(module).values() if isinstance(value, Dataset)))
