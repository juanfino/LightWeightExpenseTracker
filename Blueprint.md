# Blueprint: Gastos Familiares

## Contexto

App de registro de gastos personales/familiares. Corre como contenedor Docker en una Raspberry Pi 4 (aarch64), desplegado via Docker Compose junto a Home Assistant y Cloudflare Tunnel. Los usuarios envían mensajes de texto por Telegram (`Supermercado 150000`) y la app registra el gasto, lo categoriza automáticamente, e identifica quién lo envió. El dashboard web muestra gráficos de gastos mensuales por categoría, semanales, por usuario, gastos fijos, y conversiones de dólares.

---

## Estructura de archivos

```
/                                  ← raíz del repositorio GitHub
├── docker-compose.yml             ← definición del servicio gastos para el Pi
├── .env.example                   ← template de variables de entorno
├── .github/
│   └── workflows/
│       └── docker-publish.yml     ← CI: construye y publica imagen a ghcr.io
└── gastos/
    ├── config.yaml                ← nombre, versión, schema del add-on (legacy HA, también usado para versioning)
    ├── Dockerfile                 ← imagen Docker (contexto: raíz del repo)
    ├── requirements.txt
    ├── CHANGELOG.md
    └── app/
        ├── main.py                ← entrypoint: arranca bot + dashboard en paralelo
        ├── bot.py                 ← lógica del bot Telegram
        ├── parser.py              ← parsing de mensajes de texto
        ├── categorizer.py         ← asignación de categorías por keywords
        ├── db.py                  ← todas las operaciones SQLite
        ├── dashboard.py           ← Flask app (rutas + API JSON)
        ├── backup.py              ← backup diario de la DB via Telegram
        ├── seed.py                ← datos iniciales + migraciones idempotentes
        └── templates/
            ├── base.html
            ├── index.html         ← dashboard principal
            ├── history.html       ← historial con filtros y edición inline
            ├── settings.html      ← gestión de categorías, subcategorías y keywords
            ├── fijos.html         ← gestión de gastos fijos recurrentes
            ├── dolares.html       ← registro de operaciones de cambio de divisas
            └── config.html        ← panel de sistema (backup, restore)
```

---

## Infraestructura en el Pi

El Pi corre tres servicios en `~/docker-compose.yml`:

```
homeassistant   → network_mode: host, datos en ~/homeassistant-data
cloudflared     → network_mode: host, conecta el tunnel de Cloudflare
gastos          → network_mode: host, datos en ~/gastos-data, puerto 5000
```

Las variables de entorno de todos los servicios viven en `~/.env`.

### Exposición externa

```
expenses.juampifinochietto.com
  → Cloudflare Tunnel
    → localhost:5000 (Flask dashboard)
```

Configurado manualmente en el dashboard de Cloudflare Zero Trust.

---

## CI/CD

`.github/workflows/docker-publish.yml` se dispara en cada push a `main`:

1. Checkout del repo
2. Setup QEMU + Docker Buildx (necesario para cross-compile arm64)
3. Login a `ghcr.io` con `GITHUB_TOKEN` (sin secrets extra)
4. Build y push multi-arch (`linux/arm64`, `linux/amd64`) con dos tags:
   - `ghcr.io/juanfino/lightweightexpensetracker:latest`
   - `ghcr.io/juanfino/lightweightexpensetracker:<git-sha>`

Para desplegar en el Pi: `docker compose pull gastos && docker compose up -d gastos`

---

## Dockerfile

```dockerfile
FROM python:3.11-alpine

RUN apk add --no-cache gcc musl-dev libffi-dev

WORKDIR /app
COPY gastos/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gastos/app/ .

ENV DB_PATH=/data/gastos.db

CMD ["python3", "/app/main.py"]
```

**Notas:**
- El contexto de build es la **raíz del repo**, no el subdirectorio `gastos/`. Por eso los `COPY` usan `gastos/`.
- No usa `bashio` ni depende de HA Supervisor. La config viene exclusivamente de variables de entorno.
- `/data` es el volumen persistente; en el Pi se monta desde `~/gastos-data`.

---

## Config — Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `TELEGRAM_TOKEN` | Sí | Token del bot de @BotFather |
| `USERS_JSON` | Sí | Array JSON de usuarios autorizados |
| `ANTHROPIC_API_KEY` | No | Habilita OCR de tickets con Claude |
| `DB_PATH` | No | Path al SQLite (default: `/data/gastos.db`) |

`USERS_JSON` formato: `[{"telegram_id": "123456", "name": "Juampi"}]`

---

