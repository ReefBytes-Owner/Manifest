import yaml

def parse(text: str):
    return yaml.safe_load(text)
