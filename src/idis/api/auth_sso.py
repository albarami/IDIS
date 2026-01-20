"""IDIS SSO authentication via OIDC/JWT.

Implements enterprise SSO integration per v6.3 Security Threat Model:
- OIDC JWT validation with JWKS signature verification
- Standard claims validation (issuer, audience, exp/nbf)
- Required IDIS claims: tenant_id, user_id, roles
- Optional claims: data_region, policy_tags
- Fail-closed on any validation error or missing config

ADR-007: OIDC + RBAC + deal-level ABAC + break-glass audit
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from idis.api.errors import IdisHttpError
from idis.api.policy import ALL_ROLES

logger = logging.getLogger(__name__)

OIDC_ISSUER_ENV = "IDIS_OIDC_ISSUER"
OIDC_AUDIENCE_ENV = "IDIS_OIDC_AUDIENCE"
OIDC_JWKS_URI_ENV = "IDIS_OIDC_JWKS_URI"
OIDC_JWKS_CACHE_TTL_ENV = "IDIS_OIDC_JWKS_CACHE_TTL"

DEFAULT_JWKS_CACHE_TTL = 3600
DEFAULT_CLOCK_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class OidcConfig:
    """OIDC configuration for JWT validation.

    All fields required for OIDC to be enabled. If any field is missing,
    OIDC auth is disabled (fail-closed: Bearer tokens rejected).
    """

    issuer: str
    audience: str
    jwks_uri: str
    jwks_cache_ttl: int = DEFAULT_JWKS_CACHE_TTL
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS


@dataclass(frozen=True, slots=True)
class SsoIdentity:
    """Identity extracted from a validated SSO JWT.

    Contains all required IDIS claims for downstream auth/RBAC/ABAC.
    """

    tenant_id: str
    user_id: str
    roles: frozenset[str]
    email: str | None = None
    name: str | None = None
    data_region: str | None = None
    policy_tags: frozenset[str] = field(default_factory=frozenset)
    token_hash: str = ""


@dataclass
class JwksCache:
    """In-memory JWKS cache with TTL.

    Thread-safe for read operations. Write operations should be
    externally synchronized if needed.
    """

    keys: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_at: float = 0.0
    ttl: int = DEFAULT_JWKS_CACHE_TTL

    def is_valid(self) -> bool:
        """Check if cache is still valid based on TTL."""
        if not self.keys:
            return False
        return (time.time() - self.fetched_at) < self.ttl

    def get_key(self, kid: str) -> dict[str, Any] | None:
        """Get a key by kid from cache."""
        return self.keys.get(kid)


_jwks_cache = JwksCache()


def load_oidc_config() -> OidcConfig | None:
    """Load OIDC configuration from environment variables.

    Returns:
        OidcConfig if all required env vars are set, None otherwise.
        Returns None (not error) to allow API key fallback.
    """
    issuer = os.environ.get(OIDC_ISSUER_ENV)
    audience = os.environ.get(OIDC_AUDIENCE_ENV)
    jwks_uri = os.environ.get(OIDC_JWKS_URI_ENV)

    if not issuer or not audience or not jwks_uri:
        return None

    try:
        cache_ttl = int(os.environ.get(OIDC_JWKS_CACHE_TTL_ENV, str(DEFAULT_JWKS_CACHE_TTL)))
    except ValueError:
        cache_ttl = DEFAULT_JWKS_CACHE_TTL

    return OidcConfig(
        issuer=issuer,
        audience=audience,
        jwks_uri=jwks_uri,
        jwks_cache_ttl=cache_ttl,
    )


def _fetch_jwks(jwks_uri: str) -> dict[str, dict[str, Any]]:
    """Fetch JWKS from the configured URI.

    Args:
        jwks_uri: The JWKS endpoint URI.

    Returns:
        Dict mapping kid to JWK dict.

    Raises:
        IdisHttpError: On fetch failure (fail-closed).
    """
    try:
        import urllib.request

        with urllib.request.urlopen(jwks_uri, timeout=10) as response:
            jwks_data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.error("Failed to fetch JWKS from %s: %s", jwks_uri, str(e))
        raise IdisHttpError(
            status_code=401,
            code="sso_config_error",
            message="SSO configuration error",
        ) from e

    if not isinstance(jwks_data, dict) or "keys" not in jwks_data:
        logger.error("Invalid JWKS format from %s", jwks_uri)
        raise IdisHttpError(
            status_code=401,
            code="sso_config_error",
            message="SSO configuration error",
        )

    keys: dict[str, dict[str, Any]] = {}
    for key in jwks_data.get("keys", []):
        if isinstance(key, dict) and "kid" in key:
            keys[key["kid"]] = key

    return keys


def _get_jwk(kid: str, config: OidcConfig) -> dict[str, Any]:
    """Get JWK by kid, fetching JWKS if cache is stale.

    Args:
        kid: The key ID from the JWT header.
        config: OIDC configuration.

    Returns:
        The JWK dict for the specified kid.

    Raises:
        IdisHttpError: If kid not found or fetch fails (fail-closed).
    """
    global _jwks_cache

    if not _jwks_cache.is_valid():
        _jwks_cache.keys = _fetch_jwks(config.jwks_uri)
        _jwks_cache.fetched_at = time.time()
        _jwks_cache.ttl = config.jwks_cache_ttl

    jwk = _jwks_cache.get_key(kid)

    if jwk is None:
        _jwks_cache.keys = _fetch_jwks(config.jwks_uri)
        _jwks_cache.fetched_at = time.time()
        jwk = _jwks_cache.get_key(kid)

    if jwk is None:
        logger.warning("JWT kid '%s' not found in JWKS", kid)
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Invalid token signature",
        )

    return jwk


def _decode_jwt_parts(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Decode JWT header, payload, and signature without verification.

    Args:
        token: The raw JWT string.

    Returns:
        Tuple of (header dict, payload dict, signature bytes).

    Raises:
        IdisHttpError: On malformed JWT (fail-closed).
    """
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Malformed token",
        )

    def decode_part(part: str) -> bytes:
        padding = 4 - len(part) % 4
        if padding != 4:
            part += "=" * padding
        return base64.urlsafe_b64decode(part)

    try:
        header = json.loads(decode_part(parts[0]).decode("utf-8"))
        payload = json.loads(decode_part(parts[1]).decode("utf-8"))
        signature = decode_part(parts[2])
    except Exception as e:
        logger.debug("JWT decode error: %s", str(e))
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Malformed token",
        ) from e

    return header, payload, signature


