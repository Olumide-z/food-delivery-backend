from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    verify_access_token,
    verify_refresh_token,
    generate_verification_code,
    generate_reset_token,
    hash_reset_token,
)


def test_password_hashing():
    raw_password = "SecretPassword123!"
    hashed = hash_password(raw_password)
    assert hashed != raw_password
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False


def test_access_token_creation_and_verification():
    user_id = "42"
    token = create_access_token(data={"sub": user_id})
    verified_id = verify_access_token(token)
    assert verified_id == user_id


def test_refresh_token_creation_and_verification():
    user_id = "42"
    token = create_refresh_token(data={"sub": user_id})
    verified_id = verify_refresh_token(token)
    assert verified_id == user_id


def test_access_token_rejected_as_refresh_token():
    token = create_access_token(data={"sub": "42"})
    assert verify_refresh_token(token) is None


def test_refresh_token_rejected_as_access_token():
    token = create_refresh_token(data={"sub": "42"})
    assert verify_access_token(token) is None


def test_verification_code_generation():
    code = generate_verification_code()
    assert len(code) == 6
    assert code.isdigit()


def test_reset_token_hashing():
    token = generate_reset_token()
    token_hash = hash_reset_token(token)
    assert len(token) > 20
    assert len(token_hash) == 64  # SHA256 hex string length
