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

kill $(pgrep -f "uvicorn api:app") 2>/dev/null || true
sleep 1
cd /opt/loyalcorp-manager
nohup venv/bin/uvicorn api:app --host 0.0.0.0 --port 9000 > /var/log/loyalcorp-manager.log 2>&1 &

echo "✅ Deployed and manager restarted (pid $!)"
