#!/bin/bash
# Deploy latest code from git to both /opt/loyalcorp and /opt/loyalcorp-manager
set -e

cd /opt/loyalcorp
git fetch origin
git reset --hard origin/master

cp /opt/loyalcorp/manager/api.py /opt/loyalcorp-manager/
cp /opt/loyalcorp/manager/instance_manager.py /opt/loyalcorp-manager/
cp /opt/loyalcorp/manager/db.py /opt/loyalcorp-manager/
cp /opt/loyalcorp/manager/settings.py /opt/loyalcorp-manager/

# Install manager systemd unit if not already present
if [ ! -f /etc/systemd/system/loyalcorp-manager.service ]; then
    cp /opt/loyalcorp/manager/templates/manager.service /etc/systemd/system/loyalcorp-manager.service
    systemctl daemon-reload
    systemctl enable loyalcorp-manager
    echo "✅ Manager systemd unit installed and enabled"
fi

# Restart via systemd if active, else fall back to killing background process
if systemctl is-active --quiet loyalcorp-manager; then
    systemctl restart loyalcorp-manager
else
    kill $(pgrep -f "uvicorn api:app") 2>/dev/null || true
    sleep 1
    systemctl start loyalcorp-manager 2>/dev/null || (
        cd /opt/loyalcorp-manager
        nohup venv/bin/uvicorn api:app --host 0.0.0.0 --port 9000 > /var/log/loyalcorp-manager.log 2>&1 &
        echo "✅ Manager started as background process (pid $!)"
    )
fi

echo "✅ Deploy complete"
