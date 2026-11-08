"""OAuth token discovery, extraction, and lifecycle management."""

import base64
import ctypes
import json
import logging
import os
import re
import sqlite3
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from gateway.config import DEFAULT_CONFIG

logger = logging.getLogger("gateway.auth")


_DISCOVERED_OAUTH_CLIENTS: Optional[List[Tuple[str, str]]] = None


def _discover_ide_oauth_clients() -> List[Tuple[str, str]]:
    """Discover Antigravity OAuth client credentials from a local IDE install.

    The IDE ships these public desktop-client credentials inside its bundled
    JavaScript. We parse them at runtime instead of embedding secrets in source.
    """
    global _DISCOVERED_OAUTH_CLIENTS
    if _DISCOVERED_OAUTH_CLIENTS is not None:
        return _DISCOVERED_OAUTH_CLIENTS

    home = Path.home()
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Antigravity IDE/resources/app/out/main.js",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Antigravity/resources/app/out/main.js",
        Path(os.environ.get("PROGRAMFILES", "")) / "Antigravity IDE/resources/app/out/main.js",
        Path(os.environ.get("PROGRAMFILES", "")) / "Antigravity/resources/app/out/main.js",
        Path("/Applications/Antigravity IDE.app/Contents/Resources/app/out/main.js"),
        Path("/Applications/Antigravity.app/Contents/Resources/app/out/main.js"),
        Path("/opt/Antigravity/resources/app/out/main.js"),
        Path("/opt/antigravity/resources/app/out/main.js"),
        Path("/usr/share/antigravity/resources/app/out/main.js"),
        home / ".local/share/antigravity/resources/app/out/main.js",
    ]

    pairs: List[Tuple[str, str]] = []
    seen = set()
    for path in candidates:
        try:
            if not path or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in (r'oAe="([^"]+)",lAe="([^"]+)"', r'zQe="([^"]+)",jQe="([^"]+)"'):
            for match in re.finditer(pattern, text):
                pair = (match.group(1), match.group(2))
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)

    _DISCOVERED_OAUTH_CLIENTS = pairs
    return pairs


