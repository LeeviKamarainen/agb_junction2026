#!/usr/bin/env bash
# One-time setup of VM2 for the frames API, run there as root by `make memory-setup`
# after the first deploy: Caddy serving HTTPS in front of the API, the user the API runs as, and
# its systemd unit (started by deploy.sh). Safe to run again. Docker, the gemma container and
# port 8000 (vLLM) are never touched.
set -euo pipefail

# <ip-with-dashes>.sslip.io resolves to the server's IP, which lets Caddy get a certificate.
site_host=$1
dir=/opt/glass-memory

# The owner types the secrets into .env; this script only checks that the token is there. No
# quotes or other characters: systemd would strip quotes, the page's link would keep them.
grep -Eqs '^GLASS_API_TOKEN=[A-Za-z0-9_-]{32,}$' "$dir/.env" \
    || { echo "Nothing done: $dir/.env needs GLASS_API_TOKEN=<32 or more of A-Z a-z 0-9 _ ->, unquoted."; exit 1; }

# Ubuntu packages Caddy from 24.04 on. NEEDRESTART_SUSPEND stops apt from restarting running
# services (Docker among them) after the install.
apt-get update -q
NEEDRESTART_SUSPEND=1 DEBIAN_FRONTEND=noninteractive apt-get install -yq caddy
# Chat goes to vLLM (port 8000, its own key), so the backend's LLM_URL can be this address;
# everything else to the API.
printf '%s {\n\t@vllm path /v1/chat/* /v1/models\n\treverse_proxy @vllm 127.0.0.1:8000\n\treverse_proxy 127.0.0.1:8001\n}\n' \
    "$site_host" > /etc/caddy/Caddyfile
systemctl reload-or-restart caddy

# The API's user: a home for the model caches, and the batches folder as the one place it can
# write under the deploy folder.
id -u glass >/dev/null 2>&1 \
    || useradd --system --create-home --home-dir /var/lib/glass-memory --shell /usr/sbin/nologin glass
install -d -o glass "$dir/res/items/API-1"

# Gemma's weights are in root's Hugging Face cache (vLLM downloaded them), which the API's user
# cannot read. Hard links put the same files in its own cache: no copy, no download.
hub=.cache/huggingface/hub
model=models--google--gemma-4-31B-it
if [ -d "/root/$hub/$model" ]; then
    home=/var/lib/glass-memory
    install -d -o glass "$home/.cache" "$home/.cache/huggingface" "$home/$hub" "$home/$hub/blobs"
    cp -al --update=none "/root/$hub/$model" "$home/$hub/"
    cp -al --update=none "/root/$hub/blobs/." "$home/$hub/blobs/"
fi

install -m 644 "$(dirname "$0")/glass-memory.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet glass-memory
echo "Set up; make memory-link prints the page's address."
