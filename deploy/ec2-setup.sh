#!/usr/bin/env bash
# Bootstrap JobPilot on a fresh Ubuntu 22.04/24.04 EC2 instance (free-tier t2.micro/t3.micro works).
# Usage (on the server):  bash ec2-setup.sh <git-repo-url> [branch]
# Creates /opt/jobpilot, a 2 GB swap file, installs Docker, writes .env with generated
# secrets (readable only by root) and starts the app behind Caddy with automatic HTTPS.
set -euo pipefail
REPO="${1:?usage: ec2-setup.sh <git-repo-url> [branch]}"
BRANCH="${2:-main}"
APP=/opt/jobpilot

if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if ! command -v docker >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi

if [ -d "$APP/.git" ]; then
  sudo git -C "$APP" fetch --depth 1 origin "$BRANCH" && sudo git -C "$APP" reset --hard "origin/$BRANCH"
else
  sudo git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP"
fi

cd "$APP"
if [ ! -f .env ]; then
  TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
  IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || curl -s https://checkip.amazonaws.com)
  PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
  SK=$(openssl rand -hex 32)
  sudo tee .env >/dev/null <<ENV
JOBPILOT_ENV=production
JOBPILOT_PASSWORD=$PW
JOBPILOT_SECRET_KEY=$SK
JOBPILOT_PERSISTENT_STORAGE=1
PUBLIC_HOST=${IP//./-}.sslip.io
AI_PROVIDER=none
ENV
  sudo chmod 600 .env
  echo "Generated .env. Read your access password with: sudo grep JOBPILOT_PASSWORD $APP/.env"
fi

sudo docker compose up -d --build
sudo docker compose ps
echo "JobPilot will be available at https://$(sudo grep PUBLIC_HOST .env | cut -d= -f2)/ (certificate issuance can take ~1 minute)."
