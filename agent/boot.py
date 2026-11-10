"""Container entrypoint: provision the engine login, drop privileges, start the runner.

The login is passed once as a secret (LUMELIA_AGENT_CREDENTIALS, a JSON document) and
written to the engine's credential file on a private persistent volume. Later boots
reuse the file, so tokens refreshed by the CLI survive restarts.
"""

import json
import os
from pathlib import Path

UID = 10001
credentials = Path(os.environ.get("LUMELIA_AGENT_CREDENTIALS_FILE", "/home/agent/.agent/credentials.json"))
directory = credentials.parent
directory.mkdir(parents=True, exist_ok=True)
os.chmod(directory, 0o700)
os.chown(directory, UID, UID)
raw = os.environ.pop("LUMELIA_AGENT_CREDENTIALS", "")
if not credentials.exists():
    if not isinstance(json.loads(raw or "null"), dict):
        raise RuntimeError("LUMELIA_AGENT_CREDENTIALS must hold the engine login as a JSON object")
    with os.fdopen(os.open(credentials, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
        stream.write(raw)
    os.chown(credentials, UID, UID)
os.setgid(UID)
os.setuid(UID)
os.environ.update(HOME="/home/agent", USER="agent", LUMELIA_AGENT_HOST="hosted")
print("Agent runner starting", flush=True)
os.execvp("python3", ["python3", "/app/agent/server.py"])
