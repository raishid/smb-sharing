# CONFIGURACION INICIAL

verficiar con este comando el nombre de la red
  
  `ip -br addr`

`docker network create -d macvlan --subnet=192.168.xx0/24--gateway=192.168.x.xxx -o parent=eth0 macvlan_lan`

si responde esta manera agregar el nombre de parent en este acso eth0

`eth0             UP             192.168.xx.xxx/24`

Se debe Crea un registro A en dns para el nombre que dejamos en NETBIOS_NAME variable de entorno
para asi cuando se conecta por \\NETBIOS_NAME\impersora conecte correcatmente y resulva el nombre de dominio

---

# QUE VARIANTE USAR

| Host | Archivo | Por que |
|---|---|---|
| Synology / fisico con SMB propio | `docker-compose.yaml` (macvlan) | DSM ya ocupa 139/445: el print server necesita IP propia |
| VM dedicada (Hyper-V, VMware) | `docker-compose.host.yaml` | Los puertos estan libres y macvlan da problemas sobre hipervisores |

**Macvlan sobre un hipervisor da problemas.** El contenedor emite tramas con una
MAC distinta a la de la VM y el switch virtual las trata mal: el trafico chico
(ping, respuestas de pocos bytes) pasa perfecto, pero las transferencias grandes
se arrastran a cientos de bytes por segundo y mueren a la mitad, de forma
intermitente. En Hyper-V hay que habilitar "suplantacion de direcciones MAC",
y aun asi puede fallar. Medido en un caso real: 1,4 MB/s contra la IP de la VM
contra 517 bytes/s contra la IP macvlan del contenedor, con el mismo cliente y
el mismo archivo.

Si el host es una VM dedicada, lo simple es no usar macvlan:

```bash
ss -lntp | grep -E ':(139|445|631|8080)\b'   # tienen que estar libres
docker compose -f docker-compose.host.yaml up -d --build
```

El registro A del DNS pasa a apuntar a la IP del host, y el panel queda en
`http://<IP_DEL_HOST>:8080`. No hace falta ni el shim ni ajustar la MTU.

---

# ACCESO DEL HOST AL CONTENEDOR (shim macvlan)

> Solo aplica a la variante macvlan (`docker-compose.yaml`).

Con macvlan **el host no puede hablar con el contenedor**, aunque el resto de la
LAN si lo vea. No es un problema de configuracion: el kernel bloquea el trafico
entre una interfaz padre y sus propias macvlan hijas. En la practica el NAS no
puede hacerle ping al print server ni abrir el panel web.

Se resuelve dandole al host su propia macvlan ("shim"), porque dos macvlan
hermanas si se comunican entre si. El script es
[scripts/macvlan-shim.sh](scripts/macvlan-shim.sh) y sirve para cualquier host:
hay que ajustar las **tres variables del principio** (`PARENT`, `SHIM_IP`,
`CONTAINER_IP`). Lo unico que cambia entre un host y otro es como se hace
persistente, porque el shim **no sobrevive a un reboot por si solo**.

Reglas comunes a los dos casos:

- La interfaz padre se verifica con `ip -br addr` y tiene que ser la misma que se
  uso como `parent` al crear la red `macvlan_lan`.
- La IP del shim tiene que estar libre y fuera del rango DHCP del router. Para
  que Docker tampoco la asigne, agregar `--aux-address="shim=<IP_DEL_SHIM>"` al
  `docker network create`.
- Para probar, desde el host: `ping <IP_CONTAINER>` y
  `curl http://<IP_CONTAINER>:8080/health`.

## Host Synology (DSM)

Panel de control -> Programador de tareas -> Crear -> **Tarea activada**,
evento **Al iniciar**, usuario **root**, y en "Script definido por el usuario"
se pega el contenido del script.

**No usar una unit de systemd en DSM**: aunque DSM 7 lo tenga por debajo, las
units propias se pierden en las actualizaciones del sistema.

Si el NAS tiene Virtual Machine Manager instalado, DSM activa Open vSwitch y la
interfaz padre pasa a llamarse `ovs_eth0` / `ovs_bond0` (se confirma con
`ovs-vsctl show`; si el comando falla por falta del socket, no hay OVS).

Ademas, DSM ya ocupa los puertos 139/445 con su propio servicio SMB: por eso en
Synology **no sirve** reemplazar macvlan por `network_mode: host` ni por
publicacion de puertos. El print server necesita una IP propia si o si.

## Host Ubuntu (systemd)

