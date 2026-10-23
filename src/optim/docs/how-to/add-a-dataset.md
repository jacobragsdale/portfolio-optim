# How to add a dataset

Give a model another table to read, such as issuer credit scores from Oracle or a per-portfolio restricted list from
SQL Server. A dataset is declared once, with its row model, source, query, key and cache lifetime, and the model reads
it as a typed attribute of its `Data`.

Prerequisites:

- A model repo, with the model's tests passing.
- The query, and, for a live run, read access to the source and its `OPTIM_*` settings (see the
  [settings reference](../reference.md#settings)). The fixture lets you develop without them.

To add a dataset:

1. Decide whose it is. A dataset only this model reads goes in the model's own `datasets.py`. One that two or more
   models read belongs in `optim.datasets`, which the package's owner reviews: ask them for it, and declare it in the
   model until it lands.
2. If you need another column of the shared holdings or securities, do not copy the shared dataset. Declare a dataset
   of your own with the same key, such as `security_id`, and join it in `prepare`, as the cvxpy example joins `signals`.
3. Write the row model in the model's `datasets.py`. Field names are the column names the query returns. Document each
   field with a docstring under it, and put every rule in the annotation: rows are validated a column at a time, so a
   row model with a `@field_validator` is refused.

   ```python
   from datetime import timedelta
   from typing import Annotated

   from optim import Dataset, Row
   from pydantic import Field


   class IssuerScore(Row):
       issuer: str
       """The issuer, named as the securities name it."""
       score: Annotated[float, Field(ge=0, le=100)]
       """The issuer's internal credit score; higher is safer."""
   ```

   A field is a `float`, `int`, `str`, `bool`, `date` or `Literal[...]`, or any of them `| None` where a value may be
   missing. Every number is a float or an int: a driver's `Decimal` becomes a float as rows are validated. `optim.schema`
   has field types such as `Id`, `Cusip` and `Rating`. A row with a `portfolio_id` field is read per portfolio: in
   batches of portfolio ids, and sliced to each portfolio before the model sees it.
4. Write the SQL beside it, in `sql/issuer_scores.sql`, aliasing each column to its field name. Every query takes only
   the business date and, for a per-portfolio dataset, the batch's portfolio ids, a JSON array:

   | Source | Date | Portfolio ids |
   |---|---|---|
   | Oracle | `:as_of_date` | `IN (SELECT id FROM JSON_TABLE(:portfolios, '$[*]' COLUMNS (id VARCHAR2(64) PATH '$')))` |
   | SQL Server | `%(as_of_date)s` | `IN (SELECT id FROM OPENJSON(%(portfolios)s) WITH (id VARCHAR(64) '$'))` |

   Oracle refuses a bind its SQL does not use, so an Oracle query must use `:as_of_date`, and a per-portfolio one
   `:portfolios` too. Never let a param change the query: select rows after loading instead, so a sweep can run every
   variant from one snapshot.
5. Declare the dataset beside its row model. The example's `datasets.py` already defines `SQL`, its `sql/` directory:

   ```python
   ISSUER_SCORES = Dataset("issuer_scores", IssuerScore, source="oracle", query=SQL / "issuer_scores.sql", key=("issuer",), cache=timedelta(hours=12))
   ```

   `source` is `"oracle"`, `"mssql"`, `"rest"` (whose `query` is the API's path) or `"file"`. The `key` columns must be
   unique; a per-portfolio dataset's key includes `portfolio_id`. Leave out `cache` to read the dataset live every run:
   a dataset that changes during the day, or is read per portfolio, should not be cached. `check=` takes a function of
   the frame that raises `ValueError` for rules across rows, such as weights that must sum to 1.
6. Add it to the model's `Data`: `issuer_scores = ISSUER_SCORES`. It is then `data.issuer_scores`, a `DataFrame`, in
   `prepare`, and `portfolio.issuer_scores` in `prepare_portfolio`, whole, or sliced to the portfolio when it is read
   per portfolio.
7. Add a CSV file of a few rows for it to the fixture, `tests/MODEL/fixture/issuer_scores.csv`, with a header row of the
   field names; an empty cell is null. The contract test fails until every dataset has a file.
8. Use it in `prepare` or a rule, and test that use on a few rows built with `optim.testing.frame(ISSUER_SCORES, {...})`.

To confirm it worked, run `uv run pytest`: the `fixture[MODEL]` test loads the new file through the row model and
decides every portfolio with it. `uv run optim describe MODEL` then lists `issuer_scores` under `datasets`, with its
columns.

A snapshot saved before the dataset existed has no file for it, so it cannot be replayed with the new code.

To try a dataset that no system holds yet, or a what-if, run with `--data DIRECTORY`, which reads each dataset that has a
file there, such as `issuer_scores.csv`, and reads the others from their sources, with their settings. For every field
of `Dataset`, see the [reference](../reference.md#optimdataset).
