"""
auth.py — Autenticación simple con JWT manual (stdlib only, sin dependencias extra).

Flujo:
  POST /api/auth/login  →  { token }
  Headers protegidos:   Authorization: Bearer <token>

Configurar en .env:
  ADMIN_USERNAME=admin
  ADMIN_PASSWORD=tu_password_seguro
  SECRET_KEY=una_clave_secreta_larga
"""
import hmac
import hashlib
import json
import time
import base64
import logging
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.core.config import settings

logger = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/auth", tags=["Auth"])
bearer_scheme = HTTPBearer(auto_error=False)

TOKEN_TTL_SECONDS = 60 * 60 * 8  # 8 horas


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

        # Verificar firma
        expected = _sign(header, payload_b64)
        if not hmac.compare_digest(expected, sig):
            raise ValueError("Firma inválida")

        # Padding para base64
        pad = 4 - len(payload_b64) % 4
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * pad))

        if payload.get("exp", 0) < time.time():
            raise ValueError("Token expirado")

        return payload
    except (KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"Token malformado: {e}")


# ── Dependencia FastAPI ───────────────────────────────────────────────────────

def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Dependencia que inyectás en cualquier endpoint para protegerlo.
    Uso: def my_endpoint(..., _: dict = Depends(require_admin))
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Token requerido")
    try:
        payload = verify_token(credentials.credentials)
        return payload
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


# ── Endpoint de login ─────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

@auth_router.post("/login")
def login(body: LoginRequest):
    """
    Valida credenciales contra ADMIN_USERNAME y ADMIN_PASSWORD del .env.
    Devuelve un JWT con TTL de 8 horas.
    """
    valid = (
        body.username == settings.ADMIN_USERNAME
        and body.password == settings.ADMIN_PASSWORD
    )
    if not valid:
        # Mismo mensaje para no revelar si falla usuario o contraseña
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = create_token(body.username)
    logger.info(f"Login exitoso: {body.username}")
    return {"token": token, "expires_in": TOKEN_TTL_SECONDS}


@auth_router.get("/me")
def me(payload: dict = Depends(require_admin)):
    """Verifica que el token siga siendo válido. Útil para el front al recargar."""
    return {"username": payload["sub"], "exp": payload["exp"]}