```bash
sudo install -m 755 scripts/macvlan-shim.sh /usr/local/sbin/macvlan-shim.sh
sudo install -m 644 scripts/macvlan-shim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now macvlan-shim.service
sudo systemctl status macvlan-shim.service
```

La unit es `Type=oneshot` con `RemainAfterExit=yes` (crea la interfaz y termina,
pero systemd la sigue considerando activa) y corre **antes de docker.service**,
para que el host pueda alcanzar al contenedor apenas arranca.

Ojo con el nombre de la interfaz: en Ubuntu rara vez es `eth0`, suele ser
`enp3s0`, `ens18` y similares. Ajustar `PARENT` en el script.

Alternativas en Ubuntu, si se prefiere no usar un script:

- **systemd-networkd**: soporta macvlan de forma nativa con un `.netdev`
  (`Kind=macvlan`) mas un `MACVLAN=shim` en la `.network` de la interfaz padre.
  Es lo mas "correcto", pero si la red la maneja netplan hay que meterlo como
  drop-in sobre el archivo que netplan genera en `/run/systemd/network/`, lo que
  se vuelve fragil ante cambios de configuracion. **Netplan no soporta macvlan
  directamente.**
- **NetworkManager** (tipico en Ubuntu Desktop): se puede hacer todo persistente
  con un solo comando, sin script ni unit:

  ```bash
  sudo nmcli con add type macvlan ifname shim dev enp3s0 mode bridge \
      ip4 192.168.14.159/32 con-name shim
  sudo nmcli con mod shim +ipv4.routes "192.168.14.231/32"
  ```

---

# PANEL WEB (colas e historial)

El servicio `printweb` publica un panel interno en:

  `http://<NETBIOS_NAME>:8080`  (por ejemplo http://printserver:8080)

Usa el mismo namespace de red que `smbprinter` (`network_mode: service:smbprinter`),
asi que comparte la IP macvlan y el registro DNS que ya existe: no hace falta una
segunda IP ni otro registro A. El puerto se configura con `WEB_PORT` en el `.env`.

## Que muestra

- **Colas**: una tarjeta por impresora con el device URI, el estado
  (`idle` / `processing` / `stopped`), el mensaje de error si la impresora esta
  parada, y los trabajos pendientes. Se actualiza sola cada 5 s.
- **Historial**: tabla filtrable por impresora, usuario, estado, rango de fechas y
  texto libre (documento / equipo / usuario), con exportacion a CSV.

Acciones disponibles sobre las colas: cancelar un trabajo, pausar/reanudar una
impresora y vaciar su cola. **No hay autenticacion**, igual que la web de CUPS
(:631) y los shares SMB: el panel asume una red interna de confianza.

## De donde salen los datos

Un hilo de fondo consulta a CUPS por IPP cada 15 s (`Get-Jobs` con
`which-jobs=completed` y `not-completed`) y guarda cada trabajo en SQLite
(volumen `printweb_data`, archivo `/data/printjobs.db`). Asi el historial
sobrevive a los reinicios y a la purga que hace el propio cupsd (`MaxJobs 2000`).
Se conserva `WEB_RETENTION_DAYS` dias (365 por defecto).

## Advertencia sobre el conteo de paginas

Las colas se crean como **raw** (`lpadmin -m raw`): CUPS no interpreta el
documento, por lo que **no puede contar paginas de forma confiable**. La columna
`Pag.` solo tiene valor cuando la impresora reporta el dato; el indicador util
para medir volumen es el **tamano en KB** del trabajo. El `page_log` de CUPS esta
habilitado y se lee como complemento, pero no hay que tomarlo como conteo exacto.

## Persistencia y logs

`docker-compose.yaml` define tres volumenes:

- `cups_logs` → `/var/log/cups` (compartido en solo lectura con el panel)
- `cups_spool` → `/var/spool/cups` (historial de trabajos de CUPS)
- `printweb_data` → `/data` (base SQLite del panel)

`cupsd.conf` se monta como bind read-only, de modo que un cambio en el archivo del
repo se aplica reiniciando el contenedor (un volumen nombrado sobre `/etc/cups`
habria congelado la version de la imagen). `printers.conf` no se persiste a
proposito: se regenera en cada arranque a partir de la variable `PRINTERS`.

El contenedor `smbprinter` ahora tiene un `healthcheck` (`lpstat -r`) porque cupsd
corre en segundo plano y, si moria, el contenedor seguia arriba sin poder imprimir.
