# CONFIGURACION INICIAL

verficiar con este comando el nombre de la red
  
  `ip -br addr`

`docker network create -d macvlan --subnet=192.168.xx0/24--gateway=192.168.x.xxx -o parent=eth0 macvlan_lan`

si responde esta manera agregar el nombre de parent en este acso eth0

`eth0             UP             192.168.xx.xxx/24`

Se debe Crea un registro A en dns para el nombre que dejamos en NETBIOS_NAME variable de entorno
para asi cuando se conecta por \\NETBIOS_NAME\impersora conecte correcatmente y resulva el nombre de dominio

---

# ACCESO DEL HOST AL CONTENEDOR (shim macvlan)

Con macvlan **el host no puede hablar con el contenedor**, aunque el resto de la
LAN si lo vea. No es un problema de configuracion: el kernel bloquea el trafico
entre una interfaz padre y sus propias macvlan hijas. En la practica el NAS no
puede hacerle ping al print server ni abrir el panel web.

Se resuelve dandole al host su propia macvlan ("shim"), porque dos macvlan
hermanas si se comunican entre si. El script esta en
[scripts/synology-shim.sh](scripts/synology-shim.sh): hay que ajustar las tres
variables del principio y dejarlo en el **Programador de tareas** de DSM como
tarea activada **Al iniciar**, con usuario **root** (no usar una unit de systemd:
DSM las pierde en cada actualizacion).

Notas:

- La interfaz padre se verifica con `ip -br addr`. Si el NAS tiene Virtual
  Machine Manager instalado, DSM activa Open vSwitch y la interfaz pasa a
  llamarse `ovs_eth0` / `ovs_bond0` (se confirma con `ovs-vsctl show`).
- La IP del shim tiene que estar libre y fuera del rango DHCP del router. Para
  que Docker tampoco la asigne, agregar `--aux-address="shim=<IP_DEL_SHIM>"` al
  `docker network create`.
- Para probar, desde el host: `ping <IP_CONTAINER>` y
  `curl http://<IP_CONTAINER>:8080/health`.

Este es tambien el motivo por el que **no sirve** cambiar macvlan por
`network_mode: host` ni por publicacion de puertos: DSM ya ocupa los puertos
139/445 con su propio servicio SMB, asi que el print server necesita una IP
propia si o si.

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
