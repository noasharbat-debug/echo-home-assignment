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

IMAGE       := echo-nginx-builder
OUTPUT_DIR  := output
CONTAINER   := echo-nginx-builder-extract

.PHONY: build clean

build:
	docker build -f Dockerfile.build -t $(IMAGE) .
	-docker rm -f $(CONTAINER)
	docker create --name $(CONTAINER) $(IMAGE)
	mkdir -p $(OUTPUT_DIR)
	docker cp $(CONTAINER):/output/. $(OUTPUT_DIR)/
	-docker rm -f $(CONTAINER)
	@echo "--- build/output contents ---"
	ls -la $(OUTPUT_DIR)

clean:
	-docker rm -f $(CONTAINER)
	-docker rmi -f $(IMAGE)
	rm -rf $(OUTPUT_DIR)
