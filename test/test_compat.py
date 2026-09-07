#!/usr/bin/env python3
"""
Echo compatibility test suite.

Boots the original `nginx:1.25-bookworm` image and the drop-in replacement
`echo-nginx` image as two separate containers (different host ports, so they
run simultaneously) and asserts they behave identically across a fixed set
of scenarios: default page, custom-config mount, request-body size limits,
a malformed raw request, a non-existent path, and response headers
(including an explicit `Server` header check).

Design notes (see PROGRESS.md "Mission 4" section for the full narrative):

- Talks to Docker exclusively via `subprocess` calls to the `docker` CLI, and
  builds bind-mount paths with plain `os.path` (native Windows form on this
  host). This deliberately avoids shelling out through Git Bash: a bare
  `docker run -v ...` typed directly into Git Bash on this Windows host goes
  through MSYS's automatic path conversion and can silently mangle the
  container-side half of a `-v host:container` argument (see PROGRESS.md
  Mission 3's "MSYS_NO_PATHCONV" gotcha). A Python subprocess call is not an
  MSYS-runtime process, so it never triggers that conversion in the first
  place - empirically verified during this mission by mounting
  test/custom-test.conf via subprocess and reading it back correctly with no
  workaround needed. If you port this harness to Linux/macOS, nothing here
  needs to change either way.
- Uses only the Python stdlib (http.client, socket, subprocess, email.utils)
  so `make test` has zero pip dependencies.
- Every container is started through the `ManagedContainer` context manager,
  which guarantees `docker rm -f` on the way out (success OR exception), so a
  failed assertion never leaves an orphaned container holding a port.
- Readiness is polled with a real TCP-connect retry loop (bounded by
  READY_TIMEOUT_S), never a fixed `sleep`.
- The only header allow-listed as "legitimately different" is `Date` (checked
  for presence + valid HTTP-date format, never compared byte-for-byte).
  Every other header present on either side must match exactly, INCLUDING
  `Server` (explicitly asserted against "nginx/1.25.5" as its own check, not
  just implicitly caught by the generic header diff) - if the two builds'
  compiled-in module sets or nginx versions ever drifted, this is exactly the
  kind of thing that should show up as a real failure.
- The "large body" scenario intentionally targets the default "/" location,
  not the custom-test.conf route. During this mission's design pass, POSTing
  a 1MB+ body to custom-test.conf's `return 200 ...;` location was found to
  hang identically on BOTH images: nginx's `return` handler never reads the
  client body, so a client that writes the whole body before reading the
  response (as Python's http.client does, with no `Expect: 100-continue`)
  can deadlock once the body exceeds the kernel socket buffers. That hang is
  itself perfectly-matched behavior between the two images (so it is not a
  compatibility bug), but it makes a bad, flaky *test* - so this scenario
  instead POSTs to "/", the default static-file location, which rejects POST
  immediately (405) without attempting to read the body at all for
  under-limit requests, and returns 413 immediately for over-limit ones on
  both images. That is real, verified, non-hanging behavior on both images.
"""

import email.utils
import http.client
import os
import socket
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUSTOM_CONF_HOST_PATH = os.path.join(REPO_ROOT, "test", "custom-test.conf")

BASELINE_IMAGE = "nginx:1.25-bookworm"
ECHO_IMAGE = "echo-nginx"

# Different host ports per container so both run simultaneously.
BASELINE_HTTP_PORT = 18180
ECHO_HTTP_PORT = 18181
BASELINE_CUSTOM_PORT = 18280
ECHO_CUSTOM_PORT = 18281

BASELINE_CONTAINER_NAME = "echo-mission4-baseline"
ECHO_CONTAINER_NAME = "echo-mission4-echo"

READY_TIMEOUT_S = 30
READY_POLL_INTERVAL_S = 0.25

EXPECTED_SERVER_HEADER = "nginx/1.25.5"

# The only header whose VALUE is allowed to differ between the two images.
# Still checked for presence and for looking like a real HTTP-date - just
# never compared byte-for-byte, since wall-clock time will always differ.
DATE_HEADER = "date"


class ContainerError(RuntimeError):
    pass


