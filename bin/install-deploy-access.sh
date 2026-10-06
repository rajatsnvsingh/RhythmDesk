#!/bin/sh
set -eu
if [ "$(id -u)" != 0 ] || [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo 'Usage: sudo sh bin/install-deploy-access.sh /path/to/deployment-key.pub [absolute-project-directory]' >&2
    exit 1
fi
project=${2:-/opt/rhythm-desk}
project=$(realpath "$project")
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test -f "$project/.env"
test -f "$project/compose.yaml"
test -f "$1"
ssh-keygen -l -f "$1" >/dev/null
if ! id rhythm-deploy >/dev/null 2>&1; then
    useradd --system --user-group --create-home --home-dir /var/lib/rhythm-deploy-user --shell /bin/sh rhythm-deploy
fi
# Refuse to reuse a privileged account.
if [ "$(id -u rhythm-deploy)" = 0 ] || [ "$(id -nG rhythm-deploy)" != rhythm-deploy ] || [ "$(getent passwd rhythm-deploy | cut -d: -f6)" != /var/lib/rhythm-deploy-user ]; then
    echo 'rhythm-deploy already has excessive group permissions; stop and review.' >&2
    exit 1
fi
install -d -m 700 /etc/rhythm-deploy /var/lib/rhythm-deploy
printf '%s\n' "$project" > /etc/rhythm-deploy/project-path
chmod 600 /etc/rhythm-deploy/project-path
install -m 600 "$project/.env" "$project/compose.yaml" "$project/Dockerfile" "$project/requirements.txt" /etc/rhythm-deploy/
install -d -m 700 /etc/rhythm-deploy/config
install -m 600 "$project/config/config.yaml" /etc/rhythm-deploy/config/config.yaml
install -m 755 "$script_dir/nexus-deploy.py" /usr/local/sbin/rhythm-deploy
launcher=$(mktemp)
rule=$(mktemp)
trap 'rm -f "$launcher" "$rule"' EXIT
printf '%s\n' '#!/bin/sh' 'case "$SSH_ORIGINAL_COMMAND" in' 'deploy|status) exec /usr/bin/sudo -n /usr/local/sbin/rhythm-deploy "$SSH_ORIGINAL_COMMAND" ;;' '*) echo "Only deploy and status are permitted" >&2; exit 1 ;;' 'esac' > "$launcher"
install -m 755 "$launcher" /usr/local/sbin/rhythm-deploy-ssh
printf '%s\n' 'rhythm-deploy ALL=(root) NOPASSWD: /usr/local/sbin/rhythm-deploy deploy, /usr/local/sbin/rhythm-deploy status' > "$rule"
visudo -cf "$rule"
install -m 440 "$rule" /etc/sudoers.d/rhythm-deploy
install -d -m 755 -o root -g root /var/lib/rhythm-deploy-user
install -d -m 755 -o root -g root /var/lib/rhythm-deploy-user/.ssh
# Root-owned key list prevents the deploy account from adding unrestricted keys.
{ printf 'restrict,command="/usr/local/sbin/rhythm-deploy-ssh" '; cat "$1"; } > /var/lib/rhythm-deploy-user/.ssh/authorized_keys
chmod 644 /var/lib/rhythm-deploy-user/.ssh/authorized_keys
chown root:root /var/lib/rhythm-deploy-user/.ssh/authorized_keys
echo 'Scoped access installed. Test: ssh -i <private-key> rhythm-deploy@<server> status'
echo 'Revoke: sudo mv /var/lib/rhythm-deploy-user/.ssh/authorized_keys /var/lib/rhythm-deploy-user/.ssh/authorized_keys.disabled'
