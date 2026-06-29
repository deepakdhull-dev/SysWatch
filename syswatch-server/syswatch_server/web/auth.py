from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

import jwt
import yaml
from fastapi import Response
from passlib.context import CryptContext

from ..exceptions import CertificateError, ConfigError

logger = logging.getLogger(__name__)

ACCESS_COOKIE = "syswatch_access"
REFRESH_COOKIE = "syswatch_refresh"

ACCESS_TOKEN_EXPIRE = datetime.timedelta(hours=1)
REFRESH_TOKEN_EXPIRE = datetime.timedelta(days=7)

ALGORITHM = "RS256"

ADMIN_USERNAME = "admin"


class AuthManager:
    def __init__(self, cfg: Any) -> None:
        auth_cfg = cfg.auth

        private_key_path = Path(auth_cfg.jwt_private_key)
        public_key_path = Path(auth_cfg.jwt_public_key)

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
            auth_cfg.jwt_private_key,
            auth_cfg.jwt_public_key,
        )

        creds_path = Path(auth_cfg.credentials_file)
        if not creds_path.exists():
            raise ConfigError(
                f"Admin credentials file not found at {creds_path}. "
                "Run install.sh (server path) to set the admin password."
            )

        with open(creds_path) as f:
            creds = yaml.safe_load(f)

        if not isinstance(creds, dict) or "password_hash" not in creds:
            raise ConfigError(
                f"credentials.yaml at {creds_path} is malformed. "
                "Expected YAML with 'password_hash' key."
            )

        self._password_hash: str = creds["password_hash"]
        self._pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        logger.info("Admin credentials loaded from %s", creds_path)

    def verify_password(self, plain: str) -> bool:
        try:
            return self._pwd_context.verify(plain, self._password_hash)
        except Exception as exc:
            logger.error("Password verification error: %s", exc)
            return False

    def verify_login(self, username: str, password: str) -> bool:
        if username != ADMIN_USERNAME:
            self._pwd_context.dummy_verify()
            return False
        return self.verify_password(password)

    def _create_token(self, token_type: str, expire: datetime.timedelta) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "sub": ADMIN_USERNAME,
            "type": token_type,
            "iat": now,
            "exp": now + expire,
        }
        return jwt.encode(payload, self._private_key, algorithm=ALGORITHM)

    def create_access_token(self) -> str:
        return self._create_token("access", ACCESS_TOKEN_EXPIRE)

    def create_refresh_token(self) -> str:
        return self._create_token("refresh", REFRESH_TOKEN_EXPIRE)

    def verify_token(self, token: str, expected_type: str = "access") -> bool:
        try:
            payload = jwt.decode(
                token,
                self._public_key,
                algorithms=[ALGORITHM],
            )
            return (
                payload.get("sub") == ADMIN_USERNAME
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
            return jwt.decode(token, self._public_key, algorithms=[ALGORITHM])
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
            max_age=int(ACCESS_TOKEN_EXPIRE.total_seconds()),
        )
        response.set_cookie(
            key=REFRESH_COOKIE,
            value=refresh_token,
            httponly=True,
            samesite="lax",
            max_age=int(REFRESH_TOKEN_EXPIRE.total_seconds()),
        )

    def delete_auth_cookies(self, response: Response) -> None:
        response.delete_cookie(ACCESS_COOKIE)
        response.delete_cookie(REFRESH_COOKIE)