## Database Schema

### Tablas

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    color       TEXT    NOT NULL DEFAULT '#6366f1',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    UNIQUE NOT NULL,
    color      TEXT    NOT NULL DEFAULT '#6366f1',
    icon       TEXT    NOT NULL DEFAULT '💰',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE subcategories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE TABLE keywords (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword        TEXT    UNIQUE NOT NULL,
    category_id    INTEGER NOT NULL,
    subcategory_id INTEGER,
    FOREIGN KEY (category_id)    REFERENCES categories(id) ON DELETE CASCADE,
    FOREIGN KEY (subcategory_id) REFERENCES subcategories(id) ON DELETE SET NULL
);

CREATE TABLE expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    category_id    INTEGER,
    subcategory_id INTEGER,
    concept        TEXT    NOT NULL,
    amount         REAL    NOT NULL,
    raw_text       TEXT    NOT NULL,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)        REFERENCES users(id),
    FOREIGN KEY (category_id)    REFERENCES categories(id),
    FOREIGN KEY (subcategory_id) REFERENCES subcategories(id)
);

CREATE TABLE fixed_expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    amount      REAL,
    category_id INTEGER,
    active      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE fixed_expense_payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fixed_expense_id INTEGER NOT NULL,
    expense_id      INTEGER,   -- nullable: puede marcarse pagado sin crear gasto
    period          TEXT NOT NULL,  -- 'YYYY-MM'
    UNIQUE (fixed_expense_id, period),
    FOREIGN KEY (fixed_expense_id) REFERENCES fixed_expenses(id),
    FOREIGN KEY (expense_id)       REFERENCES expenses(id)
);

