# One allocation per GPU. Set replicas to the eligible GPU count for THIS pool.
variable "datacenters" { type = list(string) }
variable "worker_image" { type = string }
variable "model_revision" { type = string }
variable "replicas" { default = 2 }
variable "gpu_count" { default = 1 }
variable "tp_size" { default = 1 }
variable "pool" { default = "chat" }
variable "model_path" { default = "Qwen/Qwen2.5-7B-Instruct-AWQ" }
variable "model_name" { default = "chat-default" }
variable "quantization" { default = "awq_marlin" }
variable "minimum_vram" { default = "11 GiB" }
variable "maximum_vram" { default = "13 GiB" }
variable "context_length" { default = 4096 }
variable "max_running_requests" { default = 16 }
variable "extra_args" {
  type    = list(string)
  default = []
}
variable "is_embedding" { default = false }

job "sglang-chat" {
  datacenters = var.datacenters
  type        = "service"
  constraint {
    attribute = "${meta.gpu_pools}"
    operator  = "set_contains"
    value     = var.pool
  }
  group "workers" {
    count = var.replicas
    network {
      mode = "host"
      port "http" {}
    }
    # Full GPU occupancy leaves no spare GPU for a rolling canary.
    update {
      max_parallel      = 1
      canary            = 0
      health_check      = "checks"
      min_healthy_time  = "30s"
      healthy_deadline  = "15m"
      progress_deadline = "20m"
      auto_revert       = true
    }
    restart {
      attempts = 3
      interval = "30m"
      delay    = "15s"
      mode     = "fail"
    }
    reschedule {
      unlimited      = true
      delay          = "30s"
      delay_function = "exponential"
      max_delay      = "5m"
    }
    ephemeral_disk { size = 1024 }
    volume "models" {
      type      = "host"
      source    = "sglang-models"
      read_only = true
    }
    task "worker" {
      driver       = "docker"
      kill_timeout = "120s"
      config {
        image        = var.worker_image
        command      = "python3"
        network_mode = "host"
        shm_size     = 2147483648
        args = concat([
          "-m", "sglang.launch_server",
          "--model-path", var.model_path,
          "--revision", var.model_revision,
          "--served-model-name", var.model_name,
          "--host", "0.0.0.0", "--port", "${NOMAD_PORT_http}",
          "--tp-size", "${var.tp_size}",
          "--dtype", "float16",
          "--context-length", "${var.context_length}",
          "--mem-fraction-static", "0.85",
          "--max-running-requests", "${var.max_running_requests}",
          "--chunked-prefill-size", "1024",
          "--attention-backend", "triton",
          "--enable-metrics", "--enable-cache-report"
          ], var.quantization == "" ? [] : ["--quantization", var.quantization],
        var.is_embedding ? ["--is-embedding"] : [], var.extra_args)
      }
      env {
        HF_HOME        = "/models/hf"
        HF_HUB_OFFLINE = "1"
      }
      volume_mount {
        volume      = "models"
        destination = "/models"
        read_only   = true
      }
      service {
        provider     = "consul"
        name         = "sglang-worker"
        port         = "http"
        address_mode = "host"
        tags         = ["sglang", "pool=${var.pool}", "model=${var.model_name}"]
        check {
          name     = "worker-ready"
          type     = "http"
          path     = "/health"
          interval = "5s"
          timeout  = "2s"
        }
      }
      resources {
        cpu    = 4000
        memory = 16384
        device "nvidia/gpu" {
          count = var.gpu_count
          constraint {
            attribute = "${device.attr.memory}"
            operator  = ">="
            value     = var.minimum_vram
          }
          constraint {
            attribute = "${device.attr.memory}"
            operator  = "<="
            value     = var.maximum_vram
          }
        }
      }
    }
  }
}
