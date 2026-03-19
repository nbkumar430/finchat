FROM grafana/grafana:11.4.0

# Copy provisioning config and dashboards
COPY provisioning/ /etc/grafana/provisioning/
COPY dashboards/ /var/lib/grafana/dashboards/

# Environment defaults (overridden by Cloud Run env vars)
ENV GF_SECURITY_ADMIN_PASSWORD=admin \
    GF_AUTH_ANONYMOUS_ENABLED=true \
    GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_SERVER_HTTP_PORT=3000 \
    GF_SECURITY_ALLOW_EMBEDDING=true

EXPOSE 3000
