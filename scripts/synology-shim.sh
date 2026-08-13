#!/bin/sh
# ---------------------------------------------------------------------------
# Acceso del host (Synology DSM) al contenedor en la red macvlan.
#
# El kernel bloquea el trafico entre una interfaz padre y sus propias macvlan
# hijas: por eso el NAS no puede hablar con el contenedor aunque el resto de la
# LAN si lo vea. La solucion es darle al host su propia macvlan ("shim"): dos
# macvlan hermanas si se comunican entre si.
#
#   eth0 (host) --+-- shim  .159   <- esta interfaz
#                 +-- contenedor   <- IP_CONTAINER del .env
#
# COMO INSTALARLO (no sobrevive al reboot por si solo):
#   Panel de control -> Programador de tareas -> Crear -> Tarea activada
#     Evento: Al iniciar    Usuario: root
#     Script definido por el usuario: pegar el contenido de este archivo
#
# Se usa el Programador de tareas y no una unit de systemd porque las units
# propias se pierden en las actualizaciones de DSM.
# ---------------------------------------------------------------------------

set -e

PARENT="eth0"                 # verificado con: ip -br addr
SHIM_IP="192.168.14.159"      # IP libre, fuera del rango DHCP del router
CONTAINER_IP="192.168.14.231" # debe coincidir con IP_CONTAINER del .env

# Idempotente: la tarea puede re-ejecutarse sin romper nada.
if ip link show shim >/dev/null 2>&1; then
    ip link del shim
fi

ip link add shim link "$PARENT" type macvlan mode bridge
ip addr add "$SHIM_IP/32" dev shim
ip link set shim up
ip route add "$CONTAINER_IP/32" dev shim

echo "shim listo: $SHIM_IP -> $CONTAINER_IP (padre: $PARENT)"
