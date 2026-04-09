#!/bin/bash
set -e

NETBIOS_NAME=${NETBIOS_NAME:-printserver}
PRINTERS=${PRINTERS:-}

if [ -z "$PRINTERS" ]; then
  echo "ERROR: Debes definir PRINTERS" >&2
  exit 1
fi

mkdir -p /var/spool/cups /var/spool/samba /var/log/cups /var/log/samba
mkdir -p /var/lib/samba/private /var/cache/samba /run/samba
chmod 0755 /run/samba

# Crear usuario guest para Samba
useradd -M -s /sbin/nologin nobody 2>/dev/null || true
(echo ""; echo "") | smbpasswd -a -s nobody 2>/dev/null || true
smbpasswd -e nobody 2>/dev/null || true

echo "==== Iniciando CUPS ===="
cupsd || { echo "ERROR: no pudo iniciar cupsd"; exit 1; }
sleep 2

configure_printer () {
  local NAME="$1" IP="$2" QUEUE="$3"
  echo "==== Configurando impresora LPD: $IP / $QUEUE como cola '$NAME' ===="
  lpstat -p "$NAME" >/dev/null 2>&1 && lpadmin -x "$NAME" || true
  lpadmin -p "$NAME" -E -v "lpd://$IP/$QUEUE" -m raw
  lpadmin -p "$NAME" -o printer-error-policy=retry-job
  if ! lpstat -p | awk '{print $2}' | grep -qx "$NAME"; then
    echo "ERROR: no se creó la cola CUPS '$NAME'." >&2
    exit 1
  fi
}

declare -a NAMES IPs QUEUES
IFS='|' read -ra ITEMS <<< "$PRINTERS"
for item in "${ITEMS[@]}"; do
  NAME="$(echo "$item" | cut -d: -f1)"
  IP="$(echo   "$item" | cut -d: -f2)"
  Q="$(echo    "$item" | cut -d: -f3)"
  if [ -z "$NAME" ] || [ -z "$IP" ] || [ -z "$Q" ]; then
    echo "Formato inválido en PRINTERS: '$item'" >&2
    exit 1
  fi
  NAMES+=("$NAME"); IPs+=("$IP"); QUEUES+=("$Q")

  # Agregar entrada en /etc/hosts para evitar reverse DNS lookup
  # del IP de la impresora (causa del delay de ~30s si no hay PTR en DNS)
  if ! grep -q "^$IP " /etc/hosts 2>/dev/null; then
    echo "$IP $NAME-printer" >> /etc/hosts
    echo "Agregado $IP $NAME-printer a /etc/hosts"
  fi

  configure_printer "$NAME" "$IP" "$Q"
done

cupsctl --share-printers || true

SMB_CONF="/etc/samba/smb.conf"
cat > "$SMB_CONF" <<EOF
[global]
   workgroup = WORKGROUP
   netbios name = ${NETBIOS_NAME}
   server string = Puente DP-301P+
   security = user
   map to guest = Bad User
   guest account = nobody
   restrict anonymous = 0

   disable netbios = yes
   dns proxy = no
   wins support = no
   hostname lookups = no
   name resolve order = host bcast

   ntlm auth = ntlmv1-permitted
   lanman auth = yes
   server min protocol = SMB2_02
   server max protocol = SMB3

   printing = cups
   printcap name = cups
   load printers = no
   cups options = raw

   log file = /var/log/samba/%m.log
   log level = 1
EOF

for NAME in "${NAMES[@]}"; do
  cat >> "$SMB_CONF" <<EOF

[${NAME}]
   comment = Impresora ${NAME}
   path = /var/spool/samba
   printable = yes
   browseable = yes
   guest ok = yes
   read only = yes
   create mask = 0700
   printer name = ${NAME}
EOF
done

if ! testparm -s "$SMB_CONF" >/dev/null 2>&1; then
  echo "ERROR: smb.conf inválido" >&2
  testparm -s "$SMB_CONF" || true
  exit 1
fi

echo "==== Lanzando smbd (foreground) ===="
exec /usr/sbin/smbd --foreground --no-process-group -s "$SMB_CONF"