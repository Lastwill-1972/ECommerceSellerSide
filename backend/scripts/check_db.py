"""Run from backend: python -m scripts.check_db. Read-only MySQL connectivity check."""

import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine


def main() -> int:
    engine = None
    try:
        engine = get_engine()
        with engine.connect() as connection:
            result = connection.execute(text("SELECT VERSION(), DATABASE(), 1"))
            version, database_name, connectivity_probe = result.one()
            assert connectivity_probe == 1
        # These values are safe to report; never print the URL or driver exception text.
        print(f"MySQL connection OK (SELECT 1); server_version={version}; database={database_name}")
        return 0
    except ValueError as exc:
        print(f"Database configuration missing: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        # Avoid printing connection strings or credentials from driver errors.
        print(f"MySQL connection failed ({type(exc).__name__}); check service and .env", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
