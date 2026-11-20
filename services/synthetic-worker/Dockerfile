# GPL-isolated synthetic data worker (Blender is GPL; this image is run out of process, nothing in services/api imports it).
# Build from the repo root:  docker build -f services/synthetic-worker/Dockerfile -t fauxel-synthetic-worker .
# Run (CPU):  docker run --rm -v $PWD/model.ifc:/in/model.ifc:ro -v $PWD/out:/out fauxel-synthetic-worker --ifc /in/model.ifc --out /out
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 BLENDER=/opt/blender/blender
ARG BLENDER_VERSION=4.2.3
ARG BLENDER_SERIES=4.2
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils python3 python3-pip \
      libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libegl1 libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# official Blender tarball (headless use; -b)
RUN mkdir -p /opt/blender && curl -fsSL https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz \
      | tar -xJ -C /opt/blender --strip-components=1 && /opt/blender/blender --version
# IfcOpenShell (LGPL) pre-step + post-step deps in the system Python
RUN pip3 install ifcopenshell numpy opencv-python-headless requests
WORKDIR /app
COPY services/synthetic-worker /app
COPY services/cv-inference/fauxel_cv /app/services/cv-inference/fauxel_cv
RUN ln -s /app/services/cv-inference /cv-inference
ENTRYPOINT ["python3", "/app/run.py"]
