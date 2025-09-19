#!/bin/bash
set -e

# Variables de entorno: DP_IP y DP_QUEUE
DP_IP=${DP_IP:-192.168.15.120}
DP_QUEUE=${DP_QUEUE:-comandas1}

echo "Iniciando CUPS..."
cupsd &

# Esperar a que CUPS esté listo
sleep 3

echo "Configurando impresora remota LPD: $DP_IP / $DP_QUEUE"
lpadmin -p dp301p -E -v "lpd://$DP_IP/$DP_QUEUE" -m raw

# Compartir impresora
cupsctl --share-printers

echo "Iniciando Samba..."
exec /usr/sbin/smbd -i --no-process-group