def _verify_signature(token: str, jwk: dict[str, Any]) -> None:
    """Verify JWT signature using the provided JWK.

    Args:
        token: The raw JWT string.
        jwk: The JWK to verify against.

    Raises:
        IdisHttpError: On signature verification failure (fail-closed).
    """
    try:
        import base64

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        alg = jwk.get("alg", "RS256")
        kty = jwk.get("kty")

        parts = token.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode()

        def b64_decode(data: str) -> bytes:
            padding_needed = 4 - len(data) % 4
            if padding_needed != 4:
                data += "=" * padding_needed
            return base64.urlsafe_b64decode(data)

        signature = b64_decode(parts[2])

        if kty == "RSA":
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

            n = int.from_bytes(b64_decode(jwk["n"]), "big")
            e = int.from_bytes(b64_decode(jwk["e"]), "big")
            rsa_numbers = RSAPublicNumbers(e, n)
            rsa_key = rsa_numbers.public_key(default_backend())

            hash_alg: hashes.HashAlgorithm
            if alg in ("RS256", "PS256"):
                hash_alg = hashes.SHA256()
            elif alg in ("RS384", "PS384"):
                hash_alg = hashes.SHA384()
            elif alg in ("RS512", "PS512"):
                hash_alg = hashes.SHA512()
            else:
                raise IdisHttpError(
                    status_code=401,
                    code="invalid_token",
                    message="Unsupported algorithm",
                )

            if alg.startswith("PS"):
                rsa_padding: padding.AsymmetricPadding = padding.PSS(
                    mgf=padding.MGF1(hash_alg),
                    salt_length=padding.PSS.MAX_LENGTH,
                )
            else:
                rsa_padding = padding.PKCS1v15()

            rsa_key.verify(signature, signing_input, rsa_padding, hash_alg)

        elif kty == "EC":
            from cryptography.hazmat.primitives.asymmetric.ec import (
                ECDSA,
                SECP256R1,
                SECP384R1,
                SECP521R1,
                EllipticCurvePublicNumbers,
            )
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

            crv = jwk.get("crv")
            x = int.from_bytes(b64_decode(jwk["x"]), "big")
            y = int.from_bytes(b64_decode(jwk["y"]), "big")

            ec_hash_alg: hashes.HashAlgorithm
            if crv == "P-256":
                ec_numbers = EllipticCurvePublicNumbers(x, y, SECP256R1())
                ec_hash_alg = hashes.SHA256()
                sig_len = 64
            elif crv == "P-384":
                ec_numbers = EllipticCurvePublicNumbers(x, y, SECP384R1())
                ec_hash_alg = hashes.SHA384()
                sig_len = 96
            elif crv == "P-521":
                ec_numbers = EllipticCurvePublicNumbers(x, y, SECP521R1())
                ec_hash_alg = hashes.SHA512()
                sig_len = 132
            else:
                raise IdisHttpError(
                    status_code=401,
                    code="invalid_token",
                    message="Unsupported curve",
                )

            ec_key = ec_numbers.public_key(default_backend())

            r = int.from_bytes(signature[: sig_len // 2], "big")
            s = int.from_bytes(signature[sig_len // 2 :], "big")
            der_sig = encode_dss_signature(r, s)

            ec_key.verify(der_sig, signing_input, ECDSA(ec_hash_alg))

        else:
            raise IdisHttpError(
                status_code=401,
                code="invalid_token",
                message="Unsupported key type",
            )

    except IdisHttpError:
        raise
    except Exception as e:
        logger.debug("JWT signature verification failed: %s", str(e))
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Invalid token signature",
        ) from e


def _validate_standard_claims(
    payload: dict[str, Any],
    config: OidcConfig,
) -> None:
    """Validate standard JWT claims (issuer, audience, exp, nbf, iat).

    Args:
        payload: The decoded JWT payload.
        config: OIDC configuration.

    Raises:
        IdisHttpError: On any claim validation failure (fail-closed).
    """
    iss = payload.get("iss")
    if iss != config.issuer:
        logger.warning("JWT issuer mismatch: expected '%s', got '%s'", config.issuer, iss)
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Invalid token issuer",
        )

    aud = payload.get("aud")
    if isinstance(aud, list):
        if config.audience not in aud:
            logger.warning("JWT audience mismatch: expected '%s' in %s", config.audience, aud)
            raise IdisHttpError(
                status_code=401,
                code="invalid_token",
                message="Invalid token audience",
            )
    elif aud != config.audience:
        logger.warning("JWT audience mismatch: expected '%s', got '%s'", config.audience, aud)
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Invalid token audience",
        )

    now = time.time()

    exp = payload.get("exp")
    if exp is None:
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token missing expiration",
        )
    if not isinstance(exp, (int, float)) or now > exp + config.clock_skew_seconds:
        logger.warning("JWT expired: exp=%s, now=%s", exp, now)
        raise IdisHttpError(
            status_code=401,
            code="token_expired",
            message="Token has expired",
        )

    nbf = payload.get("nbf")
    if nbf is not None and (
        not isinstance(nbf, (int, float)) or now < nbf - config.clock_skew_seconds
    ):
        logger.warning("JWT not yet valid: nbf=%s, now=%s", nbf, now)
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token not yet valid",
        )

    iat = payload.get("iat")
    if iat is not None and (
        not isinstance(iat, (int, float)) or now < iat - config.clock_skew_seconds
    ):
        logger.warning("JWT issued in future: iat=%s, now=%s", iat, now)
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token issued in future",
        )


