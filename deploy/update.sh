#!/usr/bin/env bash
# Redeploy the latest code on the EC2 server (data volume is preserved).
set -euo pipefail
cd /opt/jobpilot
sudo git fetch --depth 1 origin main && sudo git reset --hard origin/main
sudo docker compose up -d --build
sudo docker image prune -f
