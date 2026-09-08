# Echo — build orchestration.
#
# The host (Windows, no Debian toolchain) never runs configure/make/dpkg-deb
# directly. Every one of those steps runs inside the debian:bookworm-slim
# based builder image defined by Dockerfile.build. This Makefile only
# shells out to `docker build` / `docker create` / `docker cp`.
#
# Usage (from the build/ directory):
#   make build   — full from-source build, produces build/output/*.deb
#   make clean   — remove local build artifacts (image + output dir)
#
# Windows note: run this from Git Bash, not PowerShell/cmd.exe directly.
# GNU Make falls back to cmd.exe as its recipe shell when sh.exe isn't on
# PATH, and cmd.exe doesn't understand POSIX flags like `mkdir -p`/`ls -la`/
# `rm -rf` (its own built-in mkdir treats "-p" as a second directory name,
# for example). Git Bash (installed with Git for Windows) supplies a real
# sh.exe and runs every line here as intended. The `-` prefixes below make
# the cleanup/listing lines failure-tolerant as a fallback, but they don't
# make cmd.exe understand POSIX syntax — Git Bash is the real fix.

IMAGE       := echo-nginx-builder
OUTPUT_DIR  := output
CONTAINER   := echo-nginx-builder-extract

.PHONY: build clean

build:
	docker build -f Dockerfile.build -t $(IMAGE) .
	-docker rm -f $(CONTAINER)
	docker create --name $(CONTAINER) $(IMAGE)
	-mkdir $(OUTPUT_DIR)
	docker cp $(CONTAINER):/output/. $(OUTPUT_DIR)/
	-docker rm -f $(CONTAINER)
	@echo "--- build/output contents ---"
	-ls -la $(OUTPUT_DIR)

clean:
	-docker rm -f $(CONTAINER)
	-docker rmi -f $(IMAGE)
	-rm -rf $(OUTPUT_DIR)
