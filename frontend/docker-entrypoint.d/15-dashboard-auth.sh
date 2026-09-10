#!/bin/sh
# Optional HTTP basic auth for the Vite floor. Empty CRM_DASHBOARD_PASSWORD
# leaves the include empty so LAN access stays open (same as Streamlit).
set -e

AUTH_INC=/etc/nginx/auth.inc
HTPASSWD=/etc/nginx/.htpasswd
: > "$AUTH_INC"

if [ -n "${CRM_DASHBOARD_PASSWORD}" ]; then
  HASH=$(openssl passwd -apr1 "${CRM_DASHBOARD_PASSWORD}")
  printf 'agency:%s\n' "$HASH" > "$HTPASSWD"
  cat > "$AUTH_INC" <<'EOF'
    auth_basic "The Agency";
    auth_basic_user_file /etc/nginx/.htpasswd;
EOF
fi