class ManagedContainer:
    """`docker run -d` a named container; guarantees `docker rm -f` on exit
    (success or exception) so no orphaned container survives a failed test."""

    def __init__(self, image, name, port_map, mounts=None):
        self.image = image
        self.name = name
        self.port_map = port_map  # {host_port: container_port}
        self.mounts = mounts or []  # [(host_path, container_path, mode)]
        self._started = False

    def __enter__(self):
        # Best-effort cleanup of a same-named leftover from a prior crashed run.
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

        cmd = ["docker", "run", "-d", "--name", self.name]
        for host_port, container_port in self.port_map.items():
            cmd += ["-p", f"{host_port}:{container_port}"]
        for host_path, container_path, mode in self.mounts:
            host_path_norm = host_path.replace(os.sep, "/")
            cmd += ["-v", f"{host_path_norm}:{container_path}:{mode}"]
        cmd.append(self.image)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ContainerError(
                f"`docker run` failed for {self.name}: {result.stderr.strip()}"
            )
        self._started = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        return False  # never suppress the original exception

    def wait_ready(self, port, timeout=READY_TIMEOUT_S):
        """Poll until the port answers a real HTTP request, not just a raw
        TCP connect. A bare TCP-connect check is not sufficient here: nginx's
        docker-entrypoint.d scripts (worker-process tuning, envsubst, etc.)
        can still be reloading/restarting the master process for a brief
        window right after the listen socket is first bound, which accepts
        the TCP handshake and then resets the connection before answering -
        this was observed empirically during this mission (an immediate GET
        right after a successful TCP connect got RemoteDisconnected on the
        first request). Any parseable HTTP response (any status code) counts
        as ready; only connection-level failures keep the loop spinning."""
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                http_request(port, "GET", "/", timeout=2)
                return
            except (OSError, http.client.HTTPException) as e:
                last_err = e
                time.sleep(READY_POLL_INTERVAL_S)
        logs = self.logs()
        raise ContainerError(
            f"{self.name} never became ready on port {port} within "
            f"{timeout}s (last error: {last_err!r}).\n--- docker logs {self.name} ---\n{logs}"
        )

    def logs(self):
        r = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        return r.stdout + r.stderr


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def http_request(port, method, path, body=None, timeout=10):
    conn = http.client.HTTPConnection("localhost", port, timeout=timeout)
    try:
        conn.request(method, path, body=body)
        resp = conn.getresponse()
        rbody = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, rbody
    finally:
        conn.close()


def raw_tcp_roundtrip(port, raw_bytes, timeout=5):
    """Open a raw socket, write malformed/arbitrary bytes directly, and read
    back whatever comes back (or b"" if the peer just closes)."""
    s = socket.create_connection(("localhost", port), timeout=timeout)
    try:
        s.sendall(raw_bytes)
        s.settimeout(timeout)
        chunks = []
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
        return b"".join(chunks)
    finally:
        s.close()


def parse_raw_http_response(raw):
    """Best-effort parse of a raw HTTP response into (status, headers, body).
    status is None if the connection produced no bytes at all (e.g. nginx
    just closed the connection without writing a response - a valid,
    comparable outcome in its own right)."""
    if not raw:
        return None, {}, b""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status_line = lines[0].decode("iso-8859-1", errors="replace")
    parts = status_line.split(" ", 2)
    try:
        status = int(parts[1])
    except (IndexError, ValueError):
        status = None
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.decode("iso-8859-1").strip().lower()] = v.decode(
                "iso-8859-1"
            ).strip()
    return status, headers, body


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def compare_responses(label, baseline, echo, compare_body=True):
    """baseline/echo = (status, headers_dict, body). Returns a list of human
    -readable mismatch strings; empty list means the scenario passed."""
    problems = []
    b_status, b_headers, b_body = baseline
    e_status, e_headers, e_body = echo

    if b_status != e_status:
        problems.append(f"[{label}] STATUS mismatch: baseline={b_status!r} echo={e_status!r}")

    for headers, tag in ((b_headers, "baseline"), (e_headers, "echo")):
        if DATE_HEADER not in headers:
            problems.append(f"[{label}] {tag} response is missing a Date header")
        else:
            raw_date = headers[DATE_HEADER]
            parsed = email.utils.parsedate_to_datetime(raw_date)
            if parsed is None:
                problems.append(
                    f"[{label}] {tag} Date header does not look like a valid "
                    f"HTTP-date: {raw_date!r}"
                )

    keys = (set(b_headers) | set(e_headers)) - {DATE_HEADER}
    for k in sorted(keys):
        bv, ev = b_headers.get(k), e_headers.get(k)
        if bv != ev:
            problems.append(f"[{label}] header {k!r} mismatch: baseline={bv!r} echo={ev!r}")

    if compare_body and b_body != e_body:
        preview = (
            f"\n    baseline body[:200]={b_body[:200]!r}\n    echo     body[:200]={e_body[:200]!r}"
            if len(b_body) < 5000 and len(e_body) < 5000
            else ""
        )
        problems.append(
            f"[{label}] BODY mismatch: baseline={len(b_body)} bytes, echo={len(e_body)} bytes{preview}"
        )
    return problems


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

