FROM ubuntu:22.04

# Instalar dependencias
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    samba samba-common smbclient cups cups-client cups-bsd printer-driver-all \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copiar configuración de Samba
COPY smb.conf /etc/samba/smb.conf

# Crear directorio para spooler
RUN mkdir -p /var/spool/samba
RUN chown -R root:root /var/spool/samba

# Copiar entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY cupsd.conf /etc/cups/cupsd.conf

# Exponer puertos SMB y web de CUPS
EXPOSE 139 445 631

# CMD
CMD ["/entrypoint.sh"]
