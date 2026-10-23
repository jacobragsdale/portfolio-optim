"""optim: one run loop for every portfolio model. A model is a module that declares its `Params` and `Data` and decides
each portfolio's trades in a pure `decide`; optim loads the data, runs the model in parallel, rounds and checks each
decision, and saves every run as a snapshot that replays."""

from importlib.metadata import version

from optim.contract import Datasets, Decision, Metrics, Params, SolverStats
from optim.data.dataset import CachePolicy, Dataset
from optim.schema import Row

__version__ = version("optim")
__all__ = ["CachePolicy", "Dataset", "Datasets", "Decision", "Metrics", "Params", "Row", "SolverStats", "__version__"]
