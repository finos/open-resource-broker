# Traditional Server Deployment

## Overview

Deploy the Open Resource Broker directly on servers using traditional installation methods.

## Direct Installation

### Prerequisites

```bash
# Python 3.11+
python3 --version

# Virtual environment
python3 -m venv orb-env
source orb-env/bin/activate
```

### Installation

```bash
# Install from PyPI
pip install open-resource-broker

# Or install from source
git clone <repository-url>
cd open-resource-broker
pip install -e .
```

### Configuration

```bash
# Create configuration directory
sudo mkdir -p /etc/orb
sudo cp config/production.json /etc/orb/config.json

# Edit configuration
sudo vim /etc/orb/config.json
```

## Systemd Service

### Service File

Create `/etc/systemd/system/orb-api.service`:

```ini
[Unit]
Description=Open Resource Broker REST API
After=network.target

[Service]
Type=simple
User=orb
Group=orb
WorkingDirectory=/opt/orb
Environment=HF_SERVER_ENABLED=true
Environment=HF_AUTH_ENABLED=true
Environment=HF_CONFIG_FILE=/etc/orb/config.json
ExecStart=/opt/orb/venv/bin/python -m src.run system serve
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Service Management

```bash
# Enable and start service
sudo systemctl enable orb-api
sudo systemctl start orb-api

# Check status
sudo systemctl status orb-api

# View logs
sudo journalctl -u orb-api -f
```

## Nginx Reverse Proxy

### Configuration

```nginx
server {
    listen 80;
    server_name api.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
        access_log off;
    }
}
```

For complete deployment options, see the [main deployment guide](readme.md).
