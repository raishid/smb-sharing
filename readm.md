# CONFIGURACION INICIAL

verficiar con este comando el nombre de la red
  
  `ip -br addr`

`docker network create -d macvlan --subnet=192.168.xx0/24--gateway=192.168.x.xxx -o parent=eth0 macvlan_lan`

si responde esta manera agregar el nombre de parent en este acso eth0

`eth0             UP             192.168.xx.xxx/24`

Se debe Crea un registro A en dns para el nombre que dejamos en NETBIOS_NAME variable de entorno
para asi cuando se conecta por \\NETBIOS_NAME\impersora conecte correcatmente y resulva el nombre de dominio
