# The upstream collector image has no shell or HTTP client, so a Compose healthcheck
# cannot probe it. Add a static busybox for `wget` against the health_check extension.
FROM busybox:1.37.0-musl AS busybox

FROM otel/opentelemetry-collector-contrib:0.138.0
COPY --from=busybox /bin/busybox /bin/busybox
