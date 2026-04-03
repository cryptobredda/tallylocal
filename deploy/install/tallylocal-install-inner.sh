#!/usr/bin/env bash
# Inner install script — executed inside the LXC by build_container()
# Sets up Tally Local using the published Docker image from GHCR

REPO="YOUR_USERNAME/tallylocal"
IMAGE="ghcr.io/${REPO}:latest"
INSTALL_DIR="/opt/tallylocal"

set -e

# Generate a random secret key
SECRET_KEY=$(openssl rand -hex 32)

# Create install directory
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Write docker-compose.yml
cat <<EOF > docker-compose.yml
version: '3.9'

services:
  finance:
    image: ${IMAGE}
    container_name: tallylocal
    ports:
      - "5000:5000"
    environment:
      - FLASK_ENV=production
      - SECRET_KEY=${SECRET_KEY}
      - DATA_DIR=/data
      - USERS_DB_PATH=/data/users.db
      - USER_DATA_DIR=/data/user_data
      - UPLOADS_DIR=/data/uploads
    volumes:
      - finance_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/"]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  finance_data:
EOF

# Pull and start
docker compose pull
docker compose up -d

msg_ok "Tally Local is running!"
