from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/ci.yml"


def test_linux_attestation_is_manual_read_only_and_exports_digests():
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert "workflow_dispatch" in document["on"]
    assert document["permissions"] == {"contents": "read"}

    job = document["jobs"]["attest-linux-x64"]
    assert job["if"] == "github.event_name == 'workflow_dispatch'"
    assert job["runs-on"] == "ubuntu-latest"
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "manifest provision" in scripts
    assert "--platform linux-x64" in scripts
    assert "--json" in scripts

    uploads = [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"] == "linux-x64-attestations.json"
