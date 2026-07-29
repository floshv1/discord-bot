from bot.cogs.palworld.service import TRANSITION_TTL, Player, Status, derive_status

ALICE = [Player(name="Alice", level=42)]


def test_running_stack_with_a_silent_game_is_still_booting():
    # The ~90 s boot window: the container is up, the game answers nothing yet. Reading
    # that as "online, empty" is what would let the auto-stop kill a server nobody has
    # had the chance to join.
    assert derive_status("running", None) is Status.BOOTING


def test_running_stack_answering_with_nobody_is_online_and_empty():
    assert derive_status("running", []) is Status.ONLINE


def test_running_stack_with_players_is_online():
    assert derive_status("running", ALICE) is Status.ONLINE


def test_unhealthy_counts_as_running():
    # Docker marks the container unhealthy while the healthcheck warms up, and again if
    # it flaps. The game is still there, and the players are the ground truth.
    assert derive_status("unhealthy", ALICE) is Status.ONLINE


def test_stopped_states_are_offline():
    for state in ("stopped", "created", "dead"):
        assert derive_status(state, None) is Status.OFFLINE, state


def test_destroyed_or_paused_stack_is_not_offline():
    # `docker compose start` cannot fix either, so the panel must not offer a button that
    # is guaranteed to fail.
    for state in ("down", "paused", "unknown"):
        assert derive_status(state, None) is Status.DOWN, state


def test_unreachable_komodo_is_unknown_not_offline():
    assert derive_status(None, None) is Status.UNKNOWN


def test_a_click_wins_over_a_stale_reading():
    # Komodo still reports the old state for a moment after the execution returns; the
    # card must react to the click, not to the lag.
    assert derive_status("stopped", None, transition=("starting", 0.0), now=1.0) is Status.BOOTING
    assert derive_status("running", ALICE, transition=("stopping", 0.0), now=1.0) is Status.STOPPING


def test_a_click_stops_winning_once_reality_agrees():
    # A stop that already took effect is a stopped server, whatever the click said.
    assert derive_status("stopped", None, transition=("stopping", 0.0), now=1.0) is Status.OFFLINE
    # And a start that came up is online, without waiting out the TTL.
    assert derive_status("running", ALICE, transition=("starting", 0.0), now=1.0) is Status.ONLINE


def test_a_click_that_never_took_effect_expires():
    # Otherwise a failed start leaves the card reading "démarrage…" until the next restart.
    assert derive_status("stopped", None, transition=("starting", 0.0), now=TRANSITION_TTL + 1) is Status.OFFLINE
