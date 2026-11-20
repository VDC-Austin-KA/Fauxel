# syntax=docker/dockerfile:1
# Postgres 16 + PostGIS 3.6 (Debian trixie, live pgdg repo) + pgvector.
# The older postgis/postgis:16-3.5 is Debian bullseye; its pgdg repo moved to apt-archive.postgresql.org.
# Build it with: --build-arg BASE=postgis/postgis:16-3.5 --build-arg PGDG_ARCHIVE=1
# Behind a TLS-intercepting proxy pass the CA as a secret (compose: BUILD_CA_BUNDLE=/path/ca.crt); TLS stays on.
ARG BASE=postgis/postgis:16-3.6
FROM ${BASE}
ARG PGDG_ARCHIVE=0
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then echo 'Acquire::https::CAInfo "/run/secrets/ca";' > /etc/apt/apt.conf.d/99build-ca; fi; \
    if [ "$PGDG_ARCHIVE" = "1" ]; then \
      sed -i 's#http://apt.postgresql.org/pub/repos/apt/#http://apt-archive.postgresql.org/pub/repos/apt/#' /etc/apt/sources.list.d/pgdg.list; \
      echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99archive; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99build-ca
COPY init/ /docker-entrypoint-initdb.d/
