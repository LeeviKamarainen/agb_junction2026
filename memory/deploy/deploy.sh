#!/usr/bin/env bash
# Deploys the frames API to VM2 (the Gemma server), the way the Makefile deploys the segmenter
# (gpu-*). Run through make from the repo root, on the laptop:
#
#   make memory-deploy    copy memory/ to /opt/glass-memory, uv sync, restart the API; the first
#                         time it installs uv and downloads about 3.3 GB of packages, and asks
#                         first
#   make memory-setup     one-time, after the first deploy: Caddy (HTTPS), the API's user and
#                         its systemd unit (deploy/setup.sh); asks first
#   make memory-logs      follow the API's log
#   make memory-ssh       a shell on VM2
#   make memory-link      the page's address with the token in it
#   make memory-token     the token API clients send as "Authorization: Bearer <token>"
#   make memory-send FRAMES=<folder> [EVERY=<seconds>]
#                         post a folder of frames from here (scripts/send_batch.py)
#
# VM2's address is VM2_IP in memory/.env. The SSH user is root unless that file sets VM2_USER;
# that user then needs passwordless sudo and an /opt/glass-memory it owns. The secrets
# (GEMMA_API_KEY, GLASS_API_TOKEN) live only in /opt/glass-memory/.env on VM2, typed there by the
# owner. The API listens on 127.0.0.1:8001; port 8000 (vLLM, the gemma container) is never
# touched.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=/dev/null
. "$root/.env"
: "${VM2_IP:?put VM2_IP=<address> in memory/.env}"
user=${VM2_USER:-root}
vm=$user@$VM2_IP
# sslip.io resolves this name to VM2_IP, so Caddy can get a certificate for it.
host=${VM2_IP//./-}.sslip.io
dir=/opt/glass-memory
sudo=$([ "$user" = root ] || echo sudo)

confirm() {
    read -rp 'Type yes to go ahead: ' answer
    [ "$answer" = yes ] || { echo 'Nothing done.'; exit 1; }
}

# The token, read from VM2's .env; setup.sh has checked that it is unquoted.
token() { ssh "$vm" "$sudo sed -n 's/^GLASS_API_TOKEN=//p' $dir/.env"; }

# Copies memory/ and installs the locked dependencies. What is excluded here is also left alone
# on VM2. uv's Python goes to .python beside the venv, not into the SSH user's home, so that the
# API's user can read it.
push() {
    # -n: ssh must not swallow the answer when it is piped in.
    if ! ssh -n "$vm" "[ -d $dir/.venv ]"; then
        echo "First deploy: installs uv for $user on VM2 if it is missing (astral.sh's installer),"
        echo "then downloads Python 3.14 and about 3.3 GB of packages into $dir."
        confirm
    fi
    rsync -az --delete --no-owner --no-group --exclude /.venv --exclude /.python --exclude .env \
        --exclude /.git --exclude /res --exclude /video --exclude /scratch --exclude __pycache__ \
        --exclude .pytest_cache --exclude .ruff_cache "$root/" "$vm:$dir/"
    ssh "$vm" "cd $dir && export PATH=\"\$HOME/.local/bin:\$PATH\" UV_PYTHON_INSTALL_DIR=$dir/.python \
        && { command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh; } \
        && uv sync --locked"
}

# Restarts the API, if setup has installed it, and waits until it answers.
restart() {
    ssh "$vm" "[ -f /etc/systemd/system/glass-memory.service ] || exit 0
        $sudo systemctl restart glass-memory || exit 1
        for _ in \$(seq 90); do curl -fsS http://127.0.0.1:8001/health && echo && exit 0; sleep 2; done
        echo '/health did not answer within 3 minutes; see make memory-logs'; exit 1"
}

setup() {
    cat <<EOF
One-time setup of VM2, run there as root (memory/deploy/setup.sh):
  - apt-get update, and install Caddy
  - replace /etc/caddy/Caddyfile: HTTPS on <ip-with-dashes>.sslip.io, proxied to 127.0.0.1:8001.
    Caddy then listens on ports 80 and 443, open to the internet.
  - add the system user "glass" (home /var/lib/glass-memory) and give it res/items/API-1
  - install, enable and start glass-memory.service
Docker, the gemma container and port 8000 are not touched.
EOF
    confirm
    rsync -az --no-owner --no-group "$root/deploy/" "$vm:$dir/deploy/"
    ssh "$vm" "$sudo bash $dir/deploy/setup.sh $host"
    restart
}

case ${1:-deploy} in
    deploy) push; restart ;;
    setup) setup ;;
    logs) ssh "$vm" "$sudo journalctl -u glass-memory -f -n 100" ;;
    ssh) ssh "$vm" ;;
    # The token alone, for an API client. This and `link` are the only places it is printed.
    token) token ;;
    # The page's address with the token in it, to open on a phone or share.
    link)
        token=$(token)
        echo "https://$host/#token=$token" ;;
    # The token goes from VM2 into send_batch.py's environment: not printed, not stored here.
    send)
        token=$(token)
        GLASS_API_URL=https://$host GLASS_API_TOKEN=$token \
            "$root/.venv/bin/python" "$root/scripts/send_batch.py" "${@:2}" ;;
    *) echo "usage: $0 [deploy|setup|logs|ssh|link|token|send FRAMES]"; exit 2 ;;
esac
