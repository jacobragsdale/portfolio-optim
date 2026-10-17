"""One LP per portfolio. `model` builds the variables and expressions, `objective` and `constraints` are written against
them, `solve` runs HiGHS, `rounding` turns the answer into tradable lots and re-checks it, and `results` is what comes
back."""
