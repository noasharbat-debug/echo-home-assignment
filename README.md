# Echo

Echo is a drop-in, from-source replacement for `nginx:1.25-bookworm` that fixes three
real CVEs (one OpenSSL dependency bump, two nginx-core source backports), ships with
vulnerability-scan evidence and a VEX document, and includes an automated
compatibility test suite that runs the original and the replacement side by side.

This project was built across five sequential, autonomous Claude Code missions. See
the **AI-tool usage transparency** section below for exactly what that means and
where it needed correction. `PROGRESS.md` in this repo is the full mission-by-mission
handoff log (command transcripts, dead ends, verification notes); this README is the
standalone summary — you shouldn't need to read `PROGRESS.md` to understand or use
this project, only to audit how it was built.

---

## Build instructions

Requirements: a working Docker daemon, and `make` (GNU Make). On Windows/Git Bash,
`make` may not be on `PATH` by default — install it (e.g. `winget install -e --id
GnuWin32.Make`) or call it via its full path
(`"/c/Program Files (x86)/GnuWin32/bin/make.exe"`), and pass `PYTHON=python3` to the
`make test`/`make all` invocation if your `python3` works but `py` (the Windows
Python Launcher default used here) doesn't exist on your machine.

From a clean clone, one command builds everything and runs the tests:

```
make all
```

This chains three steps, each usable on its own:

```
make build   # cd build && make build  — compiles nginx 1.25.5 from source with both
             # CVE fixes applied, inside a debian:bookworm-slim builder container, and
             # packages it as build/output/nginx_1.25.5-echo1_amd64.deb
make image   # docker build -f Containerfile -t echo-nginx .  — assembles the final
             # runtime image from that .deb plus the Docker-flavored entrypoint/config
             # extracted from the original image (see runtime/)
make test    # runs test/test_compat.py — starts both nginx:1.25-bookworm and
             # echo-nginx as containers and runs 6 compatibility scenarios against
             # both, side by side
```

`build/output/*.deb`, the downloaded nginx source tarball, and Docker image layers
are intentionally `.gitignore`d as build artifacts — a fresh clone will not have them
and `make build` regenerates them from source every time. That's expected, not a bug.

No `pip install` is required anywhere in this pipeline; the test harness is
stdlib-only Python (`http.client`, `socket`, `subprocess`).

A real, full clean-clone run of `make all` — cloned into a fresh temporary directory,
not the development checkout — is pasted verbatim in `PROGRESS.md`'s final "Mission
5" section as proof this actually reproduces end to end.

---

## Image size comparison

| | Bytes | MB | MiB |
|---|---|---|---|
| `nginx:1.25-bookworm` (original) | 71,005,258 | 71.0 MB | 67.7 MiB |
| `echo-nginx` (this project) | 37,544,998 | 37.5 MB | 35.8 MiB |

Echo is **~33.5 MB (~47%) smaller**, via `docker image inspect <image>
--format='{{.Size}}'` against both images on the same host. This is a side effect of
the build approach, not a design goal: the from-source `.deb` doesn't carry the
official Debian nginx packaging's extra docs and dynamic-module machinery (notably no
njs module, which was never compiled into this build's `./configure` line either —
see below), and `debian:bookworm-slim` is a leaner starting layer than whatever base
the official image's own Dockerfile builds on. Not claimed as a security improvement
by itself, just an honest side effect worth reporting.

One informational-only runtime difference: `echo-nginx`'s `Config.Env` doesn't carry
`NGINX_VERSION`/`NJS_VERSION`/`NJS_RELEASE`/`PKG_RELEASE` the way the original image's
does — these are metadata env vars only nginx-image tooling reads, not something
application code depends on, and NJS specifically was never part of this build (the
captured `./configure` line has no njs module flag), so setting `NJS_VERSION` would
have misrepresented a module that isn't actually there. Every field that governs
container *behavior* — `User`, `WorkingDir`, `Entrypoint`, `Cmd`, `ExposedPorts`,
`StopSignal` — is identical between the two images (verified with `docker inspect`).

---

## Per-CVE table