def _validate_idis_claims(payload: dict[str, Any]) -> SsoIdentity:
    """Validate and extract required IDIS claims from JWT payload.

    Required claims:
    - tenant_id: Tenant identifier (required)
    - sub or user_id: User identifier (required)
    - roles: List of role strings (required, must be non-empty)

    Optional claims:
    - email: User email
    - name: User display name
    - data_region: Data residency region
    - policy_tags: Additional policy tags for ABAC

    Args:
        payload: The decoded JWT payload.

    Returns:
        SsoIdentity with validated claims.

    Raises:
        IdisHttpError: On missing or invalid required claims (fail-closed).
    """
    tenant_id = payload.get("tenant_id") or payload.get("https://idis.io/tenant_id")
    if not tenant_id or not isinstance(tenant_id, str):
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token missing required claim: tenant_id",
        )

    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id or not isinstance(user_id, str):
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token missing required claim: user_id",
        )

    roles_claim = payload.get("roles") or payload.get("https://idis.io/roles")
    if not roles_claim or not isinstance(roles_claim, list):
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token missing required claim: roles",
        )

    roles: set[str] = set()
    for role in roles_claim:
        if isinstance(role, str):
            normalized = role.upper().strip()
            if normalized in ALL_ROLES:
                roles.add(normalized)
            else:
                logger.warning("Unknown role in JWT rejected (fail-closed): %s", role)
                raise IdisHttpError(
                    status_code=401,
                    code="invalid_token",
                    message=f"Unknown role: {role}",
                )

    if not roles:
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token has no valid roles",
        )

    email = payload.get("email")
    name = payload.get("name")
    data_region = payload.get("data_region") or payload.get("https://idis.io/data_region")
    policy_tags_claim = payload.get("policy_tags") or payload.get("https://idis.io/policy_tags")

    policy_tags: frozenset[str] = frozenset()
    if isinstance(policy_tags_claim, list):
        policy_tags = frozenset(t for t in policy_tags_claim if isinstance(t, str))

    return SsoIdentity(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=frozenset(roles),
        email=email if isinstance(email, str) else None,
        name=name if isinstance(name, str) else None,
        data_region=data_region if isinstance(data_region, str) else None,
        policy_tags=policy_tags,
    )


