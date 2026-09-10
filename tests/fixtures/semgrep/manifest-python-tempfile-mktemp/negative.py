import tempfile


def scratch_path() -> str:
    _, path = tempfile.mkstemp()
    return path
