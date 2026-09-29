import hashlib, hmac, os, secrets, time
from collections import defaultdict, deque
from datetime import timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .db import get_db
from .models import AuthToken, User, utcnow

TOKEN_TTL = timedelta(hours=int(os.getenv("TOKEN_TTL_HOURS", "12")))
_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    salt, dk = stored.split("$")
    cand = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(cand.hex(), dk)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(token_hash=_digest(raw), user_id=user.id, expires_at=utcnow() + TOKEN_TTL))
    db.commit()
    return raw


def current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    tok = db.get(AuthToken, _digest(creds.credentials))
    if not tok or tok.expires_at < utcnow():
        raise HTTPException(401, "Invalid or expired token")
    return db.get(User, tok.user_id)


# Tiny in-memory auth throttle (per client IP). Fine for one process; use Redis if scaled out.
_hits: dict[str, deque] = defaultdict(deque)


def throttle_auth(request: Request, limit: int = 10, window: int = 60):
    ip, now = (request.client.host if request.client else "?"), time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts, try again shortly")
    q.append(now)
