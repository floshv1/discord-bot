import pytest

from bot.core.discord_utils import reset_render_cache


@pytest.fixture(autouse=True)
def _clear_render_cache():
    """Keep the pinned-card content guard from leaking between tests.

    `edit_if_changed` caches a fingerprint per message id for the life of the process. Test
    fixtures reuse ids (999, 1, ...), so without this one test's render would make the next
    one skip its edit — and still pass, having exercised nothing.
    """
    reset_render_cache()
    yield
    reset_render_cache()
