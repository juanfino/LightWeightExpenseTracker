# Blueprint: Gastos Familiares — HA Custom Add-on

## Contexto
App de registro de gastos personales/familiares. Corre como Custom Add-on de Home Assistant OS en una Raspberry Pi 4 (aarch64). Los usuarios envían mensajes de texto por Telegram (`Supermercado 150000`) y la app registra el gasto, lo categoriza automáticamente, e identifica quién lo envió. El dashboard web muestra gráficos de gastos mensuales por categoría, semanales, y por usuario.

---

## Estructura de archivos FINAL

```
/                                  ← raíz del repositorio GitHub
├── repository.json
└── gastos/
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    ├── requirements.txt
    └── app/
        ├── main.py                ← entrypoint: arranca bot + dashboard en paralelo
        ├── bot.py                 ← lógica del bot Telegram
        ├── parser.py              ← parsing de mensajes de texto
        ├── categorizer.py         ← asignación de categorías por keywords
        ├── db.py                  ← todas las operaciones SQLite
        ├── dashboard.py           ← Flask app (rutas + API JSON)
        ├── seed.py                ← datos iniciales (categorías + keywords default)
        └── templates/
            ├── base.html
            ├── index.html         ← dashboard principal
            ├── history.html       ← historial con filtros
            └── settings.html      ← gestión de categorías y keywords
```

---

## 1. repository.json

```json
{
  "name": "Gastos Familiares",
  "url": "https://github.com/TU_USUARIO/TU_REPO",
  "maintainer": "Tu Nombre"
}
```

---

## 2. gastos/config.yaml

```yaml
name: "Gastos Familiares"
description: "Registro de gastos familiares via Telegram con dashboard web"
version: "1.0.0"
slug: "gastos"
init: false
arch:
  - aarch64
  - armv7
  - amd64
ports:
  5000/tcp: 5000
ports_description:
  5000/tcp: "Dashboard web"
options:
  telegram_token: ""
  users: []
schema:
  telegram_token: str
  users:
    - telegram_id: str
      name: str
map:
  - data:rw
```

**Notas:**
- `map: data:rw` expone `/data` dentro del container — ahí vive el SQLite.
- `ports:
  5000/tcp: 5000
ports_description:
  5000/tcp: "Dashboard web"` permite acceder al dashboard desde dentro de HA sin abrir puertos.
- `users` es la lista de usuarios autorizados. telegram_id es el chat_id numérico (obtenerlo con @userinfobot en Telegram).

---

## 3. gastos/Dockerfile

```dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

COPY app/ .
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
```

---

## 4. gastos/requirements.txt

```
python-telegram-bot==20.7
Flask==3.0.0
APScheduler==3.10.4
```

---

## 5. gastos/run.sh

```bash
#!/usr/bin/with-contenv bashio

export TELEGRAM_TOKEN=$(bashio::config 'telegram_token')
export USERS_JSON=$(bashio::config 'users')
export DB_PATH="/data/gastos.db"

python3 /app/main.py
```

---

## 6. Database Schema — db.py

### Tablas

```sql
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    UNIQUE NOT NULL,
    color      TEXT    NOT NULL DEFAULT '#6366f1',
    icon       TEXT    NOT NULL DEFAULT '💰',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT    UNIQUE NOT NULL,   -- lowercase, sin acentos
    category_id INTEGER NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    category_id INTEGER,                  -- NULL si no se pudo categorizar
    concept     TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    raw_text    TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)     REFERENCES users(id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);
```

### Funciones requeridas en db.py

```python
def init_db()                          # Crea tablas si no existen, llama seed si DB nueva
def get_user_by_telegram_id(tg_id)     # Retorna user o None
def create_expense(user_id, category_id, concept, amount, raw_text)
def get_expenses_by_month(year, month) # Para dashboard
def get_expenses_by_week(year, week)   # isoweek
def get_expenses_summary_by_category(year, month)  # [{category, total, color, icon}]
def get_all_keywords()                 # [{keyword, category_id, category_name}]
def add_keyword(keyword, category_id)
def delete_keyword(keyword_id)
def get_all_categories()
def get_recent_expenses(limit=50)      # Para historial
def delete_expense(expense_id)         # Para correcciones
```

---

## 7. parser.py — Parsing de mensajes

### Formatos soportados

