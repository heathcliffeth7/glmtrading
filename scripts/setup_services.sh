#!/bin/bash
# Setup trading services with feature sync

set -e

echo "=== Setting up Trading Bot Services ==="

# Copy service files
echo "Copying service files..."
sudo cp /root/trading/infra/trading-orchestrator.service /etc/systemd/system/
sudo cp /root/trading/infra/trading-feature-sync.service /etc/systemd/system/

# Reload systemd
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable services (but don't start yet)
echo "Enabling services..."
sudo systemctl enable trading-orchestrator.service
sudo systemctl enable trading-feature-sync.service

echo ""
echo "=== Services configured successfully ==="
echo ""
echo "To import historical data first:"
echo "  cd /root/trading"
echo "  .venv/bin/python scripts/import_historical_data.py --outputsize 288"
echo ""
echo "Then start the services:"
echo "  sudo systemctl start trading-feature-sync"
echo "  sudo systemctl start trading-orchestrator"
echo ""
echo "Check status:"
echo "  sudo systemctl status trading-feature-sync"
echo "  sudo systemctl status trading-orchestrator"
echo ""
echo "View logs:"
echo "  journalctl -u trading-feature-sync -f"
echo "  journalctl -u trading-orchestrator -f"
