"""Loopback transport to the OpenClaw sandbox's existing workbench."""
import argparse
import json
from http.client import HTTPException, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess


def docker(*args):
    return subprocess.check_output([shutil.which("docker") or "docker", *args], text=True).strip()


def find_container(workspace, image):
    ids = docker("ps", "-q", "--filter", f"ancestor={image}").splitlines()
    for item in ids:
        info = json.loads(docker("inspect", item))[0]
        host = info["HostConfig"]
        if not info["Name"].startswith("/openclaw-") or host["NetworkMode"] != "none" or not host["ReadonlyRootfs"]:
            continue
        if any(m["Destination"] == "/workspace" and Path(m["Source"]).resolve() == workspace
               for m in info["Mounts"]):
            return info["Id"]
    raise SystemExit("请先在 OpenClaw 打开全景 Agent，发一条消息启动沙箱。")


class Bridge(ThreadingHTTPServer):
    request_queue_size = 32


class PipeResponse:
    def __init__(self, stream):
        self.stream = stream

    def makefile(self, _mode):
        return self.stream


class Request(BaseHTTPRequestHandler):
    def proxy(self):
        self.connection.settimeout(240)
        if not self.path.startswith("/") or self.path.startswith("//"):
            self.send_error(400)
            return
        if self.headers.get("Transfer-Encoding"):
            self.send_error(400)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 48 * 1024 * 1024:
                self.send_error(413)
                return
        except ValueError:
            self.send_error(400)
            return
        command = ["docker", "exec", "-i", self.server.container, "socat", "STDIO",
                   f"TCP:127.0.0.1:{self.server.server_address[1]}"]
        sent = False
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
            try:
                request = [f"{self.command} {self.path} HTTP/1.0", "Connection: close"]
                for header in ("Host", "Origin", "Content-Type", "Content-Length"):
                    if header in self.headers:
                        request.append(f"{header}: {self.headers[header]}")
                child.stdin.write(("\r\n".join(request) + "\r\n\r\n").encode("latin-1"))
                remaining = size
                while remaining:
                    block = self.rfile.read(min(remaining, 65536))
                    if not block:
                        raise OSError("incomplete request")
                    child.stdin.write(block)
                    remaining -= len(block)
                child.stdin.flush()
                # Keep the input pipe open until the complete HTTP reply has
                # arrived. Docker/socat can drop a raw TCP half-close early.
                response = HTTPResponse(PipeResponse(child.stdout), method=self.command)
                response.begin()
                self.send_response_only(response.status, response.reason)
                for header, value in response.getheaders():
                    if header.lower() not in ("connection", "transfer-encoding"):
                        self.send_header(header, value)
                self.send_header("Connection", "close")
                self.end_headers()
                sent = True
                if self.command != "HEAD":
                    while block := response.read(65536):
                        self.wfile.write(block)
                    if response.length not in (None, 0):
                        raise OSError("incomplete sandbox response")
            except (HTTPException, OSError) as error:
                if not sent:
                    self.send_error(502, "Sandbox workbench did not return a response")
                self.log_error("%s", error)
            finally:
                child.terminate()

    do_GET = do_HEAD = do_POST = proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--image", default="space-preview-sandbox:1")
    parser.add_argument("--port", type=int, default=18792)
    args = parser.parse_args()
    container = find_container(args.workspace.resolve(), args.image)
    with Bridge(("127.0.0.1", args.port), Request) as bridge:
        bridge.container = container
        if subprocess.run(["docker", "exec", container, "python3", "-c",
                           f"from urllib.request import urlopen; urlopen('http://127.0.0.1:{args.port}/', timeout=2)"],
                          capture_output=True).returncode:
            docker("exec", "-d", container, "python3", "/workspace/app.py", "--port", str(args.port))
        print(f"http://127.0.0.1:{args.port}", flush=True)
        bridge.serve_forever()


if __name__ == "__main__":
    main()
