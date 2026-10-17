from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.core.permissions import AccessDenied, AuthorizationService, OrderAction
from app.services.identity import Identity, IdentityConflict, IdentityEligibilityError
from app.modules.auth.service import AuthService, InvalidCredentials
from app.modules.users.models import User

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    # One request-scoped Session. FastAPI caches the dependency within the request.
    # The top-level service owns begin/commit; closing rolls back uncommitted work.
    with get_session_factory()() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: DbSession,
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        return AuthService.get_active_user(session, credentials.credentials)
    except (InvalidCredentials, RuntimeError) as exc:
        # Missing/weak JWT configuration fails closed with the same public response.
        raise unauthorized() from exc


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_identity(user: CurrentUser, session: DbSession) -> Identity:
    try:
        return AuthService.get_current_identity(session, user)
    except IdentityConflict as exc:
        raise HTTPException(status_code=403, detail="Conflicting user identity relationships") from exc
    except IdentityEligibilityError as exc:
        raise unauthorized() from exc


CurrentIdentity = Annotated[Identity, Depends(get_current_identity)]


def require_platform_admin(identity: CurrentIdentity) -> Identity:
    try:
        AuthorizationService.require_platform_admin(identity)
        return identity
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail="Insufficient permissions") from exc


def require_merchant_management(merchant_id: int, identity: CurrentIdentity) -> Identity:
    try:
        AuthorizationService.check_merchant_management(identity, merchant_id)
        return identity
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail="Insufficient permissions") from exc


def require_shop_access(shop_id: int, identity: CurrentIdentity, session: DbSession) -> Identity:
    try:
        AuthorizationService.check_shop(session, identity, shop_id)
        return identity
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail="Insufficient permissions") from exc


def require_shop_management(shop_id: int, identity: CurrentIdentity, session: DbSession) -> Identity:
    try:
        AuthorizationService.check_shop_management(session, identity, shop_id)
        return identity
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail="Insufficient permissions") from exc


def require_order_action(action: OrderAction):
    """Bind the server-side action so a client query cannot downgrade its permission."""
    try:
        bound_action = OrderAction(action)
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported order action") from exc

    def dependency(shop_id: int, identity: CurrentIdentity, session: DbSession) -> Identity:
        try:
            AuthorizationService.check_order_action(session, identity, shop_id=shop_id, action=bound_action)
            return identity
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail="Insufficient permissions") from exc

    return dependency
