# Traditional Server Deployment

## Overview

Deploy the Open Host Factory Plugin directly on servers using traditional installation methods.

## Direct Installation

### Prerequisites

```bash
# Python 3.11+
python3 --version

# Virtual environment
python3 -m venv ohfp-env
source ohfp-env/bin/activate
```

### Installation

```bash
# Install from PyPI
pip install open-hostfactory-plugin

# Or install from source
git clone <repository-url>
cd open-hostfactory-plugin
pip install -e .
```

### Configuration

```bash
# Create configuration directory
sudo mkdir -p /etc/ohfp
sudo cp config/production.json /etc/ohfp/config.json

# Edit configuration
sudo vim /etc/ohfp/config.json
```

## Systemd Service

### Service File

Create `/etc/systemd/system/ohfp-api.service`:

```ini
[Unit]
Description=Open Host Factory Plugin REST API
After=network.target

[Service]
Type=simple
User=ohfp
Group=ohfp
WorkingDirectory=/opt/ohfp
Environment=HF_SERVER_ENABLED=true
Environment=HF_AUTH_ENABLED=true
Environment=HF_CONFIG_FILE=/etc/ohfp/config.json
ExecStart=/opt/ohfp/venv/bin/python -m src.run system serve
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
sudo systemctl enable ohfp-api
sudo systemctl start ohfp-api

# Check status
sudo systemctl status ohfp-api

# View logs
sudo journalctl -u ohfp-api -f
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
