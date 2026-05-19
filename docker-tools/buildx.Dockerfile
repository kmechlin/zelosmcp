# We need to build our own buildx builder
# so we can install the corporate root authority cert
# (for TLS-intercepting proxy, e.g. Palo Alto)
FROM moby/buildkit:buildx-stable-1 as buildx
  ARG CERT
  COPY ${CERT} /usr/local/share/ca-certificates/cert.crt
  RUN apk add  --no-check-certificate ca-certificates && update-ca-certificates
