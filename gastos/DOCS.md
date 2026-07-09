# Gastos Familiares

Registro de gastos familiares via Telegram con dashboard web.

## Configuración

La app se configura mediante variables de entorno:

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `TELEGRAM_TOKEN` | Sí | Token del bot obtenido con @BotFather en Telegram |
| `USERS_JSON` | Sí | Lista JSON de usuarios autorizados, ej: `[{"telegram_id":"123","name":"Juampi"}]` |
| `ANTHROPIC_API_KEY` | No | Habilita el OCR de tickets, la extracción por voz/dólar y el modo conversacional en lenguaje natural |
| `DB_PATH` | No | Ruta a la base de datos SQLite (default: `/data/gastos.db`) |

### Obtener el telegram_id de cada usuario
Cada usuario debe enviarle un mensaje a @userinfobot en Telegram.

## Uso

Enviá un mensaje al bot con el formato:
Concepto Monto
Ejemplos: `Supermercado 15000`, `YPF 100.000`, `Cine 5000`

### Modo conversacional (lenguaje natural)

Si está configurada la `ANTHROPIC_API_KEY`, además del formato clásico podés hablarle al bot con lenguaje natural:
- Registrar: `anotame 100 lucas en el súper`
- Editar: `che, me equivoqué, el último gasto fueron 90000` · `el gasto 124: total 40000 y categoría nafta`
- Categorías: `agregá la categoría Niños` · `en Casa agregá la subcategoría Productos de limpieza`
- Consultar: `cuánto gasté esta semana` · `cuánto gastó Cele en comida en marzo`

Los gastos conversacionales se registran solos (podés editarlos con los botones); las ediciones y la creación de categorías piden confirmación. Por Telegram cada usuario solo puede editar sus propios gastos.

## Comandos

| Comando | Descripción |
|---------|-------------|
| `/gastos` | Resumen del mes |
| `/semana` | Gastos de esta semana |
| `/hoy` | Gastos de hoy |
| `/sincat` | Gastos sin categoría |
| `/editar ID monto VALOR` | Editar monto de un gasto |
| `/editar ID categoria NOMBRE` | Editar categoría de un gasto |
| `/recat CONCEPTO CATEGORÍA` | Reasignar gastos por concepto |
| `/borrar ID` | Borrar un gasto |
| `/nueva_categoria Nombre Emoji Color` | Crear categoría |
| `/categorias` | Listar categorías |
| `/ayuda` | Ver todos los comandos |

## Dashboard

Accesible en `http://[IP-RASPI]:5000` desde la red local.