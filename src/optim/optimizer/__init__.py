"""One solve per portfolio, all its sleeves together, an integer program by default or an LP or QP. `model` builds the
variables and expressions, `objective` and `constraints` are written against them, `solve` runs the strategy's solver
through its `backends` entry, `rounding` makes the trades exact, `acceptance` rebuilds the result from them and decides
whether it may trade, and `results` is what comes back."""
