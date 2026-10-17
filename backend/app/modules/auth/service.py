from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.identity import Identity, IdentityService
from app.modules.auth.schemas import LoginResponse, RegisterRequest, UserResponse
from app.modules.users.models import User


class DuplicateAccount(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class AuthConfigurationError(RuntimeError):
    pass


_password_hash = PasswordHash.recommended()


def _jwt_key() -> str:
    secret = get_settings().auth_jwt_secret
    if secret is None or len(secret.get_secret_value().encode("utf-8")) < 32:
        raise AuthConfigurationError("AUTH_JWT_SECRET must contain at least 32 bytes")
    return secret.get_secret_value()


def _public_user(user: User) -> UserResponse:
    return UserResponse(id=user.id, username=user.username, email=user.email)


class AuthService:
    @staticmethod
    def register(session: Session, request: RegisterRequest) -> UserResponse:
        encoded_password = _password_hash.hash(request.password)
        user = User(username=request.username, email=str(request.email), password_hash=encoded_password)
        try:
            # Begin before the first query/write; an IntegrityError is rolled back by the
            # context manager and still protects concurrent duplicate registrations.
            with session.begin():
                existing = session.scalar(
                    select(User.id).where(or_(User.username == user.username, User.email == user.email))
                )
                if existing is not None:
                    raise DuplicateAccount("username or email is already registered")
                session.add(user)
                session.flush()
                response = _public_user(user)
            return response
        except IntegrityError as exc:
            # Unique indexes remain the final arbiter if two requests race after the check.
            if getattr(exc.orig, "args", (None,))[0] == 1062:
                raise DuplicateAccount("username or email is already registered") from exc
            raise

    @staticmethod
    def login(session: Session, identifier: str, password: str) -> LoginResponse:
        user = session.scalar(
            select(User).where(or_(User.username == identifier, User.email == identifier.lower()))
        )
        try:
            password_matches = user is not None and _password_hash.verify(password, user.password_hash)
        except UnknownHashError:
            password_matches = False
        if user is None or not user.is_active or not password_matches:
            raise InvalidCredentials("invalid username/email or password")
        key = _jwt_key()
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": str(user.id),
                "iat": now,
                "exp": now + timedelta(minutes=get_settings().auth_jwt_access_token_expire_minutes),
            },
            key,
            algorithm="HS256",
        )
        return LoginResponse(access_token=token, user=_public_user(user))

    @staticmethod
    def user_id_from_token(token: str) -> int:
        try:
            claims = jwt.decode(token, _jwt_key(), algorithms=["HS256"], options={"require": ["sub", "exp"]})
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject.isdecimal() or int(subject) < 1:
                raise InvalidCredentials("invalid bearer token")
            return int(subject)
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidCredentials("invalid bearer token") from exc

    @staticmethod
    def get_active_user(session: Session, token: str) -> User:
        user_id = AuthService.user_id_from_token(token)
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            raise InvalidCredentials("invalid bearer token")
        return user

    @staticmethod
    def get_current_identity(session: Session, user: User) -> Identity:
        return IdentityService.get_identity(session, user)
