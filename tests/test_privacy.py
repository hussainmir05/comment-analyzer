from comment_analyzer import config, privacy


def test_same_commenter_same_hash_and_no_trace_of_the_id():
    hash_author = privacy.author_hasher()
    first = hash_author("UCsomeone")
    assert first == hash_author("UCsomeone")
    assert first != hash_author("UCsomeone-else")
    assert first is not None and "UCsomeone" not in first
    assert hash_author(None) is None
    assert hash_author("") is None


def test_key_is_made_once_and_reused():
    first = privacy.author_hasher()("UCsomeone")
    stored = config.get_secret(privacy.AUTHOR_KEY)
    assert stored and len(stored) == 64
    assert privacy.author_hasher()("UCsomeone") == first
    assert config.get_secret(privacy.AUTHOR_KEY) == stored


def test_another_install_gives_other_hashes():
    first = privacy.author_hasher()("UCsomeone")
    config.delete_secret(privacy.AUTHOR_KEY)
    assert privacy.author_hasher()("UCsomeone") != first


def test_a_damaged_key_is_replaced():
    config.set_secret(privacy.AUTHOR_KEY, "not hex at all")
    assert privacy.author_hasher()("UCsomeone")
    stored = config.get_secret(privacy.AUTHOR_KEY)
    assert stored is not None and bytes.fromhex(stored)
