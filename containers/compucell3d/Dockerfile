# CompuCell3D 4.10.0 (MIT licence) for ABMfact's compucell3d engine, headless.
#
# CompuCell3D publishes conda builds for linux-64 only (no linux-aarch64), so the image is amd64; on Apple
# silicon Docker runs it under emulation. Build from the repository root:
#
#     docker build --platform linux/amd64 -t abmfact/compucell3d:4.10.0 containers/compucell3d
#
# The base image is pinned by digest and the CompuCell3D build by version and build string.
FROM mambaorg/micromamba:2.9.0@sha256:7b93def90e48d6d7744938e31c29172e03b19bc9f56f0db512039f18efd21529

USER root
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends libgl1 libglu1-mesa libosmesa6 libxrender1 \
        libxext6 libsm6 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
USER $MAMBA_USER

RUN micromamba install -y -n base -c compucell3d -c conda-forge python=3.12 "cc3d=4.10.0=*_2" \
    && micromamba clean --all --yes

ENV CC3D_HEADLESS=1 QT_QPA_PLATFORM=offscreen
WORKDIR /work