CREATE TABLE cambios_dolar (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL,
    monto_usd   REAL    NOT NULL,
    cotizacion  REAL    NOT NULL,
    monto_ars   REAL    NOT NULL,
    usuario     TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## parser.py

Parsea mensajes de texto libres en `{concept, amount}`.

| Mensaje | Resultado |
|---|---|
| `Supermercado 150000` | concept=Supermercado, amount=150000 |
| `150000 nafta` | concept=Nafta, amount=150000 |
| `YPF 100.000` | concept=YPF, amount=100000 |
| `farmacia 2500.50` | concept=Farmacia, amount=2500.50 |

Regla: si hay exactamente 3 dígitos después del único separador (`.` o `,`), es separador de miles. Retorna `None` si no hay monto válido.

---

## categorizer.py

`categorize(concept, keywords) → (category_id, subcategory_id)`

- Normaliza concepto y keywords (lowercase + quitar acentos)
- Retorna la categoría + subcategoría del primer keyword que esté contenido en el concepto
- Retorna `(None, None)` si no hay match

---

## bot.py

### Autorización

Todo handler verifica primero `_get_authorized_user(update)`. Si el `chat_id` no está en `USERS`, responde con mensaje de rechazo y corta el flujo.

### Flujo de gasto normal

1. Parsear texto → `{concept, amount}`
2. Categorizar → `(category_id, subcategory_id)`
3. Guardar en DB
4. Responder confirmación con concepto, monto, categoría (y subcategoría si existe), usuario
5. Si quedó sin categoría: mostrar teclado inline con categorías paginadas por frecuencia de uso
6. Botón "✏️ Editar monto" siempre visible

### Comandos

| Comando | Descripción |
|---|---|
| `/gastos` | Resumen del mes: total + breakdown por categoría |
| `/semana` | Gastos de la semana actual (domingo–sábado, hora BA) |
| `/hoy` | Gastos del día (hora BA) |
| `/borrar [ID]` | Borra un gasto por ID |
| `/add_keyword PALABRA CATEGORÍA` | Agrega keyword |
| `/fijos` | Estado del mes de gastos fijos con botones de pago |
| `/categorias` | Lista de categorías |
| `/ayuda` | Todos los comandos y formato de carga |
| `CambioDolar <usd> <cotizacion>` | Registra operación de cambio de divisas |

### OCR de tickets

Handler `handle_photo` acepta fotos y documentos de imagen. Llama a `ocr.extract_ticket_data()` (claude-haiku-4-5-20251001) y muestra los datos extraídos para confirmación del usuario antes de guardar.

### Gastos Fijos — detección automática

Al registrar un gasto, el bot busca si el concepto coincide con algún gasto fijo activo (matching por palabras ≥3 chars). Si hay match, ofrece al usuario registrarlo como pago del fijo o como gasto normal con botones inline.

---

## dashboard.py — Rutas

```
GET  /                          → index.html (dashboard mensual/anual)
GET  /history                   → history.html (historial con filtros)
GET  /settings                  → settings.html (categorías, subcategorías, keywords)
GET  /fijos                     → fijos.html (gastos fijos recurrentes)
GET  /dolares                   → dolares.html (cambios de divisas)
GET  /config                    → config.html (sistema: backup, restore)

GET  /api/summary               → resumen mes actual
GET  /api/monthly               → gastos por categoría de un mes
GET  /api/annual/<year>         → desglose mensual por categoría y usuario
GET  /api/weekly                → gastos por semana
GET  /api/sparklines            → últimos 6 meses por categoría
GET  /api/expenses              → listado filtrable (month, year, category_id, user_id)
GET  /api/users                 → lista de usuarios con colores
GET  /api/subcategories         → subcategorías (filtrable por category_id)

POST /api/expenses/delete       → {id}
POST /api/expenses/update       → {id, concept, amount, category_id, subcategory_id}
POST /api/expenses/<id>/subcategory → {subcategory_id}
POST /api/keywords/add          → {keyword, category_id}
POST /api/keywords/delete       → {id}
PUT  /api/keywords/<id>         → {keyword, category_id, subcategory_id}
POST /api/subcategories/add     → {name, category_id}
POST /api/subcategories/delete  → {id}

GET  /api/fixed-expenses        → lista de gastos fijos activos
GET  /api/fixed-expenses/status → estado del mes (pagado/pendiente)
POST /api/fixed-expenses/add
POST /api/fixed-expenses/update
POST /api/fixed-expenses/deactivate
POST /api/fixed-expenses/pay    → crea gasto + marca como pagado
POST /api/fixed-expenses/mark-paid → marca pagado sin crear gasto

GET  /api/cambios/resumen
GET  /api/cambios/historial
GET  /api/cambios/por_mes
GET  /api/cambios/cotizacion_historica
DELETE /api/cambios/<id>
PUT  /api/cambios/<id>

POST /admin/backup-now
POST /admin/restore-db-url      → {url} descarga DB desde URL pública HTTPS, guarda .bak y reinicia
```

---

## main.py — Secuencia de arranque

1. Leer env vars (`TELEGRAM_TOKEN`, `USERS_JSON`, `ANTHROPIC_API_KEY`, `DB_PATH`)
2. Parsear `USERS_JSON` → dict `{telegram_id_str: name}`
3. `db.init_db(users)` — crea tablas + seed si es primera vez
4. Configurar módulo `backup` (token, usuarios, db path)
5. Iniciar APScheduler con job de backup a las 21:00 ART
6. Iniciar Flask en `threading.Thread(daemon=True)` en puerto 5000
7. Iniciar bot Telegram con `app.run_polling()` (bloquea el hilo principal)

---

## seed.py — Categorías y subcategorías por defecto

Categorías padre: Hogar, Alimentación, Vehículos, Salud, Servicios, Entretenimiento, Transporte, Educación, Hijos, Trabajo, Gastos Generales, Sin categoría.

Cada categoría tiene subcategorías y keywords asociadas. `seed()` es idempotente — se llama en cada arranque y solo inserta lo que falta. `seed_keyword_subcategories()` asigna `subcategory_id` a keywords existentes donde puede inferirse del keyword.

---

## Convenciones importantes

- Timestamps en DB siempre UTC; el dashboard convierte a `America/Argentina/Buenos_Aires`.
- `telegram_id` almacenado como TEXT (aunque es un int) para evitar comparaciones de tipo.
- `python-telegram-bot` v20+ es asíncrono. Todos los handlers usan `async/await`.
- Flask y el bot corren en el mismo proceso: Flask en thread daemon, bot bloquea el hilo principal.
- Separador de miles argentino: `100.000` = 100000. El parser maneja esto.
- `categorizer.categorize()` retorna `(category_id, subcategory_id)`. Todos los flujos deben pasar ambos valores a `create_expense()`.

---

## Despliegue manual en el Pi (primera vez)

```bash
# 1. Clonar repo
git clone https://github.com/juanfino/LightWeightExpenseTracker ~/gastos

# 2. Crear directorio de datos
mkdir -p ~/gastos-data

# 3. Completar variables en ~/.env
#    (copiar de .env.example y reemplazar PLACEHOLDERs)

# 4. Agregar servicio gastos al docker-compose.yml existente
#    (copiar el bloque de docker-compose.yml del repo)

# 5. Levantar
docker compose pull gastos && docker compose up -d gastos

# 6. Verificar
docker logs -f gastos
```
