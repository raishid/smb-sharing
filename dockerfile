FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Paquetes necesarios (Samba + CUPS). Puedes quitar printer-driver-all si quieres achicar la imagen.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      samba samba-common smbclient \
      cups cups-client cups-bsd \
      printer-driver-all \
    && rm -rf /var/lib/apt/lists/*

# Crear rutas base (el entrypoint las vuelve a asegurar igual)
RUN mkdir -p /var/spool/cups /var/spool/samba /var/log/cups /var/log/samba \
    /var/lib/samba/private /var/cache/samba /run/samba

# cupsd.conf (opcional, si tienes uno personalizado)
COPY cupsd.conf /etc/cups/cupsd.conf

# Entrypoint dinámico
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Puertos (con macvlan no hacen falta para exponer, pero no estorban)
EXPOSE 139 445 631

CMD ["/entrypoint.sh"]
