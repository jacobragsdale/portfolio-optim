"""Loading machinery: row schemas to typed DataFrames (`frames`), what declares a dataset (`dataset`), datasets fetched
through their sources (`loader`), and the Redis cache every Job shares (`cache`). `frames` and `dataset` do no I/O, so
prep and the solve may import them; `loader` and `cache` are for the parent alone. The datasets themselves are declared
in `optim.datasets`."""
