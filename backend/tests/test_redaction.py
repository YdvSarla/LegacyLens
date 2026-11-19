import pytest

from app.services.redaction import redact_secrets


@pytest.mark.parametrize(
    "text,secret",
    [
        ('password = "hunter2hunter2"', "hunter2hunter2"),
        ("$db_password = 'abcd1234';", "abcd1234"),
        ("'api_key' => 'k-123456'", "k-123456"),
        ('AUTH_TOKEN: "tok_abc"', "tok_abc"),
        ('"client_secret": "shhh-value"', "shhh-value"),
        ("key = AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        ("t = ghp_" + "a" * 36, "ghp_" + "a" * 36),
        ("k = gsk_" + "b" * 30, "gsk_" + "b" * 30),
        ("g = AIza" + "c" * 35, "AIza" + "c" * 35),
        ("url = postgres://admin:s3cret@db/app", "s3cret"),
        ("-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----", "abc"),
    ],
)
def test_secret_values_are_removed(text, secret):
    output = redact_secrets(text)
    assert secret not in output
    assert "[REDACTED]" in output


@pytest.mark.parametrize(
    "text",
    [
        "def check_password(password):\n    return hash(password)",
        "password = request.form['password']",
        "$password = $_POST['password'];",
        "password: process.env.PASSWORD",
        'name = "secret santa"',
        "See https://example.com/docs:8080/a",
        "function secret() { return 1; }",
    ],
)
def test_ordinary_code_is_untouched(text):
    assert redact_secrets(text) == text


def test_line_count_is_preserved_outside_key_blocks():
    text = 'a = 1\npassword = "abcdefgh"\nb = 2\n'
    assert redact_secrets(text).count("\n") == text.count("\n")
