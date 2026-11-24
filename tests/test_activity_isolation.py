import os
import subprocess
import sys


def test_activity_never_imports_bot_config():
    code = (
        "import sys, activity.server.app, activity.server.__main__\n"
        "assert 'bot.core.config' not in sys.modules\n"
        "assert 'discord' not in sys.modules\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "DATABASE_URL")}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
