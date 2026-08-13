#!/bin/sh
# ---------------------------------------------------------------------------
# Acceso del host al contenedor que vive en la red macvlan.
#
# El kernel bloquea el trafico entre una interfaz padre y sus propias macvlan
# hijas: por eso el host no puede hablar con el contenedor aunque el resto de la
# LAN si lo vea. La solucion es darle al host su propia macvlan ("shim"): dos
# macvlan hermanas si se comunican entre si.
#
#   eth0 (host) --+-- shim         <- esta interfaz
#                 +-- contenedor   <- IP_CONTAINER del .env
#
# Uso:  macvlan-shim.sh [up|down]   (sin argumento = up)
#
# Este script solo se ocupa de crear la interfaz. Como hacerlo persistente
# depende del host (Synology / Ubuntu): ver readme.md.
# ---------------------------------------------------------------------------

set -e

# --- Ajustar estos tres valores --------------------------------------------
PARENT="eth0"                 # interfaz real del host: verificar con `ip -br addr`
SHIM_IP="192.168.14.159"      # IP libre, fuera del rango DHCP del router
CONTAINER_IP="192.168.14.231" # debe coincidir con IP_CONTAINER del .env
# ---------------------------------------------------------------------------

shim_down() {
    # Idempotente: al borrar la interfaz se van con ella su IP y su ruta.
    if ip link show shim >/dev/null 2>&1; then
        ip link del shim
    fi
}

shim_up() {
    shim_down
    ip link add shim link "$PARENT" type macvlan mode bridge
    ip addr add "$SHIM_IP/32" dev shim
    ip link set shim up
    # /32 gana sobre la ruta /24 de la LAN por ser mas especifica.
    ip route add "$CONTAINER_IP/32" dev shim
    echo "shim listo: $SHIM_IP -> $CONTAINER_IP (padre: $PARENT)"
}

case "${1:-up}" in
    up)   shim_up ;;
    down) shim_down; echo "shim eliminado" ;;
    *)    echo "uso: $0 [up|down]" >&2; exit 1 ;;
esac
