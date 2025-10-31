#!/bin/bash
set -euo pipefail

log_info() { echo -e "[`date '+%F %T'`] $*"; }

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# CONFIGURATIE VALUES VOOR TEMPLATE & VM's
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

TEMPLATE_VMID=4000
TEMPLATE_NAME="ubuntu-dso-template"
MEMORY=4096
CORES=2
BRIDGE="vmbr0"
DISK_STORAGE="ceph-storage"
DISK_SIZE=50
SSH_KEY_PATH="$HOME/.ssh/id_rsa.pub"
USER="ubuntu"
GATEWAY="10.24.20.1"
CLOUDIMG_URL="https://cloud-images.ubuntu.com/releases/22.04/release/ubuntu-22.04-server-cloudimg-amd64.img"
TMPIMG="/tmp/ubuntu-server-cloudimg-amd64.img"

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# VM DEPLOY CONFIGURATIE
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

DEPLOY_VMS=(601 602 603 604)
DEPLOY_NAMES=("giteavm" "dronecivm" "prodvm" "monitorvm")
DEPLOY_IPS=("10.24.20.61/24" "10.24.20.62/24" "10.24.20.63/24" "10.24.20.64/24" )

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# DOWNLOAD CLOUD IMAGE
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

if [ ! -f "$TMPIMG" ]; then
    log_info "Download van Ubuntu cloud image..."
    wget -q --show-progress -O "$TMPIMG" "$CLOUDIMG_URL"
fi

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# TEMPLATE VM AANMAKEN
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

vm_exists() { qm list | awk '{print $1}' | grep -q "^$1\$"; }

create_template() {
    if vm_exists "$TEMPLATE_VMID"; then
        log_info "Template $TEMPLATE_NAME ($TEMPLATE_VMID) bestaat al, overslaan."
        return
    fi

    log_info "Template $TEMPLATE_NAME ($TEMPLATE_VMID) wordt aangemaakt..."

    # Basis VM aanmaken
    qm create "$TEMPLATE_VMID" --name "$TEMPLATE_NAME" --memory "$MEMORY" --cores "$CORES" \
        --net0 virtio,bridge="$BRIDGE" --scsihw virtio-scsi-pci

    # Cloud image importeren naar storage
    qm importdisk "$TEMPLATE_VMID" "$TMPIMG" "$DISK_STORAGE" --format raw

    # Disk koppelen en aanpassen
    qm set "$TEMPLATE_VMID" --scsi0 "$DISK_STORAGE:vm-$TEMPLATE_VMID-disk-0"
    qm resize "$TEMPLATE_VMID" scsi0 "${DISK_SIZE}G"

    # Cloud-init schijf toevoegen
    qm set "$TEMPLATE_VMID" --ide2 "$DISK_STORAGE:cloudinit"

    # Bootconfiguratie en seriële console instellen
    qm set "$TEMPLATE_VMID" --boot order=scsi0 --bootdisk scsi0
    qm set "$TEMPLATE_VMID" --serial0 socket --vga serial0

    # Template markeren
    qm template "$TEMPLATE_VMID"

    log_info "Template $TEMPLATE_NAME ($TEMPLATE_VMID) is succesvol aangemaakt."
}

# Template aanmaken
create_template

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# DEPLOY VMS VANUIT TEMPLATE
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

for i in "${!DEPLOY_VMS[@]}"; do
    VMID=${DEPLOY_VMS[$i]}
    VM_NAME=${DEPLOY_NAMES[$i]}
    VM_IP=${DEPLOY_IPS[$i]}

    if vm_exists "$VMID"; then
        log_info "VM $VM_NAME ($VMID) bestaat al, overslaan..."
    else
        log_info "Clonen van template $TEMPLATE_NAME ($TEMPLATE_VMID) naar nieuwe VM $VM_NAME ($VMID)..."
        qm clone "$TEMPLATE_VMID" "$VMID" --name "$VM_NAME" --full true

        log_info "Configureren van netwerk en Cloud-Init voor $VM_NAME..."
        qm set "$VMID" --net0 virtio,bridge="$BRIDGE"
        qm set "$VMID" --ipconfig0 ip="$VM_IP",gw="$GATEWAY"
        qm set "$VMID" --sshkey "$SSH_KEY_PATH"
        qm set "$VMID" --ciuser "$USER"
        qm set "$VMID" --scsihw virtio-scsi-pci   # zorgt dat diskconfig gelijk blijft aan template
        qm resize "$VMID" scsi0 "${DISK_SIZE}G"

        log_info "Starten van $VM_NAME..."
        qm start "$VMID"
    fi
