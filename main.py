import os
import re
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8080"))
SSHX_LOG = "/tmp/sshx.log"

sshx_process = None
sshx_link = None


def log(message):
    print(message, flush=True)


def install_sshx():
    log("[*] در حال نصب sshx ...")

    result = subprocess.run(
        ["bash", "-c", "curl -sSf https://sshx.io/get | sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        log("[!] نصب sshx ناموفق بود:")
        log(result.stdout)
        sys.exit(1)

    if result.stdout:
        log(result.stdout)


def start_sshx():
    global sshx_process

    log("[*] در حال اجرای sshx ...")

    log_file = open(SSHX_LOG, "w", buffering=1)

    sshx_process = subprocess.Popen(
        ["sshx"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    return log_file


def find_sshx_link():
    pattern = re.compile(
        r"https://sshx\.io/[A-Za-z0-9/#_-]+"
    )

    log("[*] منتظر دریافت لینک sshx ...")

    for _ in range(30):
        try:
            with open(SSHX_LOG, "r", errors="ignore") as f:
                content = f.read()

            match = pattern.search(content)

            if match:
                return match.group(0)

        except FileNotFoundError:
            pass

        time.sleep(1)

    return None


class RedirectHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", sshx_link)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", sshx_link)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_redirect_server():
    log(
        f"[*] راه‌اندازی سرور ریدایرکت روی پورت {PORT} ..."
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        RedirectHandler
    )

    log(
        f"[+] هر کسی به پورت {PORT} وصل بشه، "
        f"به {sshx_link} ریدایرکت میشه"
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cleanup(signum=None, frame=None):
    global sshx_process

    log("[*] در حال خاموش کردن ...")

    if sshx_process and sshx_process.poll() is None:
        sshx_process.terminate()

        try:
            sshx_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sshx_process.kill()

    sys.exit(0)


def main():
    global sshx_link

    log(
        f"[*] کاربر فعلی: "
        f"{os.getenv('USER', 'unknown')} "
        f"(UID: {os.getuid()})"
    )

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    install_sshx()

    log_file = start_sshx()

    try:
        sshx_link = find_sshx_link()

        if not sshx_link:
            log("[!] لینک sshx پیدا نشد.")
            log("[!] خروجی لاگ:")

            try:
                with open(SSHX_LOG, "r", errors="ignore") as f:
                    print(f.read(), flush=True)
            except FileNotFoundError:
                pass

            sys.exit(1)

        log("[+] sshx با موفقیت بالا اومد")
        log(f"[+] آدرس: {sshx_link}")

        with open("/tmp/sshx_link.txt", "w") as f:
            f.write(sshx_link)

        start_redirect_server()

    finally:
        log_file.close()


if __name__ == "__main__":
    main()
