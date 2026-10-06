#!/usr/bin/env python3

import os
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

SSHX_URL = (
    "https://s3.amazonaws.com/sshx/"
    "sshx-x86_64-unknown-linux-musl.tar.gz"
)

APP_DIR = Path(os.environ.get("APP_DIR", "/app"))
BIN_DIR = APP_DIR / "bin"
SSHX_PATH = BIN_DIR / "sshx"

SSHX_HOME = Path(
    os.environ.get("SSHX_HOME", "/tmp/.sshx")
)

LOG_PATH = Path(
    os.environ.get("SSHX_LOG", "/tmp/sshx.log")
)

LINK_PATH = Path(
    os.environ.get("SSHX_LINK_FILE", "/tmp/sshx_link.txt")
)

PORT = int(os.environ.get("PORT", "8080"))

LINK_REGEX = re.compile(
    r"https://sshx\.io/[^\s\"'<>]+"
)


# ============================================================
# Logging
# ============================================================

def log(message: str) -> None:
    print(message, flush=True)


# ============================================================
# Download and extract SSHX
# ============================================================

def download_sshx() -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    SSHX_HOME.mkdir(parents=True, exist_ok=True)

    # Do not download again if already available.
    if SSHX_PATH.is_file() and os.access(SSHX_PATH, os.X_OK):
        log(f"[*] SSHX binary already exists: {SSHX_PATH}")
        return

    archive = (
        Path(tempfile.gettempdir()) /
        "sshx.tar.gz"
    )

    log(f"[*] Downloading SSHX from: {SSHX_URL}")

    request = urllib.request.Request(
        SSHX_URL,
        headers={
            "User-Agent": "ConfigFars-SSHX-Runner/1.0"
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            with archive.open("wb") as output:
                while True:
                    chunk = response.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    output.write(chunk)

    except Exception as exc:
        archive.unlink(missing_ok=True)
        raise RuntimeError(
            f"Failed to download SSHX: {exc}"
        ) from exc

    log("[*] Extracting SSHX...")

    try:
        with tarfile.open(
            archive,
            "r:gz"
        ) as tar:

            members = tar.getmembers()

            sshx_member = next(
                (
                    member
                    for member in members
                    if Path(member.name).name == "sshx"
                    and member.isfile()
                ),
                None,
            )

            if sshx_member is None:
                raise RuntimeError(
                    "The archive does not contain "
                    "an sshx binary."
                )

            # Extract only the SSHX executable.
            sshx_member.name = "sshx"

            tar.extract(
                sshx_member,
                BIN_DIR
            )

    finally:
        archive.unlink(missing_ok=True)

    SSHX_PATH.chmod(0o755)

    log(
        f"[+] SSHX binary ready: {SSHX_PATH}"
    )


# ============================================================
# Start SSHX
# ============================================================

def start_sshx() -> subprocess.Popen:
    LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    log_handle = LOG_PATH.open(
        "w",
        encoding="utf-8",
        buffering=1
    )

    process = subprocess.Popen(
        [str(SSHX_PATH)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env={
            **os.environ,
            "SSHX_HOME": str(SSHX_HOME),
        },
        text=True,
    )

    # Keep the file handle alive while SSHX runs.
    process._configfars_log_handle = log_handle

    return process


# ============================================================
# Detect SSHX public URL
# ============================================================

def wait_for_link(
    process: subprocess.Popen,
    timeout: int = 30
) -> str:

    log("[*] Waiting for SSHX link...")

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:

        if LOG_PATH.exists():

            content = LOG_PATH.read_text(
                encoding="utf-8",
                errors="replace"
            )

            match = LINK_REGEX.search(content)

            if match:
                return match.group(0).rstrip(
                    ".,)"
                )

        # SSHX stopped before producing a link.
        if process.poll() is not None:
            break

        time.sleep(1)

    log("[!] SSHX link was not found.")

    if LOG_PATH.exists():

        log("[!] SSHX log:")

        print(
            LOG_PATH.read_text(
                encoding="utf-8",
                errors="replace"
            ),
            flush=True,
        )

    raise RuntimeError(
        "SSHX did not provide a public link "
        f"within {timeout} seconds."
    )


# ============================================================
# HTTP redirect server
# ============================================================

class RedirectHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):
        self.send_response(302)

        self.send_header(
            "Location",
            self.server.sshx_link
        )

        self.send_header(
            "Content-Length",
            "0"
        )

        self.end_headers()

    def do_HEAD(self):
        self.send_response(302)

        self.send_header(
            "Location",
            self.server.sshx_link
        )

        self.send_header(
            "Content-Length",
            "0"
        )

        self.end_headers()

    def log_message(
        self,
        format,
        *args
    ):
        # Disable standard HTTP logs.
        pass


def run_redirect_server(
    link: str
) -> None:

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        RedirectHandler
    )

    server.sshx_link = link

    log(
        f"[+] Redirect server listening "
        f"on 0.0.0.0:{PORT}"
    )

    log(
        f"[+] Redirect target: {link}"
    )

    try:
        server.serve_forever()

    finally:
        server.server_close()


# ============================================================
# Main
# ============================================================

def main() -> int:

    sshx_process = None

    def shutdown(
        _signum,
        _frame
    ):
        nonlocal sshx_process

        log("[*] Shutting down...")

        if (
            sshx_process
            and sshx_process.poll() is None
        ):
            sshx_process.terminate()

            try:
                sshx_process.wait(
                    timeout=5
                )

            except subprocess.TimeoutExpired:
                sshx_process.kill()

        raise SystemExit(0)

    signal.signal(
        signal.SIGTERM,
        shutdown
    )

    signal.signal(
        signal.SIGINT,
        shutdown
    )

    log(
        f"[*] Current user: "
        f"{os.getenv('USER', 'unknown')} "
        f"(UID: {os.getuid()})"
    )

    # Download SSHX directly.
    download_sshx()

    # Start SSHX without installing it.
    log(
        "[*] Starting SSHX directly "
        "(no installation)..."
    )

    sshx_process = start_sshx()

    # Wait for public URL.
    link = wait_for_link(
        sshx_process
    )

    log(
        "[+] SSHX started successfully"
    )

    log(
        f"[+] Address: {link}"
    )

    # Save link.
    LINK_PATH.write_text(
        link + "\n",
        encoding="utf-8"
    )

    # Start HTTP redirect server.
    run_redirect_server(link)

    return 0


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":

    try:
        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:
        raise SystemExit(130)

    except Exception as exc:
        print(
            f"[!] Error: {exc}",
            file=sys.stderr,
            flush=True,
        )

        raise SystemExit(1)