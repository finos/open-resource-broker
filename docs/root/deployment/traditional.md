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
pip install orb-py

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

> **Note:** Always use `--foreground` under systemd, launchd, or any other
> service manager. `orb server start` without `--foreground` performs a
> double-fork and detaches from the controlling terminal; the service manager
> interprets the parent process exiting as an immediate failure and marks the
> unit failed. `--foreground` keeps the process in the foreground so the
> supervisor owns its lifecycle.

### Recommended: Type=simple (--foreground)

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
Environment=ORB_SERVER_ENABLED=true
Environment=ORB_AUTH_ENABLED=true
Environment=ORB_CONFIG_FILE=/etc/orb/config.json
ExecStart=/opt/orb/venv/bin/orb server start --foreground
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Alternative: Type=forking (daemon mode)

For operators who want supervisor-style restart semantics but cannot use
`Type=simple`, the daemon form is supported. Use `Type=forking` together with
a `PIDFile=` directive so systemd can track the grandchild process that the
double-fork produces. **`Type=simple --foreground` is still the recommended
path** — it gives cleaner log capture and avoids the PIDFile race.

```ini
[Unit]
Description=Open Resource Broker REST API (forking)
After=network.target

[Service]
Type=forking
PIDFile=/run/orb/orb-api.pid
User=orb
Group=orb
WorkingDirectory=/opt/orb
RuntimeDirectory=orb
RuntimeDirectoryMode=0755
Environment=ORB_SERVER_ENABLED=true
Environment=ORB_AUTH_ENABLED=true
Environment=ORB_CONFIG_FILE=/etc/orb/config.json
ExecStart=/opt/orb/venv/bin/orb server start \
    --pid-file /run/orb/orb-api.pid \
    --log-file /var/log/orb/orb-api.log
ExecStop=/opt/orb/venv/bin/orb server stop \
    --pid-file /run/orb/orb-api.pid
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

> **Note:** With `Type=forking` the daemon writes its own log file
> (`--log-file`). No rotation is applied automatically — configure
> `logrotate` for that file (see [Log Rotation](#log-rotation) below).

### Log Rotation

The daemon log file is held open via `os.dup2`, so standard logrotate
`create` semantics (rename + new file) will silently continue writing to the
old inode. Use `copytruncate` instead, which copies the current file and
truncates in place:

```
/var/log/orb/orb-api.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

Place this snippet in `/etc/logrotate.d/orb-api`. When running under
`Type=simple` with `StandardOutput=journal` the daemon log goes to the
systemd journal and no logrotate config is needed.

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
