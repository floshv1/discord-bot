import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def service_block(text: str, name: str) -> str:
    """No PyYAML in the project: slice from "  <name>:" to the next line indented by 0 or 2 spaces."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"  {name}:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith("   ") and not line.lstrip().startswith("#"):
            end = i
            break
    return "\n".join(lines[start:end])


def test_activity_service_is_isolated():
    block = service_block((ROOT / "compose.yml").read_text(encoding="utf-8"), "loldle-activity")
    for secret in ("DISCORD_TOKEN", "DASHBOARD_SECRET", "POSTGRES_PASSWORD"):
        assert secret not in block
    assert "networks:" not in block
    assert '"127.0.0.1:3250:8080"' in block
    assert "restart: unless-stopped" in block
    assert "condition: service_healthy" in block


def test_bot_does_not_depend_on_the_activity():
    text = (ROOT / "compose.yml").read_text(encoding="utf-8")
    bot = service_block(text, "bot")
    assert "loldle-activity" in bot  # the internal URL only
    depends = bot[bot.index("depends_on:") :]
    assert "loldle-activity" not in depends
    assert "LOLDLE_ACTIVITY_INTERNAL_URL: ${LOLDLE_ACTIVITY_INTERNAL_URL-http://loldle-activity:8080}" in bot
    assert "ACTIVITY_DB_PASSWORD: ${ACTIVITY_DB_PASSWORD:-}" in bot


def test_dev_compose_mirrors_it_with_a_build():
    block = service_block((ROOT / "compose.dev.yml").read_text(encoding="utf-8"), "loldle-activity")
    assert "dockerfile: activity/Dockerfile" in block
    assert "DISCORD_TOKEN" not in block
    assert "networks:" not in block


def test_dockerignore_keeps_secrets_out_of_every_image():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for needed in (".env", ".env.*", "**/node_modules", ".superpowers/"):
        assert needed in lines
    assert re.search(r"^!\.env\.\w*example", "\n".join(lines), re.M) is None


def _needs(ci: str, job: str) -> list[str]:
    block = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  \S|\Z)", ci, re.M | re.S).group(1)
    match = re.search(r"^    needs: \[(.*?)\]", block, re.M)
    return [n.strip() for n in match.group(1).split(",")] if match else []


def test_activity_image_is_gated_like_the_bot_image():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "docker-activity" in _needs(ci, "docker")
    # Same uv.lock as the bot: a failing pip-audit must stop it from reaching :latest.
    assert {"lint", "test", "security"} <= set(_needs(ci, "docker-activity"))
