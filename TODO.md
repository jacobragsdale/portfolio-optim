# TODO

## Snapshot retention

Keep 2 to 4 weeks of runs on the snapshot volume. An Airflow task, not built yet, archives older snapshots to the
datalake or Snowflake and deletes them from the volume.
