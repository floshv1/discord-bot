import pathlib

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


def load_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


def load_all_migrations() -> str:
    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return "\n".join(p.read_text() for p in paths)
