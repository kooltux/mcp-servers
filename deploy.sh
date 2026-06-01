#!/bin/bash

cd $(dirname $0)

cp http/*.py /opt/mcp/http/
cp services/*.service /etc/systemd/system
systemctl daemon-reload

systemctl restart mcp-filesystem.service
systemctl restart mcp-git.service
