"""
auth.py — Autenticación con JWT.

Soporta dos métodos:
  1. httpOnly cookie (legacy / mismo dominio)
  2. Bearer token en header Authorization (cross-site / Railway)

Flujo:
  POST /api/auth/login   →  devuelve { token, expires_in } + Set-Cookie opcional
  POST /api/auth/logout  →  borra la cookie
  GET  /api/auth/me      →  verifica que el token siga siendo válido

Configurar en .env:
  ADMIN_USERNAME=admin
  ADMIN_PASSWORD=$2b$12$...   ← hash bcrypt
  SECRET_KEY=una_clave_secreta_larga
  ENVIRONMENT=production      ← activa Secure en la cookie

Generar hash de contraseña:
  python -c "import bcrypt; print(bcrypt.hashpw(b'tu_password', bcrypt.gensalt()).decode())"
"""
import hmac
import hashlib
import json
import time
import base64
import logging
import bcrypt
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.core.config import settings

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/auth", tags=["Auth"])
bearer_scheme = HTTPBearer(auto_error=False)

TOKEN_TTL_SECONDS = 60 * 60 * 2  # 2 horas
COOKIE_NAME = "admin_token"


# ── JWT manual (HS256) ────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _sign(header_b64: str, payload_b64: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).digest()
    return _b64url(sig)

def create_token(username: str) -> str:
    header  = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }).encode())
    sig = _sign(header, payload)
    return f"{header}.{payload}.{sig}"

def verify_token(token: str) -> dict:
    """Lanza ValueError si el token es inválido o expirado."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Formato inválido")
        header, payload_b64, sig = parts

        expected = _sign(header, payload_b64)
        if not hmac.compare_digest(expected, sig):
            raise ValueError("Firma inválida")

        pad = 4 - len(payload_b64) % 4
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * pad))

        if payload.get("exp", 0) < time.time():
            raise ValueError("Token expirado")

        return payload
    except (KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"Token malformado: {e}")


# ── Dependencia FastAPI ───────────────────────────────────────────────────────

def require_admin(request: Request):
    """
    Lee el JWT desde:
      1. Header Authorization: Bearer <token>  ← prioridad (cross-site)
      2. httpOnly cookie admin_token            ← fallback (mismo dominio)
    """
    token = None

    # 1. Intentar desde header Authorization
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # 2. Fallback: cookie httpOnly
    if not token:
        token = request.cookies.get(COOKIE_NAME)

    if not token:
        raise HTTPException(status_code=401, detail="Token requerido")

    try:
        return verify_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


# ── Endpoints ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

@auth_router.post("/login")
def login(body: LoginRequest, response: Response):
    """
    Valida credenciales y devuelve el JWT en el body.
    También setea la httpOnly cookie por compatibilidad.
    ADMIN_PASSWORD debe ser un hash bcrypt.
    """
    username_ok = body.username == settings.ADMIN_USERNAME

    try:
        password_ok = bcrypt.checkpw(
            body.password.encode(),
            settings.ADMIN_PASSWORD.encode(),
        )
    except Exception:
        password_ok = False

    if not username_ok or not password_ok:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = create_token(body.username)

    # Cookie como fallback (puede ser bloqueada en cross-site por Chrome)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="none",
        secure=True,
        max_age=TOKEN_TTL_SECONDS,
        path="/",
    )

    logger.info(f"Login exitoso: {body.username}")
    return {
        "message": "Login exitoso",
        "token": token,                  # ← el frontend lo guarda en localStorage
        "expires_in": TOKEN_TTL_SECONDS,
    }


@auth_router.post("/logout")
def logout(response: Response):
    """Borra la cookie del admin."""
    response.delete_cookie(key=COOKIE_NAME, path="/", samesite="none", secure=True)
    return {"message": "Sesión cerrada"}


@auth_router.get("/me")
def me(payload: dict = Depends(require_admin)):
    """Verifica que el token siga siendo válido. Útil para el front al recargar."""
    return {"username": payload["sub"], "exp": payload["exp"]}
