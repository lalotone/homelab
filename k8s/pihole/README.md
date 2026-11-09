# Pi-hole

Ad and tracker blocking DNS for every device in the house, running on endurance-1 with the node's own network, so it
answers on port 53 of endurance-1's LAN IP. Argo CD deploys it from `main` (`../argocd/applications/pihole.yaml`).

## One-time setup

1. **Free port 53 on endurance-1.** Ubuntu's systemd-resolved holds 127.0.0.53:53, which blocks Pi-hole. On endurance-1:

       sudo mkdir -p /etc/systemd/resolved.conf.d
       printf '[Resolve]\nDNSStubListener=no\n' | sudo tee /etc/systemd/resolved.conf.d/no-stub.conf
       sudo ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
       sudo systemctl restart systemd-resolved

2. **Admin password** (kept out of Git):

       kubectl create namespace pihole
       kubectl -n pihole create secret generic pihole --from-literal=password='<choose one>'

3. **Deploy:** `kubectl apply -k ../argocd/applications` (adds the Application), or `kubectl apply -k .` by hand.

4. **Check it works** before telling the router: `dig @<endurance-1 LAN IP> example.com` answers, and
   `dig @<endurance-1 LAN IP> doubleclick.net` returns 0.0.0.0.

5. **Router:** give endurance-1 a fixed DHCP lease, then set DHCP's DNS to endurance-1's LAN IP first and the fallback
   second. Devices pick it up when their lease renews (or reconnect them).

Web UI: `http://<endurance-1>:8081/admin`.

## Notes

- If endurance-1 is down, devices use the router's fallback DNS: internet keeps working, without blocking.
- Optional: Tailscale admin → DNS → add endurance-1's Tailscale IP as a nameserver for ad blocking away from home.
- CoreDNS still forwards to 1.1.1.1/8.8.8.8 on purpose, so the cluster doesn't depend on Pi-hole.
