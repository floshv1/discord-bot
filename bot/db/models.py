import pathlib

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def load_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()