| CVE | Severity | Fix method | Evidence |
|---|---|---|---|
| **CVE-2024-6119** | High (Grype/EPSS 66.6%, 99th percentile; OpenSSL's own advisory rates it "Moderate" — noting the discrepancy) | **Bump** — `libssl3`/`openssl` upgraded from the vulnerable `3.0.11-1~deb12u2` to `3.0.20-1~deb12u2` via `apt-get install` against Debian bookworm's default repos (`bookworm/main` + `bookworm-security`), run in the runtime stage of `Containerfile` (not just the discarded builder stage) | [Trivy/aquasec advisory](https://avd.aquasec.com/nvd/cve-2024-6119) — the citation Trivy's own scan output uses |
| **CVE-2024-7347** | Low (per nginx.org's own advisory — chosen for being the cleanest available backport, not the highest severity) | **Backport** — the official nginx.org standalone patch applied with `patch -p1` to the nginx 1.25.5 source tree in `build/Dockerfile.build`, before `./configure && make`. Widens a counter from `uint32_t` to `uint64_t` in `ngx_http_mp4_crop_stsc_data()` (`src/http/modules/ngx_http_mp4_module.c`) to prevent a buffer over-read from unordered `stsc` atom chunks in a crafted mp4 file, and adds an explicit `next_chunk < chunk` rejection — the same fix nginx.org shipped upstream in 1.27.1/1.26.2 | Official patch: [`nginx.org/download/patch.2024.mp4.txt`](https://nginx.org/download/patch.2024.mp4.txt) (mirrored at `build/patches/CVE-2024-7347.patch`); advisory background: [nginx.org security advisories](https://nginx.org/en/security_advisories.html) / [nginx.org CHANGES](https://nginx.org/en/CHANGES); GHSA: [`GHSA-3r23-64c4-mj87`](https://github.com/advisories/GHSA-3r23-64c4-mj87) |
| **CVE-2026-60005** | High | **Backport** — upstream nginx commit `b99f804ad38a60ceb07bc429598d5b2c4e70e336` adds `r->ncaptures = 0` when regex captures are reallocated, preventing stale unnamed-capture bounds from exposing uninitialized memory through slice/cache subrequests. It applies to nginx 1.25.5 with a context offset and is applied before compilation | [F5 advisory K000162100](https://my.f5.com/manage/s/article/K000162100); upstream fix commit [`b99f804`](https://github.com/nginx/nginx/commit/b99f804ad38a60ceb07bc429598d5b2c4e70e336); patch: `build/patches/CVE-2026-60005.patch` |

All three fixes are recorded, with full command-level verification (patch dry-run/apply
transcripts, `nginx -V` diffs, `ldd`/`dpkg -l` proof that the fixed library is what
actually links into the shipped binary), in `PROGRESS.md`'s Mission 2 and Mission 3
sections. The Mission 6 research also fetched and dry-run-tested the candidate
areas. CVE-2026-60005 was selected because its real upstream fix is a one-line
invariant repair with a clean dry-run against 1.25.5. CVE-2026-42533 was rejected
because no equally isolated map/regex fix commit could be identified in the public
mirror and the likely backport is higher-risk; CVE-2026-56434 was rejected because
its SSI use-after-free path is broader and requires more configuration-specific
validation. The existing CVE-2026-32647 mp4 patch remains unused as prior
due-diligence evidence.

The GHSA ID above was double-checked deliberately: an earlier draft cited a
different, invented-looking GHSA ID that turned out not to exist (a live fetch of its
advisory URL 404'd). `GHSA-3r23-64c4-mj87` is the one actually confirmed to resolve
and correspond to CVE-2024-7347 — see `vex.json` and `PROGRESS.md`'s Mission 4
section for that correction in full.

---

## Vulnerability scan results

Both `nginx:1.25-bookworm` (baseline) and `echo-nginx` (fixed) were scanned with
Trivy and Grype. Raw reports are committed at the repo root:
`baseline-trivy.txt`, `baseline-grype.txt`, `fixed-trivy.txt`, `fixed-grype.txt`
(plus `fixed-trivy-vex.txt`/`fixed-grype-vex.txt`, the same fixed-image scans with
`vex.json` applied via each tool's `--vex` flag).

| Scanner | Baseline total | Fixed total |
|---|---|---|
| Trivy | 709 (UNKNOWN 34, LOW 221, MEDIUM 275, HIGH 159, CRITICAL 20) | 240 (UNKNOWN 5, LOW 87, MEDIUM 92, HIGH 52, CRITICAL 4) |
| Grype | 696 matches (High 218, Critical 44, Medium 234, Negligible 129, Low 31, Unknown 40) | 230 matches (High 57, Critical 9, Medium 67, Negligible 65, Low 8, Unknown 24) |

**Read this drop honestly, not as "660+ CVEs fixed."** Only three CVEs were
deliberately, individually fixed by this project (the table above). The rest of the
drop is mostly because `echo-nginx`'s base (`debian:bookworm-slim`, built fresh at
build time) is a more current bookworm point release than whatever base layer the
official `nginx:1.25-bookworm` image was built on — Trivy's own scan target line
shows this directly: `nginx:1.25-bookworm (debian 12.5)` vs. `echo-nginx (debian
12.15)`. A large share of the ~470/~470 count reduction is that point-release drift
on ~700 unrelated OS packages (bash, coreutils, systemd, perl, tar, etc.), not
something this project's two CVE fixes caused.

**CVE-2024-6119 specifically: confirmed gone from both scanners**, exactly as
expected for a real package-version bump — `grep -c CVE-2024-6119` against both
`fixed-trivy.txt` and `fixed-grype.txt` returns zero, vs. two matches each
(`libssl3` + `openssl`) in both baseline files.

**CVE-2024-7347 specifically: a more interesting, less clean-cut result — read the
Residual risk section below before citing this as "fixed and verified by scanning."**
It does not disappear from the scans, because it was never *in* them: neither
scanner flagged this CVE against nginx in the baseline scan of the still-vulnerable
original image either. There was nothing for the fix, or the VEX document, to make
disappear.

**CVE-2026-60005 specifically: this is the demonstrated VEX case.** With the
scanner-visible `nginx` Debian package identity, a fresh no-VEX scan of the rebuilt
image reported the CVE in both tools. Trivy's targeted output was `Total: 246` and
included `CVE-2026-60005`; Grype reported `nginx 1.25.5-echo1 ... CVE-2026-60005`.
The same image with `--vex vex.json` contained no matching CVE line in either scan;
Trivy's targeted total became 245. The exact commands and output are recorded in
`PROGRESS.md` Mission 6.

---

## Residual risk assessment

**What's still unfixed, and why:**

- The ~230–240 remaining findings in the fixed-image scans are Debian base-OS package
  noise (bash, coreutils, systemd, perl, util-linux, tar, and similar) inherited from
  `debian:bookworm-slim`, not nginx-specific and out of scope for "fix the app, not
  the whole base OS" per this project's original triage. Routine `apt-get upgrade`
  could shrink this further but wasn't the deliverable.
- Two real higher-severity nginx-core CVEs remain deliberately unbackported:
  `CVE-2026-42533` (Critical — map directive + regex heap buffer overflow) and
  `CVE-2026-56434` (High — SSI filter module use-after-free). Their upstream fixes
  need broader manual backporting and configuration-specific validation than this
  mission could justify. `CVE-2026-60005` is no longer in this list: its upstream
  fix is applied and its VEX suppression is demonstrated above.

**Scanner identity is intentional:** Mission 6 changed the Debian metadata package
name from `nginx-echo` to `nginx` while retaining the `echo-nginx` image name and
`1.25.5-echo1` version. This preserves Grype/Trivy's nginx visibility, which is
required for an honest VEX before/after demonstration; it does not change the nginx
binary, HTTP behavior, or runtime image identity.

**CVE-2024-7347's own visibility, separately:** as noted above, this CVE was never
flagged by either scanner against nginx in *either* image, before or after the fix.
Root cause (verified, not assumed — see `PROGRESS.md` Mission 4, section A.3): Trivy's
Debian OS-package database has zero entries for the `nginx` package at all (it never
had this CVE to lose), and Grype's NVD/CPE dataset for nginx 1.25.5 simply never
included this particular CVE among the 6 it does track. `vex.json` documents the true,
verified fix status (an OpenVEX `status: fixed` statement, with the real patch
mechanism, correct GHSA alias, and an explicit note about why current scanner output
can't show the before/after) so that a human or future tooling reading this repo gets
an accurate claim regardless of what either scanner's database currently covers. The
`--vex` suppression mechanism itself was verified to work correctly on this
Trivy/Grype version pair using a separate, unrelated canary CVE that *is* present in
the scan output (never committed — throwaway verification only); it genuinely
suppressed the canary in both tools. It simply has nothing to suppress for
CVE-2024-7347 given these tools' current databases.

**What we'd do next with more time:**
1. Backport at least `CVE-2026-42533` (the Critical one) properly — likely requires
   manually porting the map/regex fix commit rather than relying on a vendor patch
   file, and more test coverage around the `map` directive to validate the backport.
2. Add TLS/HTTPS test coverage (see Test coverage below — currently untested).
3. Add a live functional test for the mp4 module fix itself (a crafted malformed mp4
   file through a real `mp4` directive location), rather than relying solely on
   source-patch verification, to catch a future accidental regression.

---

## Test coverage

`make test` (`test/test_compat.py`, stdlib-only Python) starts both
`nginx:1.25-bookworm` and `echo-nginx` as containers side by side and runs 6
scenarios against both, asserting the two respond identically (except where a
difference is expected and explicitly allow-listed, like the `Date` header's
wall-clock skew):

1. **`GET /` (default page)** — status code, headers, and body of the stock welcome
   page.
2. **Custom config mount** — a config file (`test/custom-test.conf`) bind-mounted to
   `/etc/nginx/conf.d/test.conf` on a non-default port, proving the standard
   drop-in-config workflow behaves identically on both images.
3. **Large body / `client_max_body_size` boundary** — one request just under nginx's
   default 1 MB body limit and one just over it, both targeted at `/` (the default
   static-file location). This target was chosen deliberately, not arbitrarily: an
   earlier version of this test posted to the custom route's `return 200 ...;`
   handler instead, which doesn't read the request body at all, and both images hung
   identically until client timeout once the body exceeded the kernel socket buffer.
   That was itself matched behavior, not a compatibility bug, but a bad basis for an
   automated, non-flaky test — so the scenario targets `/` instead, which returns a
   clean 405/413 on both images with no hang.
4. **Malformed request (raw socket)** — a garbage request line and a request with an
   invalid HTTP version string, sent over a raw `socket` connection (not through an
   HTTP client library, which wouldn't let a malformed request line be constructed at
   all), checking both images reject them the same way.
5. **Non-existent path** — a 404 response, status/headers/body compared.
6. **Header spot-check + explicit `Server` header assertion** — every header present
   on either response is compared for exact equality except `Date` (checked only for
   presence and a parseable value); the `Server` header additionally gets its own
   dedicated assertion against the literal string `nginx/1.25.5`, so any future
   configure-flag or version drift between the two builds surfaces as a specific,
   readable test failure rather than getting silently folded into a generic header
   diff.

All 6 scenarios pass, twice in a row (idempotency check), with zero orphaned
containers left behind afterward (`docker ps -a` clean on both runs). The suite was
also deliberately run against two broken variants (never the committed script) to
confirm it actually fails loudly with a specific message and still cleans up its
containers on both an assertion failure and an infrastructure-level failure raised
before any scenario runs — see `PROGRESS.md` Mission 4, section B.5, for both
transcripts.

**What "compatibility verified" does *not* mean here** — explicitly out of scope for
this suite:
- **TLS/HTTPS.** Both images support `--with-http_ssl_module`, but no test sets up
  certificate material or exercises TLS on either side.
- **The mp4 module's actual runtime behavior.** CVE-2024-7347's fix was verified by
  diffing the patch against the real upstream fix and confirming a clean `patch -p1`
  apply during the build (see the CVE table above) — not by feeding a live crafted
  mp4 file through a running `mp4` directive location in this test suite.
- **WebSocket, HTTP/2, or HTTP/3 behavior**, despite all three being compiled in.
- **Concurrent/load behavior** — every scenario is a single sequential request.
- **Config reload (`SIGHUP`) behavior.**

This is "the common surface area behaves identically" verification, not a full
protocol- or module-level regression suite.

---

## AI-tool usage transparency

This entire project — all 5 missions, including the one that wrote this README —
was built by **Claude Code** (Anthropic's AI coding agent), run as a sequence of
autonomous background missions. Each mission started with no memory of the previous
one and picked up entirely from a written handoff (`PROGRESS.md`), which each
mission was also responsible for extending before finishing. An orchestrator session
sat above all five missions and **independently re-verified each mission's key
claims against real command output**, rather than trusting each mission's own
summary at face value — and it caught real problems doing so:

- A broken `nginx -V` capture caused by a bash redirect-order bug (`2>&1 > file`
  instead of `> file 2>&1`) that silently produced a non-empty but payload-less file
  in Mission 1 — the file looked fine (non-zero size) but didn't actually contain the
  configure-arguments output it was supposed to capture.
- **Two separate instances** of paraphrased text presented as if it were a pasted
  terminal transcript (placeholder ellipses like `[...identical to original...]`
  standing in for what should have been real command output) — caught and replaced
  with genuine re-run, pasted output in both cases.
- A fabricated-looking GHSA advisory ID in an early draft of `vex.json` that turned
  out not to resolve to a real advisory at all — caught by actually fetching the
  claimed URL and getting a 404, then correctly researching and substituting the
  real ID (`GHSA-3r23-64c4-mj87`).

**Where AI genuinely helped:** thorough, real-command-driven CVE research (fetching
nginx.org's actual advisories and CHANGES file rather than guessing version ranges
from memory, cross-referencing commit credit lines against CVE credit lines to
confirm the right commit for the right CVE); catching real subtleties in the
container build that a naive "just copy the Dockerfile" approach would have missed
(the `.deb`'s own `postinst` needing to create the `nginx` system user/group to
match the compiled `--user=nginx --group=nginx`, and the Docker image's customized
`nginx.conf`/`conf.d/default.conf` needing to be extracted and layered on top of
nginx's own plain stock config, which the `.deb` alone does not provide); and
building a genuinely raw-socket-based malformed-HTTP-request test, which an
HTTP-client-library-based test could not have constructed at all (a well-formed
client can't send a syntactically invalid request line).

**Where it needed correction:** the three items listed above, all caught by the
orchestrator's independent re-verification rather than by the authoring mission
itself. The pattern in all three cases is the same lesson: an agent's own claim of
"this works" or "here's the real output" is not sufficient evidence on its own —
spot-checking against ground truth (re-running the command, fetching the URL,
diffing the actual bytes) is what caught each issue, and is exactly the discipline
this project tried to hold itself to throughout, including in writing this section.

---

## Surprises / what we'd do differently

- **The most interesting surprise: CVE-2024-7347 never showed up in either scanner
  at all — not before the fix, not after.** The project's own working assumption
  going in (matching the general expectation that a source-level backport without a
  version-string bump would still be flagged by version-based scanners, and would
  need a VEX document to *visibly* suppress it) turned out to be wrong for this
  specific CVE, for a more interesting reason than "the VEX didn't work": there was
  never a "before" finding for either scanner to begin with; Trivy has no nginx-core
  DB coverage at all, and Grype's specific CVE set for nginx 1.25.5 doesn't happen to
  include this one. This was verified rather than assumed (a canary VEX test against
  a real, present CVE proved the `--vex` mechanism itself works correctly in both
  tools) — the absence is a genuine scanner-database gap, not a broken suppression.
- **A second, related surprise:** the `nginx-echo` package rename — a reasonable,
  arguably good choice for provenance clarity — has the unintended side effect of
  making Grype's CPE matching blind to *all* nginx-core CVEs post-fix, including the
  three genuinely unpatched ones (see Residual risk above). This wasn't anticipated
  going into the packaging decision and is a concrete example of a security-scanning
  trade-off that only became visible by actually running the scans before and after,
  rather than reasoning about it in the abstract.
- **What we'd do differently:** decide the package-naming trade-off (identity clarity
  vs. scanner visibility) deliberately up front rather than discovering it after the
  fact during the rescan; and build the raw-socket malformed-request test earlier in
  the process, since it ended up being the one scenario a naive HTTP-library-based
  approach genuinely could not have implemented at all — worth knowing before, not
  after, choosing a test-harness dependency strategy.

---

## Repository layout

```
Containerfile                     — final multi-stage image (builder + runtime stage)
Makefile                          — make all / build / image / test
build/Dockerfile.build            — from-source nginx builder (compiles + packages the .deb)
build/Makefile                    — make build / clean (wraps the docker commands above)
build/patches/CVE-2024-7347.patch — SHIPPED backport (official nginx.org patch)
build/patches/CVE-2026-32647.patch— researched fallback, NOT applied (kept as due diligence)
build/pkg/                        — .deb control file template + postinst script
runtime/                          — Docker-flavored entrypoint/config extracted verbatim
                                     from nginx:1.25-bookworm (docker-entrypoint.sh,
                                     docker-entrypoint.d/*, nginx.conf, default.conf, etc.)
test/test_compat.py               — the 6-scenario compatibility test suite
test/custom-test.conf             — test fixture for the custom-config-mount scenario
baseline-trivy.txt, baseline-grype.txt   — pre-fix scans of nginx:1.25-bookworm
fixed-trivy.txt, fixed-grype.txt         — post-fix scans of echo-nginx
fixed-trivy-vex.txt, fixed-grype-vex.txt — post-fix scans with vex.json applied
vex.json                          — OpenVEX document for CVE-2024-7347
PROGRESS.md                       — full mission-by-mission build log and evidence trail
```
