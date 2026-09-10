import subprocess

def run(cmd: list[str]) -> None:
    subprocess.run(cmd, shell=False)