| Mensaje del usuario         | Resultado esperado              |
|----------------------------|---------------------------------|
| `Supermercado 150000`      | concept=Supermercado, amount=150000 |
| `150000 nafta`             | concept=Nafta, amount=150000    |
| `Cena cumpleaños 5000`     | concept=Cena cumpleaños, amount=5000 |
| `YPF 100.000`              | concept=YPF, amount=100000      |
| `YPF 100,000`              | concept=YPF, amount=100000      |
| `farmacia 2500.50`         | concept=Farmacia, amount=2500.50|

### Lógica

```python
import re

def parse_message(text: str) -> dict | None:
    """
    Retorna {"concept": str, "amount": float} o None si no se puede parsear.
    
    Algoritmo:
    1. Normalizar separadores numéricos: quitar puntos de miles, reemplazar coma decimal por punto
    2. Buscar patrón: palabras + número (al final) → concepto son las palabras, monto el número
    3. Buscar patrón: número + palabras (al inicio) → monto primero, concepto el resto
    4. Si no matchea ninguno → retornar None
    """
    
    # Normalización: "100.000" → "100000", "2.500,50" → "2500.50"
    # Regex para encontrar el monto: dígitos con posibles separadores
    # Concepto: todo lo que no sea el monto
    ...
```

---

## 8. categorizer.py — Categorización por keywords

```python
import unicodedata

def normalize(text: str) -> str:
    """Lowercase + quitar acentos. 'Ñoño' → 'nono'"""
    ...

def categorize(concept: str, keywords: list) -> int | None:
    """
    Recibe el concepto y la lista de keywords de la DB.
    Retorna category_id o None.
    
    Algoritmo:
    - Normalizar el concepto
    - Para cada keyword (también normalizada): verificar si está contenida en el concepto
    - Retornar la categoría del primer match
    - Si no hay match → None (Sin categoría)
    """
    ...
```

---

## 9. bot.py — Lógica del bot Telegram

### Comportamiento por tipo de mensaje

**IMPORTANTE — El filtrado de usuarios es lo PRIMERO que se ejecuta en todo handler:**
```python
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.message.chat_id)
    user = db.get_user_by_telegram_id(telegram_id)
    if user is None:
        await update.message.reply_text("⛔ No estás autorizado para usar este bot.")
        return
    # Continúa el flujo normal...
```
Esto aplica a mensajes de texto Y a todos los comandos. Cualquier chat_id no registrado en `users` recibe el mensaje de rechazo y se corta el flujo. No se loguea ni se procesa nada.

**Mensaje de texto plano (gasto normal):**
1. Verificar usuario autorizado (ver arriba) — si no → cortar
2. Parsear con `parser.parse_message(text)`
3. Si parse falla → responder con formato de ayuda
4. Categorizar concepto
5. Guardar en DB
6. Responder confirmación:
   ```
   ✅ Gasto registrado
   📋 Supermercado
   💰 $150.000
   🏷️ Alimentación
   👤 Juampi
   ```

**Comandos:**

| Comando | Descripción |
|---------|-------------|
| `/gastos` | Resumen del mes: total + breakdown por categoría |
| `/semana` | Gastos de la semana actual |
| `/hoy` | Gastos del día |
| `/borrar [ID]` | Borra un gasto por ID (el ID se muestra en la confirmación) |
| `/add_keyword PALABRA CATEGORÍA` | Agrega keyword. Ej: `/add_keyword churrasco Alimentación` |
| `/categorias` | Lista de categorías disponibles |
| `/ayuda` | Muestra todos los comandos y el formato de carga |

**Manejo de errores:**
- Parse fallido → responder con ejemplo de formato correcto
- Usuario no autorizado → mensaje claro (no ignorar silenciosamente)
- Error de DB → responder "Hubo un error, intenta de nuevo" + log del error

---

## 10. dashboard.py — Flask App

### Rutas

```
GET  /                    → index.html  (dashboard principal)
GET  /history             → history.html (historial con filtros)
GET  /settings            → settings.html (gestión keywords/categorías)
GET  /api/summary         → JSON resumen mes actual
GET  /api/monthly?year=&month= → JSON gastos por categoría de un mes
GET  /api/weekly?year=&week=   → JSON gastos por semana
GET  /api/expenses        → JSON listado (acepta ?month=&year=&category_id=&user_id=)
POST /api/expenses/delete  → {id: int} elimina gasto
POST /api/keywords/add     → {keyword: str, category_id: int}
POST /api/keywords/delete  → {id: int}
```

### Formato JSON /api/summary

