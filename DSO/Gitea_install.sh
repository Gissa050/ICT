#!/bin/bash
set -euo pipefail

log_info() { echo -e "[`date '+%F %T'`] $*"; }

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Gitea installeren 
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

GITEA_VERSION="1.25.0"
GITEA_USER="git"
GITEA_HOME="/home/${GITEA_USER}"
GITEA_WORKDIR="/var/lib/gitea"
GITEA_CONF_DIR="/etc/gitea"
GITEA_BIN="/usr/local/bin/gitea"

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Installeren van vereisten 
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

log "Installeer afhankelijkheden..."
sudo apt update -y
sudo apt install -y wget git curl gpg lsb-release ca-certificates

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Downloaden van Gitea + validatie
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

cd /tmp
log "Download Gitea ${GITEA_VERSION} binary..."
wget -q -O gitea "https://dl.gitea.com/gitea/${GITEA_VERSION}/gitea-${GITEA_VERSION}-linux-amd64"
wget -q "https://dl.gitea.com/gitea/${GITEA_VERSION}/gitea-${GITEA_VERSION}-linux-amd64.asc"

log "Verifieer GPG-handtekening..."
gpg --keyserver hkps://keys.openpgp.org --recv-keys 7C9E68152594688862D62AF62D9AE806EC1592E2
gpg --verify gitea-${GITEA_VERSION}-linux-amd64.asc gitea

chmod +x gitea
sudo mv gitea ${GITEA_BIN}

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Aanmakenn van Gitea systemuser
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

if ! id -u "${GITEA_USER}" >/dev/null 2>&1; then
  log "Gebruiker ${GITEA_USER} aanmaken..."
  sudo adduser \
    --system \
    --shell /bin/bash \
    --gecos 'Git Version Control' \
    --group \
    --disabled-password \
    --home "${GITEA_HOME}" \
    "${GITEA_USER}"
fi

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Aanmaken van direcotrystructuur
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

log "Maak directorystructuur aan..."
sudo mkdir -p ${GITEA_WORKDIR}/{custom,data,log}
sudo chown -R ${GITEA_USER}:${GITEA_USER} ${GITEA_WORKDIR}
sudo chmod -R 750 ${GITEA_WORKDIR}

sudo mkdir -p ${GITEA_CONF_DIR}
sudo chown root:${GITEA_USER} ${GITEA_CONF_DIR}
sudo chmod 770 ${GITEA_CONF_DIR}

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Configureren systemd-service
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

log "Maak systemd-service aan..."

cat <<EOF | sudo tee /etc/systemd/system/gitea.service > /dev/null
[Unit]
Description=Gitea (Git with a cup of tea)
After=network.target

[Service]
RestartSec=2s
Type=simple
User=${GITEA_USER}
Group=${GITEA_USER}
WorkingDirectory=${GITEA_WORKDIR}
ExecStart=${GITEA_BIN} web --config ${GITEA_CONF_DIR}/app.ini
Restart=always
Environment=USER=${GITEA_USER} HOME=${GITEA_HOME}
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable gitea

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Starten van Gitea
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

log "Start Gitea service..."
sudo systemctl start gitea
sleep 3
sudo systemctl status gitea --no-pager

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# Output informatie
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

IP=$(hostname -I | awk '{print $1}')
log "Gitea draait nu op: http://${IP}:3000"
log "Ga in je browser naar dit adres om de web installer af te ronden."
log "Na installatie: chmod 750 ${GITEA_CONF_DIR} && chmod 640 ${GITEA_CONF_DIR}/app.ini"