def scenario_default_page():
    """1. GET / - default welcome page: status, headers, body."""
    baseline = http_request(BASELINE_HTTP_PORT, "GET", "/")
    echo = http_request(ECHO_HTTP_PORT, "GET", "/")
    return compare_responses("GET / (default page)", baseline, echo)


def scenario_headers_and_server():
    """6. Header spot check, with an explicit Server-header assertion."""
    problems = []
    baseline = http_request(BASELINE_HTTP_PORT, "GET", "/")
    echo = http_request(ECHO_HTTP_PORT, "GET", "/")
    b_server = baseline[1].get("server")
    e_server = echo[1].get("server")
    if b_server != EXPECTED_SERVER_HEADER:
        problems.append(
            f"[Server header] baseline Server header is {b_server!r}, expected {EXPECTED_SERVER_HEADER!r}"
        )
    if e_server != EXPECTED_SERVER_HEADER:
        problems.append(
            f"[Server header] echo Server header is {e_server!r}, expected {EXPECTED_SERVER_HEADER!r}"
        )
    if b_server != e_server:
        problems.append(
            f"[Server header] baseline/echo Server headers differ: {b_server!r} vs {e_server!r}"
        )
    # General spot check on a couple of other headers we especially care about.
    for h in ("content-type", "accept-ranges"):
        bv, ev = baseline[1].get(h), echo[1].get(h)
        if bv != ev:
            problems.append(f"[headers spot check] {h!r} mismatch: baseline={bv!r} echo={ev!r}")
    return problems


def scenario_custom_config():
    """2. Custom config mount (test/custom-test.conf), same route on both."""
    baseline = http_request(BASELINE_CUSTOM_PORT, "GET", "/echo-mission3-test")
    echo = http_request(ECHO_CUSTOM_PORT, "GET", "/echo-mission3-test")
    problems = compare_responses("custom-config route", baseline, echo)
    # Sanity: this must actually be the custom route's distinctive body, not
    # some fallback/404 that happened to match on both sides by coincidence.
    for label, (status, _, body) in (("baseline", baseline), ("echo", echo)):
        if b"echo-mission3-custom-config-ok" not in body:
            problems.append(
                f"[custom-config route] {label} did not return the expected "
                f"custom-route body (status={status}, body[:100]={body[:100]!r}) "
                "- the config mount may not have taken effect."
            )
    return problems


def scenario_large_body():
    """3. client_max_body_size (default 1MB) boundary, both sides of it.

    Targets "/" (default static location) rather than the custom-test.conf
    route - see module docstring for why the custom route's `return`-based
    location is unsuitable here (it doesn't read the body at all, which can
    deadlock a client that writes >1MB before reading the response)."""
    problems = []

    under_limit_body = b"a" * (1024 * 1024 - 1)  # 1,048,575 bytes: just under 1MB
    over_limit_body = b"a" * (1024 * 1024 + 1)  # 1,048,577 bytes: just over 1MB

    b_under = http_request(BASELINE_HTTP_PORT, "POST", "/", body=under_limit_body)
    e_under = http_request(ECHO_HTTP_PORT, "POST", "/", body=under_limit_body)
    problems += compare_responses("large body (under 1MB)", b_under, e_under)
    for label, (status, _, _) in (("baseline", b_under), ("echo", e_under)):
        if status == 413:
            problems.append(
                f"[large body (under 1MB)] {label} rejected a just-under-1MB "
                f"body with 413 - the size limit is stricter than expected"
            )

    b_over = http_request(BASELINE_HTTP_PORT, "POST", "/", body=over_limit_body)
    e_over = http_request(ECHO_HTTP_PORT, "POST", "/", body=over_limit_body)
    problems += compare_responses("large body (over 1MB)", b_over, e_over)
    for label, (status, _, _) in (("baseline", b_over), ("echo", e_over)):
        if status != 413:
            problems.append(
                f"[large body (over 1MB)] {label} returned {status}, expected 413"
            )

    return problems


