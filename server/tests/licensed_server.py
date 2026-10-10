"""Licences for tests, signed with a throwaway vendor key instead of the real one.

In pytest, conftest.py makes the server trust TEST_PUB. Tests that run AT-SUIT as its
own process (and the Windows app's tests) start it with this file instead of uvicorn:

    python tests/licensed_server.py --port 18180 [--host 127.0.0.1]

It trusts its own throwaway key and writes a matching licence key to
$ATSUIT_DATA/test-licence.txt, to pass as licence_key to /api/setup.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


_PRIV = Ed25519PrivateKey.generate()
TEST_PUB = _b64(_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def licence_key(**payload) -> str:
    """A key signed by the test vendor: unlimited and never ending, unless told otherwise."""
    body = {"licensee": "Test Venue", "edition": "standard", "max_nodes": 0, "max_sites": 0, "modules": ["*"],
            "expires": 0, "issued": int(time.time()) - 60, "serial": "AT-TEST", **payload}
    raw = json.dumps(body, separators=(",", ":")).encode()
    return f"{_b64(raw)}.{_b64(_PRIV.sign(raw))}"


def trust_test_key() -> None:
    from atsuit import licence

    licence._vendor_raw = lambda: (TEST_PUB, "test vendor key")
    licence.forget()


def main(argv: list[str]) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import uvicorn

    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8080
    host = argv[argv.index("--host") + 1] if "--host" in argv else "127.0.0.1"
    data = Path(os.environ.get("ATSUIT_DATA", "./data"))
    data.mkdir(parents=True, exist_ok=True)
    (data / "test-licence.txt").write_text(licence_key())
    trust_test_key()
    from atsuit.main import app

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