def validate_jwt(token: str, config: OidcConfig | None = None) -> SsoIdentity:
    """Validate a JWT and extract IDIS identity.

    Full validation flow (fail-closed):
    1. Load OIDC config (fail if not configured)
    2. Decode JWT parts (fail on malformed)
    3. Get JWK by kid from JWKS (fail if not found)
    4. Verify signature (fail on mismatch)
    5. Validate standard claims (iss, aud, exp, nbf)
    6. Validate and extract IDIS claims

    Args:
        token: The raw JWT string (without "Bearer " prefix).
        config: Optional OIDC config override (for testing).

    Returns:
        SsoIdentity with validated claims.

    Raises:
        IdisHttpError: On any validation failure (fail-closed).
    """
    if config is None:
        config = load_oidc_config()

    if config is None:
        raise IdisHttpError(
            status_code=401,
            code="sso_not_configured",
            message="SSO authentication not configured",
        )

    header, payload, _ = _decode_jwt_parts(token)

    kid = header.get("kid")
    if not kid:
        raise IdisHttpError(
            status_code=401,
            code="invalid_token",
            message="Token missing key ID",
        )

    jwk = _get_jwk(kid, config)

    _verify_signature(token, jwk)

    _validate_standard_claims(payload, config)

    identity = _validate_idis_claims(payload)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    identity = SsoIdentity(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        roles=identity.roles,
        email=identity.email,
        name=identity.name,
        data_region=identity.data_region,
        policy_tags=identity.policy_tags,
        token_hash=token_hash,
    )

    return identity


def is_sso_configured() -> bool:
    """Check if SSO/OIDC is configured.

    Returns:
        True if all required OIDC env vars are set.
    """
    return load_oidc_config() is not None


def clear_jwks_cache() -> None:
    """Clear the JWKS cache. Useful for testing."""
    global _jwks_cache
    _jwks_cache = JwksCache()
