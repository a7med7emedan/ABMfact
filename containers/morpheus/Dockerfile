# Morpheus 2.4.1 (BSD-3-Clause, TU Dresden) for ABMfact's morpheus engine, headless.
#
# Morpheus publishes its simulator as a static x86-64 Linux binary without the GUI; there is no arm64 build, so
# the image is amd64 and runs under emulation on Apple silicon. Build from the repository root:
#
#     docker build --platform linux/amd64 -t abmfact/morpheus:2.4.1 containers/morpheus
#
# The base image is pinned by digest and the binary by its SHA-256.
FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad

RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends gnuplot-nox \
    && rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:06a9b44dca3195536907657348e5c6882711f9ce6e60f39af71fef2362e093c7 \
    https://gitlab.com/morpheus.lab/morpheus/-/package_files/302774503/download /usr/local/bin/morpheus
RUN chmod 0755 /usr/local/bin/morpheus

WORKDIR /work
