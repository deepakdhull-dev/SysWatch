from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

import jwt
from fastapi import Response
from passlib.context import CryptContext

from ..exceptions import CertificateError, ConfigError

logger = logging.getLogger(__name__)

ACCESS_COOKIE = "syswatch_access"
REFRESH_COOKIE = "syswatch_refresh"


class AuthManager:
    def __init__(self, cfg: Any) -> None:
        jwt_cfg = cfg.jwt
        auth_cfg = cfg.auth

        self._algorithm: str = jwt_cfg.algorithm
        self._access_expire = datetime.timedelta(
            minutes=jwt_cfg.access_token_expire_minutes
        )
        self._refresh_expire = datetime.timedelta(
            days=jwt_cfg.refresh_token_expire_days
        )

        private_key_path = Path(jwt_cfg.private_key)
        public_key_path = Path(jwt_cfg.public_key)

        for path, label in [
            (private_key_path, "JWT private key"),
            (public_key_path, "JWT public key"),
        ]:
            if not path.exists():
                raise CertificateError(
                    f"{label} not found at {path}. "
                    "Run install.sh (server path) to generate JWT keys."
                )

        self._private_key: str = private_key_path.read_text()
        self._public_key: str = public_key_path.read_text()
        logger.info(
            "JWT keys loaded (private=%s public=%s)",
            jwt_cfg.private_key,
            jwt_cfg.public_key,
        )

        self._admin_username: str = auth_cfg.admin_username
        self._password_hash: str = auth_cfg.admin_password_hash

        if not self._password_hash:
            raise ConfigError(
                "auth.admin_password_hash is empty in config.yaml. "
                "Run install.sh (server path) to set the admin password, or "
                "set SYSWATCH_AUTH_ADMIN_PASSWORD_HASH."
            )

        self._pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        logger.info("Admin credentials loaded for user %r", self._admin_username)

    def verify_password(self, plain: str) -> bool:
        try:
            return self._pwd_context.verify(plain, self._password_hash)
        except Exception as exc:
            logger.error("Password verification error: %s", exc)
            return False

    def verify_login(self, username: str, password: str) -> bool:
        if username != self._admin_username:
            self._pwd_context.dummy_verify()
            return False
        return self.verify_password(password)

    def _create_token(self, token_type: str, expire: datetime.timedelta) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "sub": self._admin_username,
            "type": token_type,
            "iat": now,
            "exp": now + expire,
        }
        return jwt.encode(payload, self._private_key, algorithm=self._algorithm)

    def create_access_token(self) -> str:
        return self._create_token("access", self._access_expire)

    def create_refresh_token(self) -> str:
        return self._create_token("refresh", self._refresh_expire)

    def verify_token(self, token: str, expected_type: str = "access") -> bool:
        try:
            payload = jwt.decode(
                token,
                self._public_key,
                algorithms=[self._algorithm],
            )
            return (
                payload.get("sub") == self._admin_username
                and payload.get("type") == expected_type
            )
        except jwt.ExpiredSignatureError:
            logger.debug("JWT token expired")
            return False
        except jwt.InvalidTokenError as exc:
            logger.warning("JWT validation failed: %s", exc)
            return False

    def decode_token(self, token: str) -> dict | None:
        try:
            return jwt.decode(token, self._public_key, algorithms=[self._algorithm])
        except jwt.PyJWTError:
            return None

    def set_auth_cookies(self, response: Response) -> None:
        access_token = self.create_access_token()
        refresh_token = self.create_refresh_token()
        response.set_cookie(
            key=ACCESS_COOKIE,
            value=access_token,
            httponly=True,
            samesite="lax",
            max_age=int(self._access_expire.total_seconds()),
        )
        response.set_cookie(
            key=REFRESH_COOKIE,
            value=refresh_token,
            httponly=True,
            samesite="lax",
            max_age=int(self._refresh_expire.total_seconds()),
        )

    def delete_auth_cookies(self, response: Response) -> None:
        response.delete_cookie(ACCESS_COOKIE)
        response.delete_cookie(REFRESH_COOKIE)
