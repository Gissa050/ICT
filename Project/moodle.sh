#!/bin/bash
set -e

# === Basisvariabelen ===
VMNAME="moodle"
STORAGE="VM-Storage"
BRIDGE="vmbr0"
MEMORY=8192
CPUS=2
DISK_SIZE=80
CLOUD_IMAGE="/tmp/jammy-server-cloudimg-amd64.img"
CLOUD_URL="https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"

SSH_KEY_FILE_ROOT="/root/.ssh/root.pub"
SSH_KEY_FILE_ROOT_PRIVATE="/root/.ssh/root"

GW="10.24.101.254"
IP="10.24.101.114"
VMID=114
HOST_IP="$IP"

PASSWORD="SuperSecret"
SNIPPET_DIR="/var/lib/vz/snippets"
SNIPPET_STORAGE="local"

# Moodle-specifiek (nu nog niet gebruikt binnen de heredoc, maar prima)
MOODLE_DB_NAME="moodle"
MOODLE_DB_USER="moodleuser"
MOODLE_DB_PASS="StrongMoodlePassw0rd!"
MOODLE_URL="https://download.moodle.org/download.php/direct/stable405/moodle-latest-405.tgz"

# Zorg dat snippet directory bestaat
mkdir -p "$SNIPPET_DIR"

# Download cloud image als die nog niet bestaat
if [ ! -f "$CLOUD_IMAGE" ]; then
  wget -O "$CLOUD_IMAGE" "$CLOUD_URL"
fi

# Cloud-init user config snippet
cat > "$SNIPPET_DIR/moodle-user.yaml" <<EOF
#cloud-config
users:
  - name: root
    ssh_authorized_keys:
      - $(cat $SSH_KEY_FILE_ROOT)
    lock_passwd: false
ssh_pwauth: false
disable_root: false
write_files:
  - path: /etc/ssh/sshd_config.d/90-cloudinit.conf
    content: |
      PermitRootLogin yes
      PubkeyAuthentication yes
      PasswordAuthentication no

runcmd:
  - systemctl restart ssh
  - ip route add 145.37.235.0/24 via 10.24.101.1 || true

EOF

# VM aanmaken
qm create "$VMID" --name "$VMNAME" --memory "$MEMORY" \
  --cores "$CPUS" --net0 virtio,bridge="$BRIDGE"

# Disk importeren
qm importdisk "$VMID" "$CLOUD_IMAGE" "$STORAGE"
qm set "$VMID" --scsihw virtio-scsi-pci --scsi0 "$STORAGE:vm-$VMID-disk-0"

# Cloud-init device toevoegen
qm set "$VMID" \
    --ide2 "$STORAGE:cloudinit" \
    --boot order=scsi0 \
    --serial0 socket --vga serial0 \
    --agent enabled=1

# Cloud-init configuratie
qm set "$VMID" \
    --ciuser root \
    --cipassword "$PASSWORD" \
    --ipconfig0 ip="$IP"/24,gw="$GW" \
    --cicustom "user=${SNIPPET_STORAGE}:snippets/moodle-user.yaml"

qm resize "$VMID" scsi0 "${DISK_SIZE}G"

# VM starten
qm start "$VMID"
echo "VM $VMNAME met ID $VMID is gestart en gebruikt $IP"

# Oude hostkey weghalen (als die bestaat)
ssh-keygen -f "/root/.ssh/known_hosts" -R "$HOST_IP" 2>/dev/null || true

# Wachten tot de VM pingt
until ping -c1 -W2 "$IP" >/dev/null 2>&1; do
  echo "Wachten tot ${IP} pingt..."
  sleep 5
done

# Wachten tot SSH beschikbaar is
until ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -i "$SSH_KEY_FILE_ROOT_PRIVATE" root@"$IP" "echo ok" >/dev/null 2>&1; do
  echo "SSH nog niet beschikbaar..."
  sleep 5
done

# === Remote installatie via SSH ===
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY_FILE_ROOT_PRIVATE" root@"$IP" << 'EOF'
set -e

echo "=== Updates installeren ==="
apt update && apt upgrade -y

echo "=== LAMP-stack voor Moodle installeren ==="
apt install -y apache2 mariadb-server \
  php php-cli php-common php-gd php-xml php-mbstring php-intl php-zip php-curl php-ldap php-mysql

echo "=== PHP-tuning voor Moodle ==="
PHP_INI="/etc/php/8.1/apache2/php.ini"
sed -i 's/^memory_limit = .*/memory_limit = 256M/' "$PHP_INI"
sed -i 's/^upload_max_filesize = .*/upload_max_filesize = 64M/' "$PHP_INI"
sed -i 's/^post_max_size = .*/post_max_size = 64M/' "$PHP_INI"
sed -i 's/^max_execution_time = .*/max_execution_time = 300/' "$PHP_INI"

echo "=== MariaDB configureren voor Moodle ==="
systemctl enable mariadb
systemctl start mariadb

mysql <<SQL
CREATE DATABASE moodle DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'moodleuser'@'localhost' IDENTIFIED BY 'StrongMoodlePassw0rd!';
GRANT ALL PRIVILEGES ON moodle.* TO 'moodleuser'@'localhost';
FLUSH PRIVILEGES;
SQL

echo "=== Moodle downloaden en uitpakken ==="
cd /tmp
wget -O moodle.tgz "https://download.moodle.org/download.php/direct/stable405/moodle-latest-405.tgz"
tar -xzf moodle.tgz

mv /tmp/moodle /var/www/moodle

mkdir -p /var/moodledata
chown -R www-data:www-data /var/moodledata /var/www/moodle
chmod -R 755 /var/www/moodle

echo "=== Apache vhost voor Moodle configureren ==="
cat > /etc/apache2/sites-available/moodle.conf <<APACHECONF
<VirtualHost *:80>
    ServerAdmin admin@localhost
    DocumentRoot /var/www/moodle
    <Directory /var/www/moodle>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog \${APACHE_LOG_DIR}/moodle_error.log
    CustomLog \${APACHE_LOG_DIR}/moodle_access.log combined
</VirtualHost>
APACHECONF

a2dissite 000-default.conf || true
a2ensite moodle.conf
a2enmod rewrite

systemctl reload apache2

echo "=== Moodle VM basisinstallatie afgerond ==="
echo "Open nu http://$(hostname -I | awk '{print $1}') in je browser om de web-based installer te doorlopen."
EOF
