# syntax=docker/dockerfile:1
# stella_vslam (BSD-2-Clause) SLAM worker with the equirectangular camera model.
# Copyleft isolation (CLAUDE.md rule 8): this image runs out-of-process and talks to the API over HTTP only.
# Build behind a TLS-intercepting proxy: add --secret id=ca,src=/path/ca-bundle.crt (optional; unused otherwise).
# Build: docker build -t fauxel-slam-worker services/slam-worker   (set --build-arg NUM_THREADS=$(nproc))
# Layout follows stella_vslam's own Dockerfile.desktop (no viewers), with apt Eigen/OpenCV to keep the build ~20 min.
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive DEBCONF_NOWARNINGS=yes

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      build-essential pkg-config cmake git ca-certificates wget curl unzip \
      libeigen3-dev \
      libatlas-base-dev libsuitesparse-dev libglew-dev \
      libopencv-dev \
      libyaml-cpp-dev sqlite3 libsqlite3-dev \
      libtbb-dev \
      python3 python3-pip python3-requests ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ARG NUM_THREADS=2
ARG PREFIX=/usr/local
ENV CPATH=${PREFIX}/include LIBRARY_PATH=${PREFIX}/lib LD_LIBRARY_PATH=${PREFIX}/lib

# g2o (pinned by stella_vslam docs)
ARG G2O_COMMIT=20230223_git
RUN --mount=type=secret,id=ca,required=false [ -f /run/secrets/ca ] && export GIT_SSL_CAINFO=/run/secrets/ca; git clone https://github.com/RainerKuemmerle/g2o.git /tmp/g2o && cd /tmp/g2o \
 && git checkout ${G2O_COMMIT} && mkdir build && cd build \
 && cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=${PREFIX} -DBUILD_SHARED_LIBS=ON \
      -DBUILD_UNITTESTS=OFF -DG2O_USE_CHOLMOD=OFF -DG2O_USE_CSPARSE=ON -DG2O_USE_OPENGL=OFF \
      -DG2O_USE_OPENMP=OFF -DG2O_BUILD_APPS=OFF -DG2O_BUILD_EXAMPLES=OFF -DG2O_BUILD_LINKED_APPS=OFF .. \
 && make -j${NUM_THREADS} && make install && rm -rf /tmp/g2o
ENV g2o_DIR=${PREFIX}/lib/cmake/g2o

# stella_vslam core (tag 0.7.0)
ARG STELLA_REF=0.7.0
RUN --mount=type=secret,id=ca,required=false [ -f /run/secrets/ca ] && export GIT_SSL_CAINFO=/run/secrets/ca; git clone https://github.com/stella-cv/stella_vslam.git /stella_vslam && cd /stella_vslam \
 && git checkout -q ${STELLA_REF} && git submodule update --init --recursive \
 && mkdir build && cd build \
 && cmake -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX=${PREFIX} .. \
 && make -j${NUM_THREADS} && make install

# Examples repo gives run_video_slam (commit pinned by stella_vslam 0.7.0's own Dockerfile.cuda; the older 3606f68 pin lacks `--viewer none`)
ARG EXAMPLES_COMMIT=defc69eecc36e51cdda22885bb86954f08ad6887
RUN --mount=type=secret,id=ca,required=false [ -f /run/secrets/ca ] && export GIT_SSL_CAINFO=/run/secrets/ca; git clone https://github.com/stella-cv/stella_vslam_examples.git /stella_vslam_examples && cd /stella_vslam_examples \
 && git checkout -q ${EXAMPLES_COMMIT} && git submodule update --init --recursive \
 && mkdir build && cd build \
 && cmake -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX=${PREFIX} .. \
 && make -j${NUM_THREADS} run_video_slam \
 && install -m 0755 run_video_slam /usr/local/bin/run_video_slam

# ORB vocabulary. raw.githubusercontent / raw URLs can be blocked by proxies; git clone always works.
RUN --mount=type=secret,id=ca,required=false [ -f /run/secrets/ca ] && export GIT_SSL_CAINFO=/run/secrets/ca; git clone --depth 1 https://github.com/stella-cv/FBoW_orb_vocab.git /tmp/vocab \
 && mkdir -p /opt/fauxel && cp /tmp/vocab/orb_vocab.fbow /opt/fauxel/orb_vocab.fbow && rm -rf /tmp/vocab

COPY config/ /opt/fauxel/config/
COPY fauxel_slam/ /app/fauxel_slam/
COPY run.py /app/run.py
WORKDIR /app
ENV FAUXEL_SLAM_BIN=/usr/local/bin/run_video_slam \
    FAUXEL_SLAM_VOCAB=/opt/fauxel/orb_vocab.fbow \
    FAUXEL_SLAM_CONFIG_DIR=/opt/fauxel/config
ENTRYPOINT ["python3", "/app/run.py"]
