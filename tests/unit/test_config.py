from symphony.mcp.config import load


def test_load_defaults(monkeypatch):
    for k in ("SYMPHONY_MCP_HOST", "SYMPHONY_MCP_PORT", "SYMPHONY_MCP_TOKEN",
              "SYMPHONY_MCP_ALLOWED_PROJECTS", "SYMPHONY_BASE_URL",
              "SYMPHONY_API_TOKEN", "SYMPHONY_API_TOKEN_FILE"):
        monkeypatch.delenv(k, raising=False)
    s = load()
    assert s.host == "0.0.0.0"
    assert s.port == 8080
    assert s.symphony_base_url == "http://127.0.0.1:9999"
    assert s.allowed_projects == frozenset()


def test_load_env(monkeypatch):
    monkeypatch.setenv("SYMPHONY_MCP_PORT", "9000")
    monkeypatch.setenv("SYMPHONY_MCP_TOKEN", "tok")
    monkeypatch.setenv("SYMPHONY_MCP_ALLOWED_PROJECTS", "a, b ,c")
    s = load()
    assert s.port == 9000
    assert s.token == "tok"
    assert s.allowed_projects == frozenset({"a", "b", "c"})


def test_load_token_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SYMPHONY_MCP_TOKEN", raising=False)
    f = tmp_path / "token"
    f.write_text("filetoken\n")
    monkeypatch.setenv("SYMPHONY_MCP_TOKEN_FILE", str(f))
    assert load().token == "filetoken"


def test_load_api_token_file_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    token_file = tmp_path / "api-token"
    token_file.write_text("file-api-token\n")
    monkeypatch.setenv("SYMPHONY_API_TOKEN_FILE", str(token_file))
    assert load().symphony_api_token == "file-api-token"


def test_load_api_token_env_takes_precedence(monkeypatch, tmp_path):
    token_file = tmp_path / "api-token"
    token_file.write_text("file-api-token\n")
    monkeypatch.setenv("SYMPHONY_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "env-api-token")
    assert load().symphony_api_token == "env-api-token"


def test_load_allow_control_default_off(monkeypatch):
    monkeypatch.delenv("SYMPHONY_MCP_ALLOW_CONTROL", raising=False)
    assert load().allow_control is False


def test_load_allow_control_enabled(monkeypatch):
    monkeypatch.setenv("SYMPHONY_MCP_ALLOW_CONTROL", "1")
    assert load().allow_control is True
