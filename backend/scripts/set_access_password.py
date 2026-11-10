"""Set a workspace password hash in an env file without printing the password."""

import getpass
import sys
from pathlib import Path

from argon2 import PasswordHasher

path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
password = getpass.getpass("Workspace password: ")
if len(password) < 12:
    raise SystemExit("Use at least 12 characters.")
lines = path.read_text().splitlines() if path.exists() else []
lines = [line for line in lines if not line.startswith("LUMELIA_ACCESS_PASSWORD_HASH=")]
lines.append("LUMELIA_ACCESS_PASSWORD_HASH='" + PasswordHasher().hash(password) + "'")
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
print("Password hash written. Restart the API to apply it.")