done

log_info "Alle opgegeven VM's zijn succesvol gedeployed en gestart."

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# WACHTEN TOT VM BEREIKBAAR IS (PING + SSH)
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

wait_for_vm() {
    local ip=$1
    local retries=20
    local count=0

    log_info "Wachten tot VM ($ip) reageert op ping..."
    sleep 20  # geef VM even de tijd om te booten

    until ping -c1 -W1 "$ip" &>/dev/null; do
        count=$((count+1))
        if [ "$count" -ge "$retries" ]; then
            log_info "FOUT: VM ($ip) reageert niet op ping."
            exit 1
        fi
        log_info "Ping mislukt (poging $count/$retries), opnieuw proberen in 5s..."
        sleep 5
    done

    log_info "✅ VM ($ip) is pingbaar."

    # Verwijder eventueel oude SSH key om fingerprint conflicts te voorkomen
    log_info "Verwijderen van oude SSH host key voor $ip..."
    ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$ip" &>/dev/null || true

    count=0
    log_info "Controleren of SSH-poort (22) open is op $ip..."
    until nc -z -w5 "$ip" 22 &>/dev/null; do
        count=$((count+1))
        if [ "$count" -ge "$retries" ]; then
            log_info "FOUT: SSH-poort niet bereikbaar op $ip."
            exit 1
        fi
        log_info "SSH nog niet beschikbaar (poging $count/$retries), opnieuw proberen in 5s..."
        sleep 5
    done

    log_info "SSH is bereikbaar op $ip."
}

# Toepassen op alle gedeployde VM's
for ip_cidr in "${DEPLOY_IPS[@]}"; do
    ip="${ip_cidr%%/*}"
    wait_for_vm "$ip"
done

log_info "Alle VM's zijn bereikbaar via ping en SSH."

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# DOCKER INSTALLEREN OP ELKE VM
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

install_docker() {
    local host_ip=$1

    log_info "Docker-installatie starten op $host_ip..."
    ssh-keygen -R "$host_ip" &>/dev/null || true

    ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no "$USER@$host_ip" <<'EOF'
set -euo pipefail

echo "[INFO] Controleren op APT locks..."
# Wacht tot APT-locks vrij zijn
while sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1 \
   || sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
   || sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
    echo "[INFO] Wachten op apt lock..."
    sleep 2
done

echo "[INFO] Systeem bijwerken..."
sudo DEBIAN_FRONTEND=noninteractive apt update -y
sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y

echo "[INFO] Basisvereisten installeren..."
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
    apt-transport-https ca-certificates curl \
    software-properties-common gnupg lsb-release \
    net-tools vim git

echo "[INFO] Docker repository toevoegen..."
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \$(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "[INFO] Docker installeren..."
sudo DEBIAN_FRONTEND=noninteractive apt update -y
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

echo "[INFO] Docker configureren..."
sudo usermod -aG docker \$USER
sudo systemctl enable docker
sudo systemctl start docker

echo "[INFO] Docker-installatie voltooid. Versie:"
sudo docker --version
EOF
}

# Docker installeren op alle VM's
for ip_cidr in "${DEPLOY_IPS[@]}"; do
    ip="${ip_cidr%%/*}"
    install_docker "$ip"
done

log_info "Docker is succesvol geïnstalleerd op alle VM's."

# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+
# OVERZICHT VAN GEDEPLOYDE VM'S
# +--+--+--+--+--+--+--+--+--+--+--+--+--+--+

log_info "VM-overzicht:"
printf '%-15s %-18s\n' "Naam" "IP-adres"
printf '%-15s %-18s\n' "----" "---------"

for i in "${!DEPLOY_NAMES[@]}"; do
    printf '%-15s %-18s\n' "${DEPLOY_NAMES[$i]}" "${DEPLOY_IPS[$i]}"
done

log_info "Alle VM's zijn succesvol uitgerold en geconfigureerd."
