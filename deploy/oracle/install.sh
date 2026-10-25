#!/bin/sh
# Run as ubuntu from the directory containing these deployment files.
set -eu

test "$(uname -m)" = aarch64
test "$(id -un)" = ubuntu
test -f /home/ubuntu/IEM_NBG_OpenBanking/tools/check_deployment.py
sudo -n install -d -m 0755 /usr/local/lib/income-check
sudo -n install -d -o ubuntu -g ubuntu -m 0700 /srv/income-check/deployment
sudo -n install -d -o ubuntu -g ubuntu -m 0700 /srv/income-check/deployment/logs /srv/income-check/deployment/backups
sudo -n install -m 0644 autodeploy.py audit.py verify.py /usr/local/lib/income-check/
sudo -n install -m 0644 /home/ubuntu/IEM_NBG_OpenBanking/tools/check_deployment.py /usr/local/lib/income-check/check_deployment.py
sudo -n install -m 0644 income-check-deploy.service income-check-deploy.timer /etc/systemd/system/
sudo -n systemd-analyze verify /etc/systemd/system/income-check-deploy.service /etc/systemd/system/income-check-deploy.timer
sudo -n systemctl daemon-reload
printf '%s\n' 'Installed. Verify the full pipeline before enabling income-check-deploy.timer.'
