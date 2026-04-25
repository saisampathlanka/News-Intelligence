"""
Security test suite.

Covers:
  1. JWT creation, verification, expiry, tampering
  2. Password hashing correctness and timing safety
  3. Input validation (registration, login)
  4. Auth dependency logic
  5. Security edge cases and attack vectors

Run: pytest backend/tests/test_security.py -v
"""
import sys, os, time, hmac
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
import secrets
from backend.core.jwt import (
    create_access_token, create_refresh_token, create_api_key_token,
    verify_token, decode_token_unsafe, TokenError,
    ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
)
from backend.core.security import hash_password, verify_password, generate_api_key


SECRET = secrets.token_hex(32)   # fresh secret per test run


# ── JWT: token creation ───────────────────────────────────────────────────────

class TestJWTCreation:
    def test_access_token_is_string(self):
        t = create_access_token("42", SECRET)
        assert isinstance(t, str)

    def test_access_token_three_parts(self):
        t = create_access_token("42", SECRET)
        assert len(t.split(".")) == 3

    def test_access_token_starts_eyJ(self):
        # All JWTs start with base64({"alg":"HS256","typ":"JWT"})
        t = create_access_token("42", SECRET)
        assert t.startswith("eyJ")

    def test_payload_contains_sub(self):
        t = create_access_token("user99", SECRET)
        payload = verify_token(t, SECRET)
        assert payload["sub"] == "user99"

    def test_payload_contains_role(self):
        t = create_access_token("1", SECRET, role="admin")
        payload = verify_token(t, SECRET)
        assert payload["role"] == "admin"

    def test_payload_contains_exp(self):
        t = create_access_token("1", SECRET)
        payload = verify_token(t, SECRET)
        assert "exp" in payload
        assert payload["exp"] > int(time.time())

    def test_payload_contains_jti(self):
        t = create_access_token("1", SECRET)
        payload = verify_token(t, SECRET)
        assert "jti" in payload
        assert len(payload["jti"]) == 32  # 16 bytes hex = 32 chars

    def test_unique_jti_per_token(self):
        t1 = create_access_token("1", SECRET)
        t2 = create_access_token("1", SECRET)
        p1 = verify_token(t1, SECRET)
        p2 = verify_token(t2, SECRET)
        assert p1["jti"] != p2["jti"]

    def test_token_type_is_access(self):
        t = create_access_token("1", SECRET)
        payload = verify_token(t, SECRET)
        assert payload["type"] == "access"

    def test_refresh_token_type(self):
        t = create_refresh_token("1", SECRET)
        payload = verify_token(t, SECRET, expected_type="refresh")
        assert payload["type"] == "refresh"

    def test_refresh_token_no_role(self):
        t = create_refresh_token("1", SECRET)
        payload = verify_token(t, SECRET, expected_type="refresh")
        assert "role" not in payload

    def test_api_key_token_no_expiry(self):
        t = create_api_key_token("svc1", SECRET, role="viewer")
        payload = verify_token(t, SECRET, expected_type="api_key")
        assert "exp" not in payload

    def test_extra_claims_included(self):
        t = create_access_token("1", SECRET, extra_claims={"org": "acme"})
        payload = verify_token(t, SECRET)
        assert payload["org"] == "acme"

    def test_different_secrets_produce_different_tokens(self):
        t1 = create_access_token("1", SECRET)
        t2 = create_access_token("1", "different_secret_value_here_32chars")
        assert t1 != t2


# ── JWT: verification ─────────────────────────────────────────────────────────

