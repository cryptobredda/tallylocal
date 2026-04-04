#!/usr/bin/env bash
# Tally Local - Proxmox LXC Installer
# Standalone script — no tteck framework dependency
# Run from Proxmox host: bash -c "$(wget -qLO - https://raw.githubusercontent.com/cryptobredda/tallylocal/main/deploy/install/tallylocal-install.sh)"

set -e

# --- Settings ---
CTID=$(pvesh get /cluster/nextid 2>/dev/null || echo "101")
HOSTNAME="tallylocal"
OS_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
STORAGE="local-lvm"
CORES="2"
RAM="4096"
DISK="8"
BRIDGE="vmbr0"
IMAGE="ghcr.io/cryptobredda/tallylocal:latest"

# --- Colors ---
GN='\033[0;32m'
YW='\033[1;33m'
RD='\033[0;31m'
CL='\033[0m'
BFR='\033[K'

info()  { echo -e "${YW}${1}${CL}"; }
ok()    { echo -e "${GN}✔️  ${1}${CL}"; }
err()   { echo -e "${RD}❌  ${1}${CL}"; exit 1; }

# --- Checks ---
[[ "$(id -u)" -ne 0 ]] && err "Run this script as root on the Proxmox host."
command -v pct >/dev/null 2>&1 || err "pct command not found — is this a Proxmox host?"
command -v pvesh >/dev/null 2>&1 || err "pvesh command not found."

# --- Check if template exists, download if missing ---
if ! pveam list local | grep -q "$OS_TEMPLATE"; then
    info "Downloading Debian 12 template..."
    pveam download local "$OS_TEMPLATE" || err "Failed to download template."
    ok "Template downloaded."
else
    ok "Template found."
fi

# --- Check for port conflict ---
if ss -tlnp | grep -q ':5000 '; then
    err "Port 5000 is already in use on the Proxmox host."
fi

# --- Create LXC ---
info "Creating LXC container ${CTID} (${HOSTNAME})..."
pct create "$CTID" "local:vztmpl/${OS_TEMPLATE}" \
    --hostname "$HOSTNAME" \
    --cores "$CORES" \
    --memory "$RAM" \
    --rootfs "${STORAGE}:${DISK}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
    --features nesting=1 \
    --unprivileged 0 \
    --onboot 1 \
    --start 1 2>&1 || err "Failed to create container."

ok "LXC container ${CTID} created and starting..."

# --- Wait for network ---
info "Waiting for network..."
for i in $(seq 1 30); do
    if pct exec "$CTID" -- ping -c 1 -W 1 1.1.1.1 >/dev/null 2>&1; then
        ok "Network is up."
        break
    fi
    if [[ $i -eq 30 ]]; then
        err "Network did not come up after 30 seconds."
    fi
    sleep 2
done

# --- Install Docker ---
info "Installing Docker inside LXC..."
pct exec "$CTID" -- bash -c '
    apt-get update -qq &&
    apt-get install -y -qq curl gnupg2 ca-certificates &&
    install -m 0755 -d /etc/apt/keyrings &&
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg &&
    chmod a+r /etc/apt/keyrings/docker.gpg &&
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list &&
    apt-get update -qq &&
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin &&
    systemctl enable docker &&
    systemctl start docker
' || err "Docker installation failed."
ok "Docker installed."

# --- Deploy Tally Local ---
info "Deploying Tally Local..."
SECRET_KEY=$(openssl rand -hex 32)
pct exec "$CTID" -- bash -c "
    mkdir -p /opt/tallylocal && cd /opt/tallylocal
    cat > docker-compose.yml <<'COMPOSE'
version: '3.9'

services:
  tallylocal:
    image: ${IMAGE}
    container_name: tallylocal
    ports:
      - \"5000:5000\"
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

volumes:
  finance_data:
COMPOSE
    docker compose pull
    docker compose up -d
" || err "Tally Local deployment failed."

# --- Get IP ---
IP=$(pct exec "$CTID" -- bash -c "hostname -I | awk '{print \$1}'")

# --- Done ---
echo ""
ok "Tally Local has been successfully deployed!"
echo ""
info "Access it at: http://${IP}:5000"
info "Container ID: ${CTID}"
info "Secret key was auto-generated and saved inside the container."
echo ""
