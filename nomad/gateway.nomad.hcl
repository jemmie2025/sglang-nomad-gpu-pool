variable "datacenters" { type = list(string) }
variable "gateway_image" { type = string }
variable "discovery_image" { type = string }
variable "consul_url" { type = string }
variable "policy" { default = "cache_aware" }

job "sglang-gateway" {
  datacenters = var.datacenters
  type        = "service"
  group "gateway" {
    count = 1
    network {
      mode = "host"
      port "http" {}
      port "metrics" {}
    }
    restart {
      attempts = 3
      interval = "10m"
      delay    = "5s"
      mode     = "fail"
    }
    reschedule {
      unlimited      = true
      delay          = "15s"
      delay_function = "exponential"
      max_delay      = "2m"
    }
    task "gateway" {
      driver       = "docker"
      kill_timeout = "120s"
      config {
        image        = var.gateway_image
        network_mode = "host"
        # Use the upstream image's gateway entrypoint; confirm it in preflight.
        args = [
          "--host", "0.0.0.0", "--port", "${NOMAD_PORT_http}",
          "--enable-igw", "--policy", var.policy,
          "--balance-abs-threshold", "4", "--balance-rel-threshold", "1.5",
          "--prometheus-host", "0.0.0.0", "--prometheus-port", "${NOMAD_PORT_metrics}",
          "--health-check-interval-secs", "2",
          "--health-check-timeout-secs", "1",
          "--health-failure-threshold", "1",
          "--health-success-threshold", "1",
          "--cb-failure-threshold", "1",
          "--retry-max-retries", "3",
          "--connect-timeout-secs", "1",
          "--request-timeout-secs", "300",
          "--max-concurrent-requests", "64",
          "--queue-size", "128", "--queue-timeout-secs", "240"
        ]
      }
      service {
        provider     = "consul"
        name         = "sglang-gateway"
        port         = "http"
        address_mode = "host"
        tags         = ["internal", "openai-compatible"]
        check {
          name     = "gateway-ready"
          type     = "http"
          path     = "/readiness"
          interval = "5s"
          timeout  = "2s"
        }
      }
      service {
        provider     = "consul"
        name         = "sglang-gateway-metrics"
        port         = "metrics"
        address_mode = "host"
        tags         = ["prometheus"]
      }
      resources {
        cpu    = 1000
        memory = 1024
      }
    }
    task "discovery" {
      driver = "docker"
      config {
        image        = var.discovery_image
        network_mode = "host"
      }
      env {
        CONSUL_HTTP_ADDR = var.consul_url
        GATEWAY_URL      = "http://127.0.0.1:${NOMAD_PORT_http}"
        WORKER_SERVICE   = "sglang-worker"
        POLL_SECONDS     = "2"
      }
      # Supply least-privilege Consul read token through Nomad Variables.
      # Omit this template only when the lab deliberately has ACLs disabled.
      template {
        destination = "secrets/consul.env"
        env         = true
        change_mode = "restart"
        data        = <<EOF
{{ with nomadVar "nomad/jobs/sglang-gateway" }}
CONSUL_HTTP_TOKEN={{ .consul_token | toJSON }}
{{ end }}
EOF
      }
      resources {
        cpu    = 250
        memory = 128
      }
    }
  }
}
