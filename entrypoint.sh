#!/bin/bash
set -e

# ========= Variables globales opcionales =========
NETBIOS_NAME=${NETBIOS_NAME:-printserver}

# ========= Lista de impresoras (OBLIGATORIA) =========
# Formato: nombre:ip:cola|nombre2:ip2:cola2|...
PRINTERS=${PRINTERS:-}

if [ -z "$PRINTERS" ]; then
  echo "ERROR: Debes definir PRINTERS (formato: nombre:ip:cola|nombre2:ip2:cola2|...)" >&2
  exit 1
fi

# ========= Rutas necesarias =========
mkdir -p /var/spool/cups /var/spool/samba /var/log/cups /var/log/samba
mkdir -p /var/lib/samba/private /var/cache/samba /run/samba
chmod 0755 /run/samba

echo "==== Iniciando CUPS ===="
cupsd || { echo "ERROR: no pudo iniciar cupsd"; exit 1; }
sleep 2

configure_printer () {
  local NAME="$1" IP="$2" QUEUE="$3"
  echo "==== Configurando impresora LPD: $IP / $QUEUE como cola '$NAME' ===="
  # idempotente: si existe la cola, elimínala
  lpstat -p "$NAME" >/dev/null 2>&1 && lpadmin -x "$NAME" || true
  # RAW (deprecado a futuro, hoy funciona con LPD)
  lpadmin -p "$NAME" -E -v "lpd://$IP/$QUEUE" -m raw
  if ! lpstat -p | awk '{print $2}' | grep -qx "$NAME"; then
    echo "ERROR: no se creó la cola CUPS '$NAME'." >&2
    exit 1
  fi
}

# --- Parsear PRINTERS y configurar CUPS ---
declare -a NAMES IPs QUEUES
IFS='|' read -ra ITEMS <<< "$PRINTERS"
for item in "${ITEMS[@]}"; do
  NAME="$(echo "$item" | cut -d: -f1)"
  IP="$(echo   "$item" | cut -d: -f2)"
  Q="$(echo    "$item" | cut -d: -f3)"
  if [ -z "$NAME" ] || [ -z "$IP" ] || [ -z "$Q" ]; then
    echo "Formato inválido en PRINTERS: '$item' (usa nombre:ip:cola)" >&2
    exit 1
  fi
  NAMES+=("$NAME"); IPs+=("$IP"); QUEUES+=("$Q")
  configure_printer "$NAME" "$IP" "$Q"
done

cupsctl --share-printers || true

# ========= Generar smb.conf COMPLETO (dinámico) =========
SMB_CONF="/etc/samba/smb.conf"
cat > "$SMB_CONF" <<EOF
[global]
   workgroup = "WORKGROUP"
   netbios name = ${NETBIOS_NAME}
   server string = Puente DP-301P+
   security = user
   map to guest = Bad User

   # Integración con CUPS
   printing = cups
   printcap name = cups
   load printers = no
   cups options = raw

   # Protocolos modernos
   min protocol = SMB2
   max protocol = SMB3

   # Logs
   log file = /var/log/samba/%m.log
   log level = 1
EOF

# Un share por impresora (mismo estilo que tu smb.conf actual)
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

# Validar smb.conf recién generado
if ! testparm -s "$SMB_CONF" >/dev/null 2>&1; then
  echo "ERROR: smb.conf generado inválido. Detalle:" >&2
  testparm -s "$SMB_CONF" || true
  exit 1
fi

echo "==== Lanzando smbd (foreground) ===="
exec /usr/sbin/smbd -i --debug-stdout -s "$SMB_CONF"
