# Gastos Familiares

Registro de gastos familiares via Telegram con dashboard web.

## Monedas

La moneda por defecto es **ARS**. Para registrar un gasto en dólares escribí, por ejemplo, `Netflix 15 USD` o `Hotel 200 dólares`; el bot también lo reconoce por voz. En el dashboard podés elegir ARS/USD al agregar o editar un gasto, y el Historial permite filtrarlos. Los totales siempre se muestran separados: la app no convierte dólares ni los suma con pesos.

Los gastos fijos también tienen moneda y un pago hereda la de su fijo. Un gasto vinculado a un fijo no puede cambiar de moneda hasta desvincularse. El OCR parte de ARS: antes de confirmar el ticket se puede elegir USD.

## Configuración

La app se configura mediante variables de entorno:

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `TELEGRAM_TOKEN` | Sí | Token del bot obtenido con @BotFather en Telegram |
| `TELEGRAM_BOT_USERNAME` | Sí | Usuario del bot sin `@`, usado para el enlace de conexión |
| `PUBLIC_DASHBOARD_URL` | No | URL pública que recibe un chat todavía no vinculado |
| `USERS_JSON` | Sí | Lista JSON de usuarios autorizados, ej: `[{"telegram_id":"123","name":"Juampi","email":"opcional@ejemplo.com"}]`; el email opcional vincula una cuenta Telegram histórica con el acceso web |
| `AUTH_SECRET_KEY` | Sí | Secreto aleatorio para el estado temporal de autenticación |
| `AUTH_BOOTSTRAP_EMAIL` | Sí | Email inicial del dueño de la familia existente |
| `SUPERADMIN_EMAIL` | Sí | Email del superadministrador de la instalación |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Sí | Credenciales OAuth de Google |
| `RESEND_API_KEY` | Sí | Envío de códigos de acceso por email |
| `TURNSTILE_SECRET` | Sí | Secreto privado para verificar Turnstile del lado del servidor |
| `ANTHROPIC_API_KEY` | No | Habilita el OCR de tickets, la extracción por voz/dólar y el modo conversacional en lenguaje natural |
| `OPENAI_API_KEY` | No | Habilita la transcripción de mensajes de voz (Whisper) |
| `DATABASE_URL` | Sí | URL de conexión a PostgreSQL |
| `DASHBOARD_PORT` | No | Puerto del dashboard (default: `5000`) |

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

### Por voz

Si está configurada la `OPENAI_API_KEY` (además de `ANTHROPIC_API_KEY`), le podés mandar un audio al bot en vez de escribir, ej. "gasté 30 mil en la verdulería". Si el monto queda claro se registra solo (con teclado para editar); si no, pide confirmación.

### Dólares (compra/venta)

Con `ANTHROPIC_API_KEY` configurada, también se pueden registrar operaciones de cambio en lenguaje natural (texto o audio): `vendí 500 dólares a 1700`, `compré 1000 dólares a 1550 cada uno`. Sigue funcionando el comando clásico `CambioDolar <monto_usd> <cotizacion>` (registra una venta).

## Comandos

| Comando | Descripción |
|---------|-------------|
| `/gastos` | Resumen del mes |
| `/semana` | Gastos de esta semana |
| `/hoy` | Gastos de hoy |
| `/sincat` | Gastos sin categoría |
| `/fijos` | Estado del mes de gastos fijos, con botones para marcar pago |
| `/editar ID monto VALOR` | Editar monto de un gasto |
| `/editar ID moneda ARS\|USD` | Editar moneda (salvo gastos vinculados a un fijo) |
| `/editar ID categoria NOMBRE` | Editar categoría de un gasto |
| `/recat CONCEPTO CATEGORÍA` | Reasignar gastos por concepto |
| `/borrar ID` | Borrar un gasto |
| `/add_keyword PALABRA CATEGORÍA` | Agregar keyword → categoría |
| `/nueva_categoria Nombre Emoji Color` | Crear categoría |
| `/categorias` | Listar categorías |
| `/ayuda` | Ver todos los comandos |

También se puede mandar una foto o documento de imagen de un ticket: el bot extrae comercio/monto/fecha por OCR y pide confirmación antes de guardar.

## Dashboard