def scenario_malformed_request():
    """4. Malformed raw request (garbage request line + invalid HTTP version),
    via a raw socket - requests/http.client cannot construct these."""
    problems = []
    cases = {
        "garbage request line": b"GARBAGE REQUEST LINE NOT HTTP\r\n\r\n",
        "invalid HTTP version (HTTP/9.9)": b"GET / HTTP/9.9\r\nHost: localhost\r\n\r\n",
    }
    for case_name, raw_bytes in cases.items():
        b_raw = raw_tcp_roundtrip(BASELINE_HTTP_PORT, raw_bytes)
        e_raw = raw_tcp_roundtrip(ECHO_HTTP_PORT, raw_bytes)
        baseline = parse_raw_http_response(b_raw)
        echo = parse_raw_http_response(e_raw)
        label = f"malformed request ({case_name})"
        if baseline[0] is None and echo[0] is None:
            # Both sides just closed the connection with no response bytes -
            # that is a legitimate, matching outcome; nothing further to compare.
            continue
        problems += compare_responses(label, baseline, echo)
    return problems


def scenario_not_found():
    """5. Non-existent path - 404 behavior: status, headers, body."""
    baseline = http_request(BASELINE_HTTP_PORT, "GET", "/this-path-does-not-exist-echo-mission4")
    echo = http_request(ECHO_HTTP_PORT, "GET", "/this-path-does-not-exist-echo-mission4")
    return compare_responses("GET /nonexistent (404)", baseline, echo)


SCENARIOS = [
    ("1. GET / (default page)", scenario_default_page),
    ("2. Custom config mount", scenario_custom_config),
    ("3. Large body (client_max_body_size boundary)", scenario_large_body),
    ("4. Malformed request (raw socket)", scenario_malformed_request),
    ("5. Non-existent path (404)", scenario_not_found),
    ("6. Headers spot check + Server header", scenario_headers_and_server),
]


def main():
    baseline_container = ManagedContainer(
        BASELINE_IMAGE,
        BASELINE_CONTAINER_NAME,
        port_map={BASELINE_HTTP_PORT: 80, BASELINE_CUSTOM_PORT: 8888},
        mounts=[(CUSTOM_CONF_HOST_PATH, "/etc/nginx/conf.d/test.conf", "ro")],
    )
    echo_container = ManagedContainer(
        ECHO_IMAGE,
        ECHO_CONTAINER_NAME,
        port_map={ECHO_HTTP_PORT: 80, ECHO_CUSTOM_PORT: 8888},
        mounts=[(CUSTOM_CONF_HOST_PATH, "/etc/nginx/conf.d/test.conf", "ro")],
    )

    all_problems = {}

    with baseline_container, echo_container:
        print(f"Waiting for {BASELINE_CONTAINER_NAME} (port {BASELINE_HTTP_PORT}, {BASELINE_CUSTOM_PORT})...")
        baseline_container.wait_ready(BASELINE_HTTP_PORT)
        baseline_container.wait_ready(BASELINE_CUSTOM_PORT)
        print(f"Waiting for {ECHO_CONTAINER_NAME} (port {ECHO_HTTP_PORT}, {ECHO_CUSTOM_PORT})...")
        echo_container.wait_ready(ECHO_HTTP_PORT)
        echo_container.wait_ready(ECHO_CUSTOM_PORT)
        print("Both containers ready. Running scenarios...\n")

        for name, fn in SCENARIOS:
            try:
                problems = fn()
            except Exception as e:  # noqa: BLE001 - surface any scenario crash as a failure, not a silent skip
                problems = [f"[{name}] scenario raised an exception: {e!r}"]
            all_problems[name] = problems
            status = "PASS" if not problems else "FAIL"
            print(f"[{status}] {name}")
            for p in problems:
                print(f"    - {p}")

    # Containers are guaranteed torn down at this point (context managers' __exit__ ran).
    total_failures = sum(len(v) for v in all_problems.values())
    print("\n" + "=" * 70)
    if total_failures == 0:
        print(f"ALL {len(SCENARIOS)} SCENARIOS PASSED")
        return 0
    else:
        failed_scenarios = [n for n, v in all_problems.items() if v]
        print(f"{len(failed_scenarios)}/{len(SCENARIOS)} SCENARIOS FAILED "
              f"({total_failures} total mismatches):")
        for n in failed_scenarios:
            print(f"  - {n}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
