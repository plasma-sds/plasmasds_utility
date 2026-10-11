"""The examples in ``examples/`` run, against the public data server.

They run in the test home (the ``home`` fixture sets the environment, which the
script's process and the notebook's kernel inherit), and use public data only, so
CI never contacts the SFTP server.
"""

import json
import subprocess
import sys
from pathlib import Path

import nbclient
import nbformat
import pytest

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.mark.network
def test_script_runs(home):
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "get_data.py")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "it says: plasmasds_utility connection test" in result.stdout
    assert str(home) in result.stdout  # the data went to the test home


@pytest.mark.network
def test_notebook_runs(home, monkeypatch):
    # Only this environment's kernel: not a "python3" kernel the developer has
    # installed for their user, pointing to another Python.
    for variable in ("JUPYTER_PATH", "JUPYTER_CONFIG_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(home / "jupyter"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    notebook = nbformat.read(EXAMPLES / "get_data.ipynb", as_version=4)
    nbclient.NotebookClient(notebook, timeout=120, kernel_name="python3").execute()
    outputs = json.dumps([cell.get("outputs", []) for cell in notebook.cells])
    assert "plasmasds_utility connection test" in outputs
    assert "connection_test.txt" in outputs
    assert home.parent.name in outputs  # the kernel used the test home


def test_notebook_is_committed_without_outputs():
    notebook = json.loads((EXAMPLES / "get_data.ipynb").read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == []
            assert cell["execution_count"] is None
