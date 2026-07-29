from bot.cogs.palworld.service import EMPTY_TIMEOUT, START_GRACE, EmptyWatch, Status


def test_an_empty_server_is_stopped_after_the_timeout():
    watch = EmptyWatch()
    assert watch.observe(Status.ONLINE, 0, 0.0) is False
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT - 1) is False
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT) is True


def test_one_player_resets_the_clock():
    watch = EmptyWatch()
    watch.observe(Status.ONLINE, 0, 0.0)
    watch.observe(Status.ONLINE, 1, EMPTY_TIMEOUT - 1)  # somebody joined at the last minute
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT) is False
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT * 2 - 1) is False
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT * 2) is True


def test_the_boot_window_does_not_count_as_empty():
    # BOOTING means the game did not answer, which is not the same as answering "nobody".
    # Counting it would start the shutdown clock before anyone could possibly connect.
    watch = EmptyWatch()
    watch.observe(Status.BOOTING, 0, 0.0)
    watch.observe(Status.BOOTING, 0, EMPTY_TIMEOUT)
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT + 1) is False


def test_a_komodo_outage_does_not_shut_anything_down():
    watch = EmptyWatch()
    watch.observe(Status.ONLINE, 0, 0.0)
    watch.observe(Status.UNKNOWN, 0, EMPTY_TIMEOUT)
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT + 1) is False


def test_the_grace_period_protects_whoever_just_pressed_start():
    # They still have to launch the game and load in. Stopping the server under them is
    # how a feature like this gets turned off for good.
    # Both clocks run at once and the shutdown lands on the later of the two: the server
    # has been empty long enough at EMPTY_TIMEOUT, but the grace still holds it up.
    watch = EmptyWatch()
    watch.note_start(0.0)
    assert watch.observe(Status.ONLINE, 0, 0.0) is False
    assert watch.observe(Status.ONLINE, 0, EMPTY_TIMEOUT) is False
    assert watch.observe(Status.ONLINE, 0, START_GRACE - 1) is False
    assert watch.observe(Status.ONLINE, 0, START_GRACE) is True


def test_a_start_clears_a_running_empty_clock():
    watch = EmptyWatch()
    watch.observe(Status.ONLINE, 0, 0.0)
    watch.note_start(1.0)
    assert watch.empty_since is None
