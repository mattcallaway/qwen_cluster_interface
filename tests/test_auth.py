import pytest
from app.auth import (
    hash_password,
    verify_password,
    create_session_token,
    verify_session_token
)

def test_password_hashing():
    pw = "super-secret-password-123"
    hashed = hash_password(pw)
    assert hashed != pw
    assert "$" in hashed
    assert verify_password(hashed, pw) is True
    assert verify_password(hashed, "wrong-password") is False

def test_session_token_validation():
    username = "admin"
    token = create_session_token(username)
    assert token is not None
    assert ":" in token
    
    verified = verify_session_token(token)
    assert verified == username
    
    # Tampered token
    tampered = token[:-4] + "abcd"
    assert verify_session_token(tampered) is None
    
    # Invalid token format
    assert verify_session_token("invalid-token") is None
    assert verify_session_token("") is None
