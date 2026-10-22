# PhysiCell 1.14.2 build environment for ABMfact's PhysiCell engine.
#
# Build:  docker build -t abmfact/physicell:1.14.2 containers/physicell
#
# The image holds a compiler with OpenMP and the PhysiCell 1.14.2 sources in /opt/PhysiCell. ABMfact
# builds each project inside it, against those sources, and runs the binary in the same image, so the
# image digest pins the engine. PhysiCell is BSD 3-Clause (Paul Macklin and the PhysiCell Project).
FROM debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8

ARG PHYSICELL_VERSION=1.14.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ make git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${PHYSICELL_VERSION}" https://github.com/MathCancer/PhysiCell.git /opt/PhysiCell \
    && rm -rf /opt/PhysiCell/.git

ENV PHYSICELL_SOURCE=/opt/PhysiCell

LABEL org.opencontainers.image.title="abmfact-physicell" \
      org.opencontainers.image.version="1.14.2" \
      org.opencontainers.image.description="PhysiCell 1.14.2 sources and a g++ with OpenMP, for ABMfact" \
      org.opencontainers.image.licenses="BSD-3-Clause"

WORKDIR /work
