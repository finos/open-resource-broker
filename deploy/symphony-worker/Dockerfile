# Symphony compute-worker image.
#
# ORB does not publish this image: the base must be your own IBM Spectrum
# Symphony compute image, which carries licensed binaries. Build it with:
#
#   docker build --build-arg BASE_IMAGE=<your-symphony-compute-image> \
#     -t <your-registry>/symphony-worker:<tag> deploy/symphony-worker
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Fail the build, not the liveness probe, when the base lacks a tool the
# worker scripts need.
RUN for b in sh bash pgrep uuidgen; do \
      command -v "$b" >/dev/null || { echo "missing $b"; exit 1; }; \
    done

COPY bin/ /opt/orb-worker/bin/
