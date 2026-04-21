FROM ubuntu:22.04

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
ARG http_proxy=""
ARG https_proxy=""
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG no_proxy="localhost,127.0.0.1,::1"
ARG NO_PROXY="localhost,127.0.0.1,::1"
ARG CA_CERT_PATH="/etc/ssl/certs/ca-certificates.crt"

ARG GO_VERSION=1.25.0

ENV TZ=Etc/UTC \
    LANG=C.UTF-8 \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    SSL_CERT_FILE=${CA_CERT_PATH} \
    REQUESTS_CA_BUNDLE=${CA_CERT_PATH} \
    CURL_CA_BUNDLE=${CA_CERT_PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget build-essential jq curl locales locales-all tzdata \
    ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) && \
    wget -q "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -O /tmp/go.tar.gz && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && \
    rm /tmp/go.tar.gz

ENV PATH="/usr/local/go/bin:/root/go/bin:${PATH}" \
    GOPATH="/root/go" \
    GOFLAGS="-count=1" \
    GOTOOLCHAIN=local

RUN go install honnef.co/go/tools/cmd/staticcheck@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

# Cross-distro SSL cert symlinks
RUN mkdir -p /etc/pki/tls/certs /etc/pki/tls /etc/pki/ca-trust/extracted/pem /etc/ssl/certs && \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt 2>/dev/null; \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/cert.pem 2>/dev/null; \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/cert.pem 2>/dev/null; \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem 2>/dev/null; \
    ln -sf /etc/ssl/certs /etc/pki/tls/certs 2>/dev/null; \
    true

# MITM CA cert injection via BuildKit secret
RUN --mount=type=secret,id=mitm_ca,required=false \
    if [ -f /run/secrets/mitm_ca ]; then \
        cp /run/secrets/mitm_ca /usr/local/share/ca-certificates/mitm-ca.crt && \
        update-ca-certificates && \
        echo "MITM CA certificate installed successfully"; \
    else \
        echo "No MITM CA certificate found, skipping"; \
    fi
