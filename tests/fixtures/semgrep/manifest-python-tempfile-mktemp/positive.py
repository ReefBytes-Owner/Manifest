import tempfile


def scratch_path() -> str:
    return tempfile.mktemp()