class TokenManager:
    """Manages Google OAuth tokens for Antigravity Cloud Code."""

    def __init__(self, config=DEFAULT_CONFIG):
        self.config = config
        self._cached_access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._refresh_token: Optional[str] = None
        self._account_email: Optional[str] = None
        self._project_id: Optional[str] = config.default_project_id

    def get_candidate_db_paths(self) -> List[Path]:
        """Locate Antigravity SQLite databases across platforms."""
        home = Path.home()
        candidates = [
            home / "Library/Application Support/Antigravity/User/globalStorage/state.vscdb",
            home / "Library/Application Support/Antigravity IDE/User/globalStorage/state.vscdb",
            home / ".config/Antigravity/User/globalStorage/state.vscdb",
            home / "AppData/Roaming/Antigravity/User/globalStorage/state.vscdb",
            home / "AppData/Roaming/Antigravity IDE/User/globalStorage/state.vscdb",
            home / "AppData/Local/Antigravity/User/globalStorage/state.vscdb",
            home / "AppData/Local/Antigravity IDE/User/globalStorage/state.vscdb",
            home / ".gemini/antigravity-ide/User/globalStorage/state.vscdb",
            home / ".gemini/antigravity/User/globalStorage/state.vscdb",
        ]

        # De-duplicate while preserving order
        seen = set()
        unique: List[Path] = []
        for p in candidates:
            if p in seen:
                continue
            seen.add(p)
            unique.append(p)

        return [p for p in unique if p.exists() and p.is_file()]

    def _read_windows_credential(self, target: str) -> Optional[bytes]:
        """Read a raw credential blob from the Windows Credential Manager."""
        if os.name != "nt":
            return None
        try:
            class CREDENTIAL(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR),
                ]

            CredRead = ctypes.windll.advapi32.CredReadW
            CredRead.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
            ]
            CredRead.restype = wintypes.BOOL

            ptr = ctypes.POINTER(CREDENTIAL)()
            ok = CredRead(target, 1, 0, ctypes.byref(ptr))
            if not ok:
                return None
            c = ptr.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        except Exception as e:
            logger.debug("Failed reading Windows credential %s: %s", target, e)
            return None

    def extract_refresh_token_from_windows_credential_manager(
        self,
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Extract refresh token, email, access token and expiry from Windows Credential Manager.

        Returns (refresh_token, email, access_token, expiry).
        """
        blob = self._read_windows_credential("gemini:antigravity")
        if not blob:
            return None, None, None, None
        try:
            data = json.loads(blob.decode("utf-8"))
        except Exception:
            try:
                data = json.loads(blob.decode("utf-16-le"))
            except Exception as e:
                logger.debug("Failed parsing credential blob: %s", e)
                return None, None, None, None

        token = data.get("token", {}) if isinstance(data, dict) else {}
        refresh_token = token.get("refresh_token") if isinstance(token, dict) else None
        access_token = token.get("access_token") if isinstance(token, dict) else None
        expiry = token.get("expiry") if isinstance(token, dict) else None
        email = data.get("id_token_email") if isinstance(data, dict) else None
        return refresh_token, email, access_token, expiry

    # Backwards-friendly alias
    def extract_refresh_token_from_credential_manager(self):
        r, e, a, x = self.extract_refresh_token_from_windows_credential_manager()
        return r, e

    def extract_refresh_token_from_db(self) -> Tuple[Optional[str], Optional[str]]:
        """Extract refresh token and email from Antigravity local state database."""
        db_paths = self.get_candidate_db_paths()
        if not db_paths:
            logger.warning("No Antigravity state.vscdb databases found.")
            return None, None

        for db_path in db_paths:
            try:
                # Open read-only using URI
                uri = f"file:{db_path}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=3.0)
                cursor = conn.cursor()

                # 1. Try unified state sync oauthToken protobuf
                cursor.execute(
                    "SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.oauthToken'"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        raw = base64.b64decode(row[0])
                        match = re.search(rb'\{.*"accessToken".*\}', raw)
                        if match:
                            data = json.loads(match.group(0).decode("utf-8", errors="ignore"))
                            r_token = data.get("refreshToken") or data.get("refresh_token")
                            if r_token:
                                logger.info("Successfully extracted refresh token from %s", db_path.name)
                                conn.close()
                                return r_token, None
                        # Some builds store a nested JSON payload with a refresh token directly
                        try:
                            text = raw.decode("utf-8", errors="ignore")
                            nested = json.loads(text)
                            if isinstance(nested, dict):
                                r_token = nested.get("refreshToken") or nested.get("refresh_token")
                                if r_token:
                                    logger.info("Successfully extracted refresh token from %s", db_path.name)
                                    conn.close()
                                    return r_token, None
                        except Exception:
                            pass
                    except Exception as e:
                        logger.debug("Failed parsing protobuf in %s: %s", db_path, e)

                # 2. Try antigravityAuthStatus for email or token
                cursor.execute(
                    "SELECT value FROM ItemTable WHERE key = 'antigravityAuthStatus'"
                )
                auth_row = cursor.fetchone()
                if auth_row and auth_row[0]:
                    try:
                        auth_data = json.loads(auth_row[0])
                        email = auth_data.get("email")
                        if email:
                            self._account_email = email
                        r_token = auth_data.get("refreshToken") or auth_data.get("refresh_token")
                        if r_token:
                            conn.close()
                            return r_token, self._account_email
                    except Exception:
                        pass

                conn.close()
            except Exception as e:
                logger.debug("Error reading %s: %s", db_path, e)

        return None, self._account_email

    async def refresh_access_token(self, refresh_token: str) -> str:
        """Call Google OAuth2 token endpoint to obtain a new access token.

        Tries all known Antigravity OAuth client credentials until one succeeds.
        """
        # Build candidate client credential pairs
        pairs: List[Tuple[str, str]] = []
        if self.config.oauth_client_id and self.config.oauth_client_secret:
            pairs.append((self.config.oauth_client_id, self.config.oauth_client_secret))
        for pair in _discover_ide_oauth_clients():
            if pair not in pairs:
                pairs.append(pair)

        last_error: Optional[Exception] = None
        for client_id, client_secret in pairs:
            data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(self.config.oauth_token_url, data=data, headers=headers)
                if resp.status_code == 200:
                    payload = resp.json()
                    access_token = payload.get("access_token")
                    expires_in = payload.get("expires_in", 3600)

                    if access_token:
                        self._cached_access_token = access_token
                        # Set expiration with a 5-minute safety margin
                        self._token_expires_at = time.time() + expires_in - 300
                        logger.info("Successfully refreshed access token (valid for %ds)", expires_in)
                        return access_token

                err_text = resp.text
                logger.debug(
                    "Token refresh failed with client %s: %d - %s",
                    client_id[:12],
                    resp.status_code,
                    err_text[:200],
                )
                last_error = RuntimeError(
                    f"Token refresh failed: {resp.status_code} {err_text[:200]}"
                )
            except Exception as e:
                logger.debug("Token refresh exception with client %s: %s", client_id[:12], e)
                last_error = e

        if last_error is None:
            last_error = RuntimeError("Token refresh failed: no client credentials available")
        raise last_error

    async def get_access_token(self) -> str:
        """Get a valid access token, auto-refreshing if expired or nearing expiry."""
        now = time.time()
        if self._cached_access_token and now < self._token_expires_at:
            return self._cached_access_token

        # 1. Windows Credential Manager (agy / Antigravity IDE)
        if not self._refresh_token:
            r_token, email, access_token, expiry = (
                self.extract_refresh_token_from_windows_credential_manager()
            )
            if access_token and expiry:
                try:
                    exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    exp_ts = exp_dt.timestamp()
                    if now < exp_ts - 300:
                        self._cached_access_token = access_token
                        self._token_expires_at = exp_ts - 300
                        if email:
                            self._account_email = email
                        if r_token:
                            self._refresh_token = r_token
                        logger.info("Using access token from Windows Credential Manager")
                        return access_token
                except Exception as e:
                    logger.debug("Failed parsing credential expiry: %s", e)
            if r_token:
                self._refresh_token = r_token
                if email:
                    self._account_email = email

        # 2. Antigravity state databases
        if not self._refresh_token:
            r_token, email = self.extract_refresh_token_from_db()
            if r_token:
                self._refresh_token = r_token
                if email:
                    self._account_email = email
            else:
                # 3. Environment variable
                env_refresh = os.environ.get("GOOGLE_REFRESH_TOKEN")
                if env_refresh:
                    self._refresh_token = env_refresh

        if not self._refresh_token:
            raise RuntimeError(
                "No Google refresh token found. Ensure Antigravity is logged in or provide GOOGLE_REFRESH_TOKEN."
            )

        return await self.refresh_access_token(self._refresh_token)

    @property
    def account_email(self) -> str:
        return self._account_email or "default@antigravity"

    @property
    def project_id(self) -> str:
        return self._project_id or self.config.default_project_id

    def set_project_id(self, project_id: str):
        self._project_id = project_id


# Global instance
token_manager = TokenManager()