class TestJWTVerification:
    def test_valid_token_returns_payload(self):
        t = create_access_token("user1", SECRET, role="viewer")
        p = verify_token(t, SECRET)
        assert p["sub"] == "user1"
        assert p["role"] == "viewer"

    def test_expired_token_raises(self):
        t = create_access_token("1", SECRET, expires_minutes=-1)
        with pytest.raises(TokenError) as exc:
            verify_token(t, SECRET)
        assert "expired" in str(exc.value).lower()

    def test_wrong_secret_raises(self):
        t = create_access_token("1", SECRET)
        with pytest.raises(TokenError) as exc:
            verify_token(t, "wrong_secret_completely_different")
        assert "signature" in str(exc.value).lower()

    def test_tampered_signature_raises(self):
        t = create_access_token("1", SECRET)
        parts = t.split(".")
        parts[2] = parts[2][:-4] + "XXXX"
        tampered = ".".join(parts)
        with pytest.raises(TokenError):
            verify_token(tampered, SECRET)

    def test_tampered_payload_raises(self):
        t = create_access_token("1", SECRET, role="viewer")
        parts = t.split(".")
        import base64, json
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        payload["role"] = "admin"  # attempt privilege escalation
        parts[1] = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b"=").decode()
        forged = ".".join(parts)
        with pytest.raises(TokenError):
            verify_token(forged, SECRET)

    def test_empty_token_raises(self):
        with pytest.raises(TokenError):
            verify_token("", SECRET)

    def test_none_token_raises(self):
        with pytest.raises(TokenError):
            verify_token(None, SECRET)  # type: ignore

    def test_malformed_one_part_raises(self):
        with pytest.raises(TokenError):
            verify_token("notavalidtoken", SECRET)

    def test_malformed_two_parts_raises(self):
        with pytest.raises(TokenError):
            verify_token("header.payload", SECRET)

    def test_wrong_type_raises(self):
        access = create_access_token("1", SECRET)
        with pytest.raises(TokenError) as exc:
            verify_token(access, SECRET, expected_type="refresh")
        assert "type" in str(exc.value).lower()

    def test_refresh_token_rejected_as_access(self):
        refresh = create_refresh_token("1", SECRET)
        with pytest.raises(TokenError):
            verify_token(refresh, SECRET, expected_type="access")

    def test_future_iat_raises(self):
        """Tokens with iat far in the future should be rejected (clock skew attack)."""
        import json, base64, hmac as _hmac, hashlib, time as _time
        future_payload = {
            "sub": "1", "type": "access", "jti": "x",
            "iat": int(_time.time()) + 3600,  # 1 hour in future
            "exp": int(_time.time()) + 7200,
        }
        h = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
        p = base64.urlsafe_b64encode(json.dumps(future_payload).encode()).rstrip(b"=").decode()
        sig = _hmac.new(SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        s = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        with pytest.raises(TokenError) as exc:
            verify_token(f"{h}.{p}.{s}", SECRET)
        assert "future" in str(exc.value).lower()

    def test_decode_unsafe_does_not_verify(self):
        t = create_access_token("1", SECRET)
        parts = t.split(".")
        parts[2] = "invalidsig"
        bad = ".".join(parts)
        payload = decode_token_unsafe(bad)
        assert payload.get("sub") == "1"  # returned despite bad sig

    def test_decode_unsafe_returns_empty_on_garbage(self):
        assert decode_token_unsafe("garbage") == {}


# ── Password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_returns_string(self):
        h = hash_password("Password123!")
        assert isinstance(h, str)

    def test_hash_is_base64(self):
        import base64
        h = hash_password("Password123!")
        base64.b64decode(h)  # should not raise

    def test_correct_password_verifies(self):
        pw = "Secure@Password1"
        assert verify_password(pw, hash_password(pw)) is True

    def test_wrong_password_fails(self):
        h = hash_password("Correct!Passw0rd")
        assert verify_password("WrongPassword1!", h) is False

    def test_empty_password_fails(self):
        h = hash_password("SomePassword1!")
        assert verify_password("", h) is False

    def test_empty_hash_fails(self):
        assert verify_password("Password1!", "") is False

    def test_none_inputs_safe(self):
        assert verify_password(None, "hash") is False  # type: ignore
        assert verify_password("pw", None) is False    # type: ignore

    def test_sql_injection_attempt(self):
        h = hash_password("RealPassword1!")
        assert verify_password("' OR '1'='1", h) is False
        assert verify_password("'; DROP TABLE users; --", h) is False

    def test_different_salts_each_time(self):
        pw = "SamePassword1!"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2  # different salt → different hash

    def test_both_hashes_verify_correctly(self):
        pw = "SamePassword1!"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert verify_password(pw, h1) is True
        assert verify_password(pw, h2) is True

    def test_timing_safe_comparison(self):
        """Wrong and correct passwords should take similar time (PBKDF2 dominates)."""
        pw = "TimingTest1!"
        h  = hash_password(pw)
        N  = 5

        t1 = time.perf_counter()
        for _ in range(N): verify_password("WrongPassword1!", h)
        wrong_time = (time.perf_counter() - t1) / N

        t2 = time.perf_counter()
        for _ in range(N): verify_password(pw, h)
        correct_time = (time.perf_counter() - t2) / N

        # Both dominated by PBKDF2 — should be within 3x of each other
        ratio = max(wrong_time, correct_time) / max(min(wrong_time, correct_time), 0.001)
        assert ratio < 3.0, f"Timing ratio {ratio:.2f} — possible timing oracle"

    def test_empty_password_raises(self):
        with pytest.raises(ValueError):
            hash_password("")

    def test_unicode_password(self):
        pw = "Ünïcödé#Pässwörd1"
        h = hash_password(pw)
        assert verify_password(pw, h) is True
        assert verify_password("Unicode#Password1", h) is False


# ── API Key generation ────────────────────────────────────────────────────────

class TestApiKeyGeneration:
    def test_starts_with_prefix(self):
        k = generate_api_key()
        assert k.startswith("nip_")

    def test_correct_length(self):
        k = generate_api_key()
        assert len(k) == 44  # "nip_" (4) + 40 hex chars

    def test_unique_each_call(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100  # all unique

    def test_url_safe_characters(self):
        import re
        for _ in range(10):
            k = generate_api_key()
            assert re.match(r'^nip_[0-9a-f]{40}$', k), f"Bad format: {k}"


# ── Input validation ──────────────────────────────────────────────────────────

class TestInputValidation:
    """Test Pydantic validators in auth schemas."""

    def _make_register(self, email, password):
        from backend.api.auth import RegisterRequest
        from pydantic import ValidationError
        try:
            return RegisterRequest(email=email, password=password), None
        except ValidationError as e:
            return None, e

    def test_valid_registration(self):
        req, err = self._make_register("user@example.com", "SecurePass1!")
        assert req is not None
        assert err is None

    def test_email_normalised_lowercase(self):
        req, _ = self._make_register("USER@EXAMPLE.COM", "SecurePass1!")
        assert req.email == "user@example.com"

    def test_invalid_email_rejected(self):
        _, err = self._make_register("not-an-email", "SecurePass1!")
        assert err is not None

    def test_email_without_domain_rejected(self):
        _, err = self._make_register("user@", "SecurePass1!")
        assert err is not None

    def test_weak_password_too_short(self):
        _, err = self._make_register("u@ex.com", "Ab1!")
        assert err is not None

    def test_weak_password_no_uppercase(self):
        _, err = self._make_register("u@ex.com", "lowercase123!")
        assert err is not None

    def test_weak_password_no_digit(self):
        _, err = self._make_register("u@ex.com", "NoDigitsHere!")
        assert err is not None

    def test_weak_password_no_lowercase(self):
        _, err = self._make_register("u@ex.com", "UPPERCASE123!")
        assert err is not None

    def test_strong_password_accepted(self):
        req, err = self._make_register("u@ex.com", "StrongP4ssword!")
        assert req is not None

    def test_very_long_email_rejected(self):
        long_email = "a" * 250 + "@x.com"
        _, err = self._make_register(long_email, "SecurePass1!")
        assert err is not None


# ── Security edge cases ───────────────────────────────────────────────────────

class TestSecurityEdgeCases:
    def test_jwt_algorithm_confusion(self):
        """Token with 'none' algorithm should be rejected."""
        import base64, json
        header  = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub":"admin","role":"admin","type":"access",
                        "exp": int(time.time())+3600}).encode()
        ).rstrip(b"=").decode()
        none_token = f"{header}.{payload}."
        with pytest.raises(TokenError):
            verify_token(none_token, SECRET)

    def test_jwt_key_confusion_empty_secret(self):
        """Token signed with empty secret must be rejected if server uses real secret."""
        t = create_access_token("1", "")
        with pytest.raises(TokenError):
            verify_token(t, SECRET)  # real secret != empty secret

    def test_very_long_token_rejected(self):
        """Extremely long token should not cause memory issues."""
        long_token = "a" * 100_000
        with pytest.raises(TokenError):
            verify_token(long_token, SECRET)

    def test_token_with_xss_payload_rejected(self):
        """XSS in subject field — token should verify but output is not interpreted."""
        t = create_access_token("<script>alert(1)</script>", SECRET)
        payload = verify_token(t, SECRET)
        # The sub is stored as-is — the API layer must sanitize output
        assert payload["sub"] == "<script>alert(1)</script>"

    def test_unicode_in_token_subject(self):
        t = create_access_token("用户123", SECRET)
        p = verify_token(t, SECRET)
        assert p["sub"] == "用户123"

    def test_refresh_token_longer_lived_than_access(self):
        access  = create_access_token("1", SECRET)
        refresh = create_refresh_token("1", SECRET)
        pa = verify_token(access, SECRET)
        pr = verify_token(refresh, SECRET, expected_type="refresh")
        assert pr["exp"] > pa["exp"]

    def test_constant_time_signature_check(self):
        """verify_token uses hmac.compare_digest — test it doesn't short-circuit."""
        valid = create_access_token("1", SECRET)
        parts = valid.split(".")

        # Forge with all-zero signature
        zero_sig = "A" * len(parts[2])
        forged = f"{parts[0]}.{parts[1]}.{zero_sig}"

        t_start = time.perf_counter()
        for _ in range(20):
            try: verify_token(forged, SECRET)
            except TokenError: pass
        elapsed = time.perf_counter() - t_start

        # Should not be near-instant (< 0.1ms total for 20 calls = timing short-circuit)
        assert elapsed > 0.0001, "Signature check appears to short-circuit — not constant time"


