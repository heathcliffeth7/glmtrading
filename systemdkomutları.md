# Systemd Komutları

```
sudo cp /root/trading/infra/trading-orchestrator.service /etc/systemd/system/
sudo cp /root/trading/infra/trading-dummy-feed.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-orchestrator
sudo systemctl enable --now trading-dummy-feed
```

Durum ve log takibi:

```
sudo systemctl status trading-orchestrator
sudo systemctl status trading-dummy-feed
journalctl -u trading-orchestrator -f
journalctl -u trading-dummy-feed -f
```
