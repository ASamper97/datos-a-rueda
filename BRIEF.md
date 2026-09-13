# Brief: scraper PCS — jóvenes de la Vuelta 2026

## Objetivo
Extraer, para cada corredor de 23 años o menos de la lista de salida de la Vuelta a España 2026:
- Nombre, nacionalidad, fecha de nacimiento, equipo actual
- Historial de equipos (año a año), incluyendo etapa amateur/sub-23
- Año y edad en que pasó a profesional (primer equipo UCI: WorldTeam, ProTeam o Continental)
- Número de top-10 en carreras UCI antes de pasar a profesional

## Entorno

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install procyclingstats requests beautifulsoup4 lxml pandas matplotlib
```

Paquete principal: `procyclingstats` (wrapper no oficial de PCS).
Clases relevantes: `RaceStartlist`, `Rider`, `RiderResults`.
Si el paquete falla o le faltan campos, se cae a `requests` + `BeautifulSoup` sobre el HTML crudo.

## Arquitectura: tres capas separadas

Esto es lo importante. Tres scripts, no uno.

### 1. `fetch.py` — descarga y cachea
- Única capa que toca la red.
- Guarda cada URL en `cache/{hash_de_la_url}.html`.
- Antes de pedir nada, comprueba si ya está en cache. Si está, no hace request.
- `time.sleep(1.5)` entre peticiones reales.
- User-Agent identificativo real (nombre + email o URL), no el de por defecto.
- Reintentos con backoff exponencial ante error 429 o 5xx.
- Log de cada URL descargada.

### 2. `parse.py` — extrae datos del HTML cacheado
- No toca la red jamás. Solo lee de `cache/`.
- Salida: `data/riders.parquet` y `data/teams.parquet`.
- Cada función de parseo devuelve `None` en vez de reventar si no encuentra el campo.
- Registra en un log qué corredores tienen campos faltantes.

### 3. `analyze.py` — agrega y grafica
- Lee los parquet, no parsea HTML.
- Produce la tabla final y el gráfico.

## Esquema de datos

**riders**
```
rider_id (slug de PCS)  |  name  |  nationality  |  birth_date  |  age_2026
current_team  |  first_pro_year  |  first_pro_team  |  age_turned_pro
last_amateur_team  |  last_amateur_year  |  top10s_before_pro
```

**teams** (una fila por corredor y año)
```
rider_id  |  year  |  team_name  |  team_level
```
`team_level`: `WT` / `PRT` / `CT` / `amateur` / `development`

## Puntos donde esto se va a romper

1. **Clasificar amateur vs. profesional.** PCS no siempre lo marca limpio. Los equipos de desarrollo (los "Development Team" de los WorldTeams) son un caso aparte: no son amateur puro ni profesional. Trátalos como categoría propia desde el principio, no los metas en ninguno de los dos saco.
2. **Fechas de nacimiento ausentes** en algunos corredores. Filtrar por edad va a perder gente. Registra cuántos pierdes.
3. **Carreras amateur mal cubiertas** en PCS, sobre todo fuera de Europa occidental. El sesgo existe: asúmelo y dilo en el post.
4. **Nombres de equipo cambiantes** año a año (cambio de patrocinador). Normaliza antes de agregar o contarás el mismo equipo dos veces.

## Criterio de "hecho"
- `data/riders.parquet` existe con una fila por corredor sub-24 de la lista de salida.
- Un log que diga cuántos corredores tienen datos incompletos y cuáles.
- Reejecutar `parse.py` tarda segundos, no minutos.

## Reglas de uso
- Una petición cada segundo y medio como mínimo.
- Nunca repetir una descarga que ya esté en cache.
- Uso personal y análisis público, no redistribución del dataset de PCS.
- Citar PCS como fuente en cualquier cosa que publiques.
