from importlib.metadata import version

import foreman


def test_public_version_matches_distribution_metadata() -> None:
    assert foreman.__version__ == version("foreman-factory")
