"""Per-request delegated credentials; never serialize these into run state."""

import asyncio
import logging
from time import time
from types import TracebackType
from typing import Literal, Self
from uuid import UUID

import httpx
import jwt
from azure.core.credentials import AccessToken
from pydantic import AwareDatetime, Field, SecretStr, model_validator

from .contracts import Contract, utc_now

logger = logging.getLogger(__name__)
TokenKind = Literal["fabric", "foundry", "graphql", "kusto"]


class SourceAuthorizationError(ValueError):
    pass


class DelegatedTokens(Contract):
    tokens: dict[TokenKind, SecretStr] = Field(min_length=2, max_length=4, repr=False)


class SourceAuthPolicy(Contract):
    tenant_id: UUID
    spa_client_id: UUID
    audiences: dict[TokenKind, tuple[str, ...]]
    scopes: dict[TokenKind, str]

    @model_validator(mode="after")
    def consistent_resources(self) -> "SourceAuthPolicy":
        if set(self.audiences) != set(self.scopes) or not {"fabric", "foundry"}.issubset(self.scopes):
            raise ValueError("Every enabled source requires its audience and resource scope.")
        if any(not values or any(not value.strip() for value in values) for values in self.audiences.values()):
            raise ValueError("Source token audiences cannot be empty.")
        if len(set(self.scopes.values())) != len(self.scopes):
            raise ValueError("Source resource scopes must be distinct.")
        return self


class DelegatedCredential:
    """Validated token leases, isolated to one requesting user and one run."""

    def __init__(
        self, principal_id: UUID, policy: SourceAuthPolicy, tokens: dict[TokenKind, AccessToken],
    ):
        self.principal_id, self.policy = principal_id, policy
        self._tokens = tokens

    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        if len(scopes) != 1:
            raise SourceAuthorizationError("Exactly one configured source resource must be requested.")
        if kwargs.get("claims"):
            raise SourceAuthorizationError("Conditional Access requires renewed consent through the existing SPA.")
        if kwargs.get("tenant_id") not in (None, str(self.policy.tenant_id)):
            raise SourceAuthorizationError("The credential lease cannot be used for another tenant.")
        token: AccessToken | None = None
        for kind, scope in self.policy.scopes.items():
            if scope == scopes[0]:
                token = self._tokens.get(kind)
                break
        if token is None:
            logger.error("Requested source has no authorized delegated token lease.")
            raise SourceAuthorizationError("The requested source needs a delegated token from the signed-in SPA.")
        if token.expires_on <= time():
            logger.warning("Delegated source token lease expired; refresh through the existing SPA.")
            raise SourceAuthorizationError("The source token expired; renew it through the existing sign-in session.")
        return token

    async def close(self) -> None:
        self._tokens.clear()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None = None, exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()


class SourceTokenVerifier:
    def __init__(self, policy: SourceAuthPolicy, client: httpx.AsyncClient):
        self.policy, self.client = policy, client
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires = 0.0
        self._refreshed = 0.0
        self._lock = asyncio.Lock()

    async def key(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            now = time()
            if self._expires <= now or (kid not in self._keys and now - self._refreshed >= 30):
                response = await self.client.get(
                    f"https://login.microsoftonline.com/{self.policy.tenant_id}/discovery/v2.0/keys",
                    timeout=10, follow_redirects=False,
                )
                response.raise_for_status()
                keys = response.json().get("keys")
                if not isinstance(keys, list) or not keys or len(keys) > 100:
                    raise SourceAuthorizationError("The tenant signing-key response is invalid.")
                parsed = {}
                for item in keys:
                    if not isinstance(item, dict) or not isinstance(item.get("kid"), str):
                        raise SourceAuthorizationError("The tenant returned an invalid signing key.")
                    if item["kid"] in parsed:
                        raise SourceAuthorizationError("The tenant returned duplicate signing-key identities.")
                    if item.get("kty") == "RSA" and item.get("use") == "sig":
                        parsed[item["kid"]] = jwt.PyJWK.from_dict(item, algorithm="RS256")
                self._refreshed = time()
                self._keys, self._expires = parsed, self._refreshed + 300
            selected = self._keys.get(kid)
            if selected is None:
                raise SourceAuthorizationError("The token signing key is not registered by the selected tenant.")
            return selected

    async def verify(self, body: DelegatedTokens, deadline: AwareDatetime) -> DelegatedCredential:
        if not {"fabric", "foundry"}.issubset(body.tokens):
            raise SourceAuthorizationError("Fabric and Foundry delegated tokens are required.")
        if not utc_now() < deadline:
            raise SourceAuthorizationError("The credential lease must belong to an active bounded run.")
        tokens: dict[TokenKind, AccessToken] = {}
        principal: UUID | None = None
        tenant = str(self.policy.tenant_id)
        for kind, secret in body.tokens.items():
            audiences = self.policy.audiences.get(kind)
            if not audiences or kind not in self.policy.scopes:
                raise SourceAuthorizationError("The source token has no configured authorization policy.")
            raw = secret.get_secret_value()
            if not 1 <= len(raw) <= 20000:
                raise SourceAuthorizationError("The delegated token exceeds its bounded input size.")
            try:
                header = jwt.get_unverified_header(raw)
                kid = header.get("kid")
                if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 256:
                    raise SourceAuthorizationError("Only tenant-issued RS256 signing keys are accepted.")
                key = await self.key(kid)
                claims = jwt.decode(raw, key.key, algorithms=["RS256"], audience=list(audiences),
                                    options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "scp"]})
                if claims["iss"] not in (
                    f"https://sts.windows.net/{tenant}/", f"https://login.microsoftonline.com/{tenant}/v2.0",
                ):
                    raise SourceAuthorizationError("The delegated token issuer differs from the selected tenant.")
                if not isinstance(claims["tid"], str) or UUID(claims["tid"]) != self.policy.tenant_id:
                    raise SourceAuthorizationError("The source token belongs to a different tenant.")
                client_id = claims.get("azp") or claims.get("appid")
                if not isinstance(client_id, str) or UUID(client_id) != self.policy.spa_client_id:
                    raise SourceAuthorizationError("The source token was not issued to the configured SPA.")
                if not isinstance(claims["scp"], str) or not claims["scp"].strip() or claims.get("idtyp") == "app":
                    raise SourceAuthorizationError("App-only tokens cannot replace delegated source access.")
                if not isinstance(claims["oid"], str):
                    raise SourceAuthorizationError("The delegated token has no valid signed-in user identity.")
                current = UUID(claims["oid"])
                if principal is not None and current != principal:
                    raise SourceAuthorizationError("Source tokens belong to different signed-in users.")
                expires = claims["exp"]
                if type(expires) is not int or expires < deadline.timestamp():
                    raise SourceAuthorizationError("Renew the source token lease before submitting this run.")
            except (jwt.InvalidTokenError, ValueError, TypeError) as error:
                logger.warning("Delegated %s token authorization rejected (%s).", kind, type(error).__name__)
                raise SourceAuthorizationError("The delegated source token failed authorization validation.") from error
            principal = current
            tokens[kind] = AccessToken(raw, expires)
        if principal is None:
            raise SourceAuthorizationError("No signed-in user identity was verified.")
        return DelegatedCredential(principal, self.policy, tokens)
