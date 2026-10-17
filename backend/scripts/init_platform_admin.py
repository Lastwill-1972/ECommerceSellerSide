"""Explicitly grant platform-admin status to one existing, eligible account."""

import argparse
import sys

from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.users.models import User
from app.services.identity import IdentityConflict, IdentityService, Role


def initialize_platform_admin(username: str, confirm_username: str, *, session_factory=None) -> str:
    if not username or username != confirm_username:
        raise ValueError("confirmation must exactly match --username")

    factory = session_factory or get_session_factory()
    with factory() as session:
        with session.begin():
            # First read is a current read and locks the identity root.
            user = session.scalar(
                select(User)
                .where(User.username == username)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if user is None or user.username != username:
                raise LookupError("exact username was not found")
            if not user.is_active:
                raise ValueError("inactive account cannot become a platform administrator")
            try:
                identity = IdentityService.get_identity(session, user)
            except IdentityConflict as exc:
                raise ValueError("account has conflicting platform or tenant identities") from exc
            if identity.role is Role.PLATFORM_ADMIN:
                return f"Platform administrator already initialized for username: {user.username}"
            if identity.role is not Role.NONE:
                raise ValueError("account already has a merchant or shop identity")
            user.is_platform_admin = True
            session.flush()
            result = user.username
    return f"Platform administrator initialized for username: {result}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="exact username of an existing account")
    parser.add_argument("--confirm-username", required=True, help="repeat the exact username to confirm")
    args = parser.parse_args(argv)
    try:
        print(initialize_platform_admin(args.username, args.confirm_username))
        return 0
    except (LookupError, ValueError) as exc:
        print(f"Platform administrator initialization refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Avoid printing database exceptions, connection URLs, query parameters, or credentials.
        print(f"Platform administrator initialization failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
