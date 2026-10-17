"""Every dataset the optimizer loads, one module per subject. Each declares its row schemas and `Dataset`s; the SQL they
run is in `sql/`. `optim.inputs` combines them into what a solve sees."""
