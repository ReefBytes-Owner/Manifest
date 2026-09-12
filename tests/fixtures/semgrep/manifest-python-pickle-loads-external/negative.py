import json


def deserialize(data: bytes):
    return json.loads(data)
