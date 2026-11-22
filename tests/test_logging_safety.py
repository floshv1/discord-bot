"""Tracebacks must never print local variable values: loguru's default `diagnose=True` printed the
Discord token (a local in `main()`) into the container logs on the first ticker crash."""

import io
import sys

from loguru import logger


def test_tracebacks_do_not_print_local_values():
    import main

    buf = io.StringIO()
    try:
        main.configure_logging(buf)

        def boom():
            discord_token = "MTM4-SECRET-TOKEN"  # noqa: F841 - the value under test
            raise ValueError("boom")

        try:
            boom()
        except ValueError:
            logger.exception("crash")
        out = buf.getvalue()
        assert "crash" in out and "ValueError" in out  # the traceback is still logged...
        assert "MTM4-SECRET-TOKEN" not in out  # ...without the values of locals
    finally:
        logger.remove()
        logger.add(sys.stderr)
