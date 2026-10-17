"""The committed example environment stays in step with `Settings`."""

from pathlib import Path

from optim.settings import Settings


def test_the_example_env_file_sets_every_setting() -> None:
    settings = Settings(_env_file=Path(__file__).parents[1] / ".env.example")
    assert settings.oracle.dsn == "dbhost.example.com:1521/ANALYTICS"
    assert settings.redis_url is None