```json
{
  "month": "Mayo 2026",
  "total": 450000,
  "by_category": [
    {"name": "Alimentación", "total": 200000, "color": "#22c55e", "icon": "🛒", "pct": 44},
    {"name": "Combustible",  "total": 100000, "color": "#f59e0b", "icon": "⛽", "pct": 22}
  ],
  "by_week": [
    {"week": 1, "label": "Sem 1", "total": 120000},
    {"week": 2, "label": "Sem 2", "total": 180000}
  ],
  "by_user": [
    {"name": "Juampi", "total": 250000},
    {"name": "Pareja", "total": 200000}
  ]
}
```

---

## 11. seed.py — Categorías y keywords por defecto

```python
DEFAULT_CATEGORIES = [
    {"name": "Alimentación",    "color": "#22c55e", "icon": "🛒"},
    {"name": "Vehículos",       "color": "#f59e0b", "icon": "🚗"},
    {"name": "Salud",           "color": "#ef4444", "icon": "💊"},
    {"name": "Servicios",       "color": "#3b82f6", "icon": "🔌"},
    {"name": "Entretenimiento", "color": "#a855f7", "icon": "🎬"},
    {"name": "Transporte",      "color": "#06b6d4", "icon": "🚌"},
    {"name": "Educación",       "color": "#f97316", "icon": "📚"},
    {"name": "Ropa",            "color": "#ec4899", "icon": "👕"},
    {"name": "Sin categoría",   "color": "#6b7280", "icon": "❓"},
]

DEFAULT_KEYWORDS = {
    "Alimentación":    ["supermercado", "super", "almacen", "verduleria", "carniceria",
                        "panaderia", "feria", "mercado", "kiosco", "fiambreria", "despensa"],
    "Vehículos":       ["nafta", "combustible", "ypf", "shell", "axion", "puma",
                        "taller", "mecanico", "gomeria", "repuesto", "aceite", "patente"],
    "Salud":           ["farmacia", "medico", "doctor", "clinica", "hospital",
                        "remedios", "medicamento", "turno", "dentista", "oculista"],
    "Servicios":       ["luz", "gas", "agua", "internet", "telefono", "celular",
                        "claro", "personal", "movistar", "directv", "netflix", "spotify"],
    "Entretenimiento": ["cine", "teatro", "bar", "restaurant", "restaurante",
                        "pizza", "sushi", "delivery", "pedidosya", "rappi"],
    "Transporte":      ["uber", "taxi", "remis", "subte", "colectivo", "tren", "peaje"],
    "Educación":       ["colegio", "universidad", "curso", "libro", "cuota", "matricula"],
    "Ropa":            ["ropa", "zapatillas", "zapatos", "indumentaria", "calzado"],
}
```

---

## 12. main.py — Entrypoint

```python
"""
Arranca el bot de Telegram y el dashboard Flask en paralelo usando threading.
- Bot: corre en el hilo principal con run_polling()
- Dashboard: corre en un hilo daemon con Flask development server (puerto 5000)

Secuencia de inicio:
1. Cargar variables de entorno (TELEGRAM_TOKEN, USERS_JSON, DB_PATH)
2. Parsear USERS_JSON y convertirlo a dict {telegram_id: name}
3. init_db() → crea tablas + seed si es primera vez
4. Arrancar Flask en thread daemon
5. Arrancar bot Telegram (bloqueante)
"""
```

---

## 13. Frontend — templates/

### Estética

**Estilo:** Dark theme. Minimalista pero con datos bien resaltados. Sin frameworks pesados.
- **Fuente:** JetBrains Mono (números) + Inter (texto)
- **Colores base:** `#0f0f0f` fondo, `#1a1a1a` cards, `#ffffff` texto principal
- **Accent:** colores de cada categoría
- **Librerías:** Chart.js (CDN) para gráficos. Sin Bootstrap, sin Tailwind CDN.

### index.html — Dashboard principal

Secciones:
1. **Header:** mes actual, total gastado, navegación mes anterior/siguiente
2. **Cards resumen:** Total mes | Cantidad de gastos | Promedio diario | Categoría top
3. **Gráfico torta:** gastos por categoría (Chart.js Doughnut)
4. **Gráfico barras:** gastos por semana del mes (Chart.js Bar)
5. **Tabla últimos 10 gastos:** fecha, concepto, categoría, monto, usuario

### history.html — Historial

- Filtros: mes/año, categoría, usuario
- Tabla paginada con todos los gastos
- Botón eliminar por fila (con confirm dialog)

### settings.html — Configuración

- Lista de categorías (nombre, color, icono)
- Lista de keywords con su categoría asignada
- Formulario para agregar keyword
- Botón eliminar keyword

