"""Every dataset the optimizer loads, one module per subject. Each declares its row schemas and `Dataset`s; the SQL they
run is in `sql/`. `optim.inputs` combines them into what a solve sees."""

import importlib
import pkgutil

from optim.data.loader import Dataset


def all_datasets() -> list[Dataset]:
    """Every `Dataset` declared in this package, so `optim warm` finds a new one without a list to keep in step."""
    modules = [importlib.import_module(f"{__name__}.{info.name}") for info in pkgutil.iter_modules(__path__)]
    return list(dict.fromkeys(value for module in modules for value in vars(module).values() if isinstance(value, Dataset)))