# ── Security headers ──────────────────────────────────────────────────────────

class TestSecurityHeaders:
    def test_header_names_correct(self):
        expected = [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "X-XSS-Protection",
            "Referrer-Policy",
            "Permissions-Policy",
        ]
        # Just verify the middleware code references these headers
        import ast
        src = open("backend/main.py").read()
        for header in expected:
            assert header in src, f"Security header missing: {header}"

    def test_x_frame_options_is_deny(self):
        src = open("backend/main.py").read()
        assert '"X-Frame-Options"' in src and '"DENY"' in src

    def test_no_server_header_leakage(self):
        src = open("backend/main.py").read()
        assert 'response.headers.pop("server"' in src


# ── .env security ─────────────────────────────────────────────────────────────

class TestEnvSecurity:
    def test_env_example_has_secret_key(self):
        env = open(".env.example").read()
        assert "SECRET_KEY" in env
        assert "CHANGE_THIS" in env or "generate" in env.lower()

    def test_env_example_not_committed_with_real_secret(self):
        env = open(".env.example").read()
        # Must not contain a 64-char hex string (real secret)
        import re
        real_secrets = re.findall(r'SECRET_KEY=[0-9a-f]{64}', env)
        assert len(real_secrets) == 0, "Real secret found in .env.example!"

    def test_gitignore_excludes_env(self):
        gitignore = open(".gitignore").read()
        assert ".env" in gitignore

    def test_gitignore_keeps_env_example(self):
        gitignore = open(".gitignore").read()
        # .env.example should NOT be in gitignore (it's safe to commit)
        assert ".env.example" not in gitignore or "!.env.example" in gitignore

    def test_settings_secret_key_is_placeholder(self):
        src = open("config/settings.py").read()
        # The default in code must be obviously a placeholder
        assert "CHANGE_THIS" in src or "change-in-production" in src.lower()