En la URL pública, `/` muestra la presentación del servicio; `/privacy` y `/terms` contienen las páginas legales. El acceso se hace con Google o con un código de seis dígitos enviado por email; Google muestra siempre el selector para evitar entrar por error con otra cuenta abierta en el dispositivo. Una vez autenticado, el Dashboard vive en `/dashboard`. **Cerrar sesión** revoca la sesión del servidor y vuelve al login. Las demás pantallas siguen en Movimientos (`/history`), Ingresos (`/ingresos`), Lista (`/lista`), Categorías (`/settings`), Gastos Fijos (`/fijos`), Dólares (`/dolares`), Resúmenes (`/resumenes`), Exportar (`/exportar`) y Sistema (`/config`).

Una familia nueva ve una checklist de primeros pasos en el Dashboard: crear la cuenta, cargar el primer gasto, conectar Telegram (opcional) e invitar a alguien. Cada paso lleva directamente a la pantalla correspondiente y la tarjeta desaparece cuando todos están completos. Las pantallas sin datos muestran qué va a aparecer ahí y un botón para empezar.

Desde **Historial → Agregar gasto** se puede elegir categoría y subcategoría. Al final de cada selector aparece la opción para crear una nueva ahí mismo; la subcategoría se crea dentro de la categoría elegida y queda seleccionada automáticamente para el gasto en curso.

En **Categorías**, una “palabra para categorizar” asocia un texto frecuente con una categoría o subcategoría: por ejemplo, `carrefour` → Hogar / Supermercado. Los siguientes gastos que contengan esa palabra se ordenan automáticamente.

En **Ingresos** se registran sueldos, honorarios, alquileres, ventas, reintegros, inversiones, regalos y otros ingresos. ARS y USD se muestran separados y la aplicación no los convierte ni calcula un balance artificial. Cada persona puede editar o eliminar únicamente sus propios ingresos. Las categorías de ingresos son independientes de las categorías de gastos y se administran desde la misma pantalla.

En Telegram, `Ingreso: sueldo 1.500.000` usa el camino rápido. También se entienden frases inequívocas como `cobré 300.000 de un freelance` o `me depositaron el sueldo`. El formato corto `concepto monto` sin prefijo continúa registrando un gasto.

En **Lista** (`/lista`), toda la familia comparte los productos pendientes agrupados por categoría. Se puede agregar una cantidad libre (`2 paquetes`, `1 kg`), editar, marcar comprado, reagregar desde los últimos 30 días o limpiar los comprados. Un producto pendiente repetido no se duplica: la cantidad nueva lo actualiza.

Telegram entiende `falta detergente`, `necesitamos dos leches`, `compré el detergente` y `qué falta comprar`. Un mensaje con monto es un gasto; algo faltante o necesario sin monto pertenece a la lista.

En **Exportar** (`/exportar`) cualquier miembro activo puede descargar CSV de Movimientos, Gastos fijos, Dólares, Ingresos, Lista y Taxonomía, o un ZIP con todo. Incluyen el nombre de la persona, pero no emails ni identificadores de Telegram. Para Excel en español, usar **Datos → Desde texto/CSV** y elegir coma.

En **Fijos** se configuran gastos que se repiten, como alquiler, internet o cuotas. En **Resúmenes**, cada generación usa IA sobre los datos del grupo, consume una de las 15 generaciones mensuales disponibles y puede tardar cerca de un minuto.

Desde el nombre de la familia en la barra superior se abre **Familia** (`/familia`). El owner puede generar un enlace para copiar y enviar por WhatsApp; vence a los 7 días y funciona una sola vez. Quien lo recibe se une con Google o código por email como miembro. El owner también puede renombrar la familia, remover miembros sin borrar sus gastos, transferir la propiedad y eliminar definitivamente la familia escribiendo su nombre exacto. Un miembro puede salir por su cuenta; sus gastos anteriores quedan visibles en la familia.

Cada miembro puede elegir **Conectar mi Telegram**. En el celular se toca el botón; desde una computadora se escanea el QR. Telegram abre el bot directamente y alcanza con tocar **INICIAR**: la web confirma sola la conexión. El enlace vence a los 15 minutos y es de un solo uso. La cuenta se puede desconectar desde Familia. Los grupos todavía no están soportados.

Las funciones de IA admiten 100 llamadas por familia por día. La carga directa `Supermercado 15000` no usa IA y sigue funcionando si se alcanza ese límite. Resúmenes admite 15 generaciones por familia por mes y muestra cuántas quedan.
