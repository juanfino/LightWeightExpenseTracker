# Gastos Familiares

Registro de gastos familiares via Telegram con dashboard web.

## Configuración

| Campo | Descripción |
|-------|-------------|
| `telegram_token` | Token del bot obtenido con @BotFather en Telegram |
| `users` | Lista de usuarios autorizados |

### Obtener chat_id de cada usuario
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