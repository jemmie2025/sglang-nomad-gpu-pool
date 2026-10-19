#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "Usage: bash bench/link-test.sh ROUTED_PEER_IP NEW_OUTPUT_DIRECTORY" >&2
  exit 2
fi
peer=$1
out=$2
mkdir "$out"
date -u +%FT%TZ > "$out/started-utc.txt"
ip route get "$peer" > "$out/route.txt"
ping -n -c 50 -i 0.2 "$peer" > "$out/ping.txt"
iperf3 -c "$peer" -t 20 -J > "$out/iperf-forward-single.json"
iperf3 -c "$peer" -t 20 -R -J > "$out/iperf-reverse-single.json"
iperf3 -c "$peer" -t 20 -P 4 -J > "$out/iperf-forward-p4.json"
echo "Saved measurements to $out. Also measure with NCCL before reconsidering cross-host TP."
