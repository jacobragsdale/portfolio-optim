"""One solve per portfolio, an integer program by default or an LP. `model` builds the variables and expressions,
`objective` and `constraints` are written against them, `solve` runs HiGHS, `rounding` makes the trades exact and
re-checks every constraint, and `results` is what comes back."""