---

## 14. Configuración del Add-on en HA

Una vez pusheado el repo a GitHub:

1. HA → Settings → Add-ons → Add-on Store → ⋮ → Repositories
2. Agregar URL del repo
3. El add-on aparece como "Gastos Familiares"
4. Instalar → Ir a Configuración del add-on:
   ```yaml
   telegram_token: "123456:ABC-DEF..."
   users:
     - telegram_id: "111222333"
       name: "Juampi"
     - telegram_id: "444555666"
       name: "Pareja"
   ```
5. Iniciar — el dashboard queda disponible en `http://[IP-RASPI]:5000` desde la red local

---

## 15. Exposición externa — Cloudflare Tunnel + Google SSO

### Paso 1 — Agregar el subdominio al tunnel (en HA)

El add-on Cloudflared tiene una sección "Additional Hosts" en su configuración:

```
Additional Hosts → Add:
  Hostname: expenses.juampifinochietto.com
  Service:  http://localhost:5000
```

Guardar y reiniciar el add-on Cloudflared.

### Paso 2 — DNS en Cloudflare Dashboard

En el dashboard de Cloudflare (cloudflare.com):
- DNS → Records → el tunnel debería haber creado el CNAME automáticamente
- Verificar que `expenses.juampifinochietto.com` apunta al tunnel (mismo destino que `ha.juampifinochietto.com`)

### Paso 3 — Google OAuth App (Google Cloud Console)

1. console.cloud.google.com → crear proyecto (o usar uno existente)
2. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
3. Application type: **Web application**
4. Authorized redirect URIs: `https://[tu-team].cloudflareaccess.com/cdn-cgi/access/callback`
   (el team name se ve en Cloudflare Zero Trust → Settings → General)
5. Guardar → copiar **Client ID** y **Client Secret**

### Paso 4 — Cloudflare Access

En Cloudflare Zero Trust Dashboard (one.dash.cloudflare.com):

**Configurar Identity Provider:**
- Settings → Authentication → Add new → Google
- Pegar Client ID y Client Secret
- Test connection

**Crear la aplicación:**
- Access → Applications → Add an application → Self-hosted
- Application name: `Gastos Familiares`
- Session duration: 1 month (no querés loguearte seguido)
- Application domain: `expenses.juampifinochietto.com`

**Crear política:**
- Policy name: `Solo familia`
- Action: Allow
- Include → Emails → agregar los dos emails:
  - `juampi@gmail.com`
  - `pareja@gmail.com`

Cualquier otra cuenta de Google queda bloqueada antes de llegar a la app.

### Resultado

- `expenses.juampifinochietto.com` → pantalla de login Google (Cloudflare)
- Solo los dos emails autorizados pasan
- Flask recibe el header `Cf-Access-Authenticated-User-Email` con el email del usuario logueado
- El dashboard puede mostrar el nombre del usuario sin ningún sistema de sesiones propio

---

## 15. Consideraciones de implementación para CC

- El `telegram_id` es el **chat_id numérico** del usuario, no el username. Es un int pero almacenarlo como TEXT en SQLite evita comparaciones de tipo.
- `python-telegram-bot` v20+ es asíncrono (asyncio). El bot debe usar `ApplicationBuilder` y handlers async.
- Flask y el bot corren en el mismo proceso: Flask en un `threading.Thread(daemon=True)`, el bot bloquea el hilo principal con `app.run_polling()`.
- El path de la DB es `/data/gastos.db` — HA monta `/data` persistente entre reinicios.
- Los separadores de miles en Argentina son puntos (`100.000`). El parser debe manejar esto.
- `bashio` es la utilidad de HA para leer la configuración del add-on desde `run.sh`. Está disponible en la imagen base de HA.
- La imagen base del Dockerfile se define con `ARG BUILD_FROM` — HA la inyecta en build time.
- Para desarrollo local sin HA, crear un `.env` con las variables y arrancar `main.py` directamente.

---

## 16. Orden de implementación recomendado para CC

1. `repository.json` + `config.yaml` + `Dockerfile` + `run.sh` + `requirements.txt`
2. `db.py` (schema + todas las funciones)
3. `seed.py`
4. `parser.py` (con tests unitarios inline en `if __name__ == "__main__"`)
5. `categorizer.py`
6. `bot.py`
7. `dashboard.py` (rutas + API)
8. `templates/base.html` + `templates/index.html`
9. `templates/history.html` + `templates/settings.html`
10. `main.py` (integra todo)