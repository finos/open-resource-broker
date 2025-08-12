#!/bin/bash

# Exit on error
set -e

# Configure logging
exec 1> >(logger -s -t $(basename $0)) 2>&1

echo "[INFO] Starting instance configuration..."

# Update system
echo "[INFO] Updating system packages..."
yum update -y
yum install -y \
    amazon-cloudwatch-agent \
    aws-cli \
    jq \
    python3 \
    python3-pip \
    htop \
    tmux

# Configure CloudWatch agent
echo "[INFO] Configuring CloudWatch agent..."
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
    "agent": {
        "metrics_collection_interval": 60,
        "run_as_user": "root"
    },
    "metrics": {
        "namespace": "HostFactory",
        "metrics_collected": {
            "cpu": {
                "measurement": [
                    "cpu_usage_idle",
                    "cpu_usage_iowait",
                    "cpu_usage_user",
                    "cpu_usage_system"
                ],
                "metrics_collection_interval": 60,
                "totalcpu": false
            },
            "disk": {
                "measurement": [
                    "used_percent",
                    "inodes_free"
                ],
                "metrics_collection_interval": 60,
                "resources": [
                    "*"
                ]
            },
            "diskio": {
                "measurement": [
                    "io_time",
                    "write_bytes",
                    "read_bytes",
                    "writes",
                    "reads"
                ],
                "metrics_collection_interval": 60,
                "resources": [
                    "*"
                ]
            },
            "mem": {
                "measurement": [
                    "mem_used_percent"
                ],
                "metrics_collection_interval": 60
            },
            "swap": {
                "measurement": [
                    "swap_used_percent"
                ],
                "metrics_collection_interval": 60
            }
        }
    },
    "logs": {
        "logs_collected": {
            "files": {
                "collect_list": [
                    {
                        "file_path": "/var/log/messages",
                        "log_group_name": "/hostfactory/system",
                        "log_stream_name": "{instance_id}/messages",
                        "retention_in_days": 7
                    },
                    {
                        "file_path": "/var/log/cloud-init-output.log",
                        "log_group_name": "/hostfactory/cloud-init",
                        "log_stream_name": "{instance_id}/cloud-init",
                        "retention_in_days": 7
                    }
                ]
            }
        }
    }
}
EOF

# Start CloudWatch agent
echo "[INFO] Starting CloudWatch agent..."
systemctl enable amazon-cloudwatch-agent
systemctl start amazon-cloudwatch-agent

# Configure instance
echo "[INFO] Configuring instance..."

# Set hostname
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
PRIVATE_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
hostnamectl set-hostname ${PRIVATE_IP}

# Configure system limits
echo "[INFO] Configuring system limits..."
cat > /etc/security/limits.d/90-hostfactory.conf << 'EOF'
*               soft    nofile          65535
*               hard    nofile          65535
*               soft    nproc           65535
*               hard    nproc           65535
*               soft    memlock         unlimited
*               hard    memlock         unlimited
EOF

# Configure sysctl
echo "[INFO] Configuring sysctl..."
cat > /etc/sysctl.d/90-hostfactory.conf << 'EOF'
# Network settings
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.tcp_fin_timeout = 30
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_probes = 5
net.ipv4.tcp_keepalive_intvl = 15

# Memory settings
vm.swappiness = 10
vm.dirty_ratio = 40
vm.dirty_background_ratio = 10

# File system settings
fs.file-max = 2097152
EOF

sysctl -p /etc/sysctl.d/90-hostfactory.conf

# Create directories
echo "[INFO] Creating application directories..."
mkdir -p /opt/hostfactory/{bin,etc,log,data}
chown -R root:root /opt/hostfactory

# Configure logging
echo "[INFO] Configuring logging..."
cat > /etc/logrotate.d/hostfactory << 'EOF'
/opt/hostfactory/log/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
EOF

# Configure motd
echo "[INFO] Configuring MOTD..."
cat > /etc/motd << 'EOF'
 _    _           _   ______          _                  
| |  | |         | | |  ____|        | |                 
| |__| | ___  ___| |_| |__ __ _  ___| |_ ___  _ __ _   _ 
|  __  |/ _ \/ __| __|  __/ _` |/ __| __/ _ \| '__| | | |
| |  | | (_) \__ \ |_| | | (_| | (__| || (_) | |  | |_| |
|_|  |_|\___/|___/\__|_|  \__,_|\___|\__\___/|_|   \__, |
                                                     __/ |
                                                    |___/ 

Instance ID: ${INSTANCE_ID}
Private IP:  ${PRIVATE_IP}

EOF

# Final message
echo "[INFO] Instance configuration complete!"
