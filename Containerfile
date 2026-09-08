# Echo — final drop-in replacement image for nginx:1.25-bookworm.
#
# Two-stage build:
#   1. `builder` — reuses Mission 2's already-built output (the `echo-nginx-builder`
#      image, produced by `cd build && make build`, which is a `scratch`-based image
#      whose only content is /output/nginx-echo_*.deb). This stage does NOT
#      recompile nginx; it just gives us a `COPY --from=` source for the .deb.
#      PREREQUISITE: `cd build && make build` must have been run at least once so the
#      local Docker image tag `echo-nginx-builder` exists (build/output/*.deb is
#      gitignored, so a fresh checkout needs this rebuilt).
#   2. runtime — `debian:bookworm-slim`, with the OpenSSL bump fix (CVE-2024-6119)
#      installed fresh in THIS stage (not inherited from the builder stage, which is
#      discarded), the compiled/patched nginx .deb (CVE-2024-7347 backport already
#      baked in) installed via dpkg, and the Docker-image-specific runtime pieces
#      (docker-entrypoint.sh, docker-entrypoint.d/*, conf.d/default.conf, the
#      welcome-page html, log-to-stdout/stderr symlinks) restored byte-for-byte from
#      the original nginx:1.25-bookworm image under ./runtime/ (see PROGRESS.md for
#      the extraction commands and sha256 proof).

FROM echo-nginx-builder AS builder

FROM debian:bookworm-slim

LABEL maintainer="Echo Build <noasharbat@gmail.com>"

# --- CVE-2024-6119 (OpenSSL) bump fix ---------------------------------------
# Must happen in THIS stage (the one that ships) — the builder stage's own
# openssl/libssl3 upgrade is thrown away when that stage is discarded. Default
# bookworm + bookworm-security repos already carry the fixed version (verified
# in PROGRESS.md Mission 2: 3.0.20-1~deb12u2, well past the 3.0.14 minimum).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssl libssl3 libpcre3 zlib1g \
    && rm -rf /var/lib/apt/lists/*

# --- Install the compiled/patched nginx package -----------------------------
# Built by Mission 2: nginx 1.25.5 from source, exact original ./configure
# flags, CVE-2024-7347 (mp4 module) patch applied before compile. postinst
# creates the `nginx` system user/group and cache/log directories.
COPY --from=builder /output/*.deb /tmp/
RUN dpkg -i /tmp/*.deb \
      || (apt-get update && apt-get install -y -f --no-install-recommends && dpkg -i /tmp/*.deb) \
    && rm -rf /tmp/*.deb /var/lib/apt/lists/*

# --- Docker-image-specific pieces (not produced by `make install`) ---------
# All files under ./runtime/ were extracted byte-for-byte from the real
# nginx:1.25-bookworm image via `docker cp` (see PROGRESS.md Mission 3 section
# for the exact commands and sha256sums) — not rewritten from scratch.

# Docker-flavored nginx.conf: adds `include /etc/nginx/conf.d/*.conf;`,
# `user nginx;`, `worker_processes auto;`, and /var/log/nginx/* log paths.
# The .deb only ships nginx.org's plain stock nginx.conf, which this replaces.
COPY runtime/nginx.conf /etc/nginx/nginx.conf

RUN mkdir -p /etc/nginx/conf.d
COPY runtime/default.conf /etc/nginx/conf.d/default.conf
COPY runtime/usr-share-nginx-html/ /usr/share/nginx/html/
# Preserve the original image's welcome-page metadata so nginx emits the same
# Last-Modified and ETag headers as nginx:1.25-bookworm.
RUN touch -d '2024-04-16 14:29:59 UTC' /usr/share/nginx/html/index.html

COPY runtime/docker-entrypoint.sh /docker-entrypoint.sh
COPY runtime/docker-entrypoint.d/ /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.sh /docker-entrypoint.d/*

# Logs to stdout/stderr, matching the original image.
RUN ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log

# --- Runtime contract (build/original-runtime-contract.txt) ----------------
# No USER directive: the container itself runs as root, exactly like the
# original — only the nginx worker processes drop privilege, via `user nginx;`
# in nginx.conf (compiled in via --user=nginx --group=nginx).
EXPOSE 80
STOPSIGNAL SIGQUIT
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
