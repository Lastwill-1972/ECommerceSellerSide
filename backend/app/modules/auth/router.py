from fastapi import APIRouter, HTTPException, status
from app.api.deps import CurrentIdentity, CurrentUser, DbSession, unauthorized
from app.modules.auth.schemas import CurrentUserResponse, LoginRequest, LoginResponse, RegisterRequest, UserResponse
from app.modules.auth.service import AuthConfigurationError, AuthService, DuplicateAccount, InvalidCredentials

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, session: DbSession) -> UserResponse:
    try:
        return AuthService.register(session, request)
    except DuplicateAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, session: DbSession) -> LoginResponse:
    try:
        return AuthService.login(session, request.identifier, request.password)
    except InvalidCredentials as exc:
        raise unauthorized() from exc
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Authentication service is not configured") from exc


@router.get("/me", response_model=CurrentUserResponse)
def me(user: CurrentUser, identity: CurrentIdentity) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=identity.role,
        merchant_id=identity.merchant_id,
        shop_id=identity.shop_id,
    )
