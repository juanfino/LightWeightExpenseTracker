# Gastos Familiares

Registro de gastos familiares via Telegram con dashboard web.

## Configuración

La app se configura mediante variables de entorno:

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `TELEGRAM_TOKEN` | Sí | Token del bot obtenido con @BotFather en Telegram |
| `USERS_JSON` | Sí | Lista JSON de usuarios autorizados, ej: `[{"telegram_id":"123","name":"Juampi"}]` |
| `ANTHROPIC_API_KEY` | No | Habilita el reconocimiento de tickets por foto (OCR) |
| `DB_PATH` | No | Ruta a la base de datos SQLite (default: `/data/gastos.db`) |

### Obtener el telegram_id de cada usuario
Cada usuario debe enviarle un mensaje a @userinfobot en Telegram.

## Uso

Enviá un mensaje al bot con el formato:
Concepto Monto
Ejemplos: `Supermercado 15000`, `YPF 100.000`, `Cine 5000`

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