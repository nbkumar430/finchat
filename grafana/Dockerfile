FROM prom/prometheus:v2.54.1 AS prom

FROM grafana/grafana:11.4.0

USER root

COPY --from=prom /bin/prometheus /usr/local/bin/prometheus
RUN /usr/local/bin/prometheus --version >/dev/null
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh /usr/local/bin/prometheus

# Provisioning & dashboards
COPY provisioning/ /etc/grafana/provisioning/
COPY dashboards/ /var/lib/grafana/dashboards/

# Sidecar Prometheus listens here; datasource uses this URL.
# Prototype defaults (override in Cloud Run for production): Grafana UI login admin / admin.
ENV GF_SERVER_HTTP_PORT=3000 \
    GF_SECURITY_ADMIN_USER=admin \
    GF_SECURITY_ADMIN_PASSWORD=admin \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_SECURITY_ALLOW_EMBEDDING=true \
    GF_SERVER_ROOT_URL=%(protocol)s://%(domain)s/ \
    GF_LOG_LEVEL=warn

# Set at deploy time to your FinChat app base URL, e.g. https://finchat-app-xxx.run.app
ENV FINCHAT_APP_BASE_URL=http://host.docker.internal:8080

EXPOSE 3000

ENTRYPOINT ["/entrypoint.sh"]
