# Auditoría de primer contacto del bot de Telegram

Auditoría documental del recorrido que vive una persona externa que acaba de vincular
Telegram y no recibió ninguna explicación previa. Fuente de verdad: `PROJECT.md` y el
código actual de `bot.py`, `intent.py`, `parser.py`, `exchange.py`, `audio.py`, `ocr.py`,
`llm_limits.py`, `auth.py` y las rutas web a las que el bot deriva. No se probaron cambios
de copy ni se modificó comportamiento.

Las consecuencias se clasifican como pide el brief:

- **Datos incorrectos:** el recorrido puede guardar o dejar persistido algo distinto de
  lo que la persona cree.
- **Capacidad sin uso:** la función existe pero una persona que sólo conoce lo que dice el
  bot difícilmente la descubra.
- **Callejón sin salida:** el bot rechaza, falla o pide una acción que no explica cómo
  completar.

---

## 1. Recorrido de primer contacto actual

### 1.1. Vinculación

El dashboard prepara un deep link de 15 minutos y un solo uso. Al abrirlo, Telegram envía
`/start <token>`.

Si funciona, `cmd_start()` responde:

> ✅ ¡Listo, {nombre}! Tu Telegram quedó conectado.
>
> Probá ahora con:
> Supermercado 15000

La confirmación hace bien dos cosas: confirma inequívocamente que la vinculación terminó
y enseña el formato básico `concepto monto` con un ejemplo real. No explica qué ocurrirá
al enviarlo ni menciona `/ayuda`.

Si la persona vuelve a ejecutar `/start` ya vinculada, ve:

> 👋 Ya estás conectado. Probá cargar un gasto así:
> Supermercado 15000

Si el token venció o ya se usó, ve solamente:

> ⚠️ Este enlace venció o ya fue utilizado.

Si ese Telegram pertenece a otra identidad, ve solamente:

> ⚠️ Este Telegram ya está conectado a otra cuenta.

Ninguno de esos dos rechazos explica el próximo paso.

### 1.2. Primer mensaje

Si sigue el ejemplo y manda `Supermercado 15000`, `handle_message()` guarda el gasto de
inmediato y responde:

> ✅ Gasto registrado
> 📋 Supermercado
> 💰 $ 15.000,00
> {emoji} {categoría inferida, o Sin categoría}
> 👤 {nombre}
> #ID{id}

Si encontró una categoría por keyword, el teclado sólo ofrece `✏️ Editar monto`,
`💱 Moneda` y `🔗 Gasto fijo`. No dice que la categoría fue inferida ni ofrece
cambiarla ahí. Si no encontró categoría, sí muestra el selector completo; después de
elegir, la confirmación actual ya explica correctamente el aprendizaje:

> 🧠 Los gastos parecidos a "{concepto}" ya van a ir solos a esta categoría.

No hace falta agregar más texto a esa confirmación: ya cumple su trabajo.

Si el primer texto no tiene un monto parseable, el resultado depende de la configuración:

- Con Anthropic configurado, todo texto que el parser no entiende pasa a la capa de
  intención. Un saludo puede recibir una respuesta libre; un gasto cuyo monto quedó ambiguo
  suele terminar en:

  > 🤔 No me quedó claro el monto. ¿Me lo repetís?

- Sin Anthropic, la respuesta determinística es:

  > ❓ No pude entender el gasto.
  > Formatos válidos:
  > Supermercado 15000
  > 15000 nafta
  > Cena con amigos 8500,50

- El fast path de ingresos sí da un ejemplo accionable cuando no puede parsear:

  > ❓ No pude entender el ingreso. Ejemplo: Ingreso: sueldo 1.500.000

Un comando desconocido toma otro camino. Para una persona ya vinculada,
`unknown_command()` no envía ninguna respuesta. Por ejemplo, `/help`, `/ayuuda` o
`/gasto` quedan en silencio.

### 1.3. Primera automatización visible

#### Categorización automática

El primer gasto puede aparecer ya categorizado dentro de la confirmación anterior. No hay
un mensaje separado que diga que fue una inferencia. El usuario ve el resultado, pero no
el mecanismo ni una corrección de categoría en el teclado inmediato.

#### Oferta de vínculo a un gasto fijo

Después de cualquier alta —texto simple, lenguaje natural, voz u OCR— el matcher puede
enviar un segundo mensaje. Con una coincidencia:

> 💡 ¿Es el pago de tu gasto fijo {fijo}?
>
> [✅ Sí] [❌ No]

Si ese fijo ya tiene un pago en el período:

> 💡 ¿Es un segundo pago de tu gasto fijo {fijo}?

Con varias coincidencias:

> 💡 Este gasto coincide con varios gastos fijos. ¿Es el pago de alguno?

La pregunta comunica bien que nada se vincula solo. No comunica que aceptar copia la
categoría y subcategoría del fijo, reemplazando las actuales, ni que marca como pagado el
período derivado de la fecha del gasto. La confirmación posterior sólo dice:

> ✅ Vinculado a tu gasto fijo {fijo}.

#### Nota de voz

Primero aparece:

> 🎙️ Procesando audio...

Si hay gastos de confianza alta, el bot anticipa la automatización:

> 🎙️ Escuché: "{transcripción}"
> Registré {n} automáticamente:

Luego muestra una confirmación `✅ Gasto registrado` por cada alta y deja botones para
editar monto, moneda y vínculo fijo; si no hubo categoría, también deja elegirla. Para
extracciones de menor confianza pregunta `¿Guardamos?` antes. `/ayuda` ya avisa: “Si es
claro, se registra solo; si no, te pido confirmar”. El alta automática por voz está
razonablemente explicada y es recuperable; no necesita más copy general.

#### Cambio de moneda

Si la confianza es baja, el bot muestra los dos lados, la tasa y la fecha y pregunta:

> 💱 Entendí este cambio de moneda:
> {resumen de la operación}
>
> ¿Registramos?

Si la confianza es alta, omite esa confirmación y responde directamente:

> ✅ Cambio registrado
> {resumen de la operación}

No anticipó en ningún mensaje que un cambio claro se guardaría solo, no ofrece deshacer ni
editar en Telegram y `/ayuda` tampoco documenta esa regla para cambios.

El alta conversacional de un gasto (`anotame 100 lucas en el súper`) también se guarda sin
confirmación y responde con el mismo bloque `✅ Gasto registrado`. Es corregible con el
teclado parcial o con una edición conversacional, pero ninguna de esas dos capacidades está
explicada al llegar.

### 1.4. `/ayuda` hoy

El único índice bajo demanda del bot dice:

> 📖 Gastos Familiares — Comandos disponibles
>
> 💰 REGISTRAR UN GASTO
> Supermercado 15000
> YPF 100.000
>
> 🎙️ POR VOZ
> Mandá un audio: "gasté 30 mil en la verdulería".
> Si es claro, se registra solo; si no, te pido confirmar.
>
> 💱 CAMBIOS DE MONEDA
> Escribí o mandá audio en lenguaje natural:
> "vendí 500 dólares a 1700"
> "cambié 100 reales por 18 euros"
>
> 📊 CONSULTAS
> /gastos → resumen del mes
> /semana → gastos de esta semana
> /hoy → gastos de hoy
> /sincat → gastos sin categoría
> /fijos → estado gastos fijos del mes
>
> ✏️ EDITAR
> /editar ID monto 15000
> /editar ID categoria Vehiculos
> /editar ID fecha 15/06/2026
> /recat papota Entretenimiento
>
> 🗑️ BORRAR
> /borrar ID
>
> 🏷️ KEYWORDS
> /add_keyword nafta Vehiculos
> /categorias → lista de categorías
>
> ⚙️ CATEGORÍAS
> /nueva_categoria Mascotas 🐶 #f59e0b
> (gestión completa en el dashboard web)
>
> ❓ AYUDA
> /ayuda → este mensaje

Lo que documenta existe. Lo que omite también existe y no tiene otro índice dentro del bot:

- foto o documento imagen de un ticket, con OCR y confirmación;
- gastos en otras monedas (`Hotel 200 EUR`, `Taxi 80 reales`);
- registro conversacional de gastos y por voz más allá del ejemplo rígido;
- edición conversacional de monto, concepto, fecha, moneda, categoría, subcategoría y
  vínculo fijo;
- preguntas libres de solo lectura sobre gastos de toda la familia, incluidos follow-ups
  durante cinco minutos;
- crear categorías y subcategorías en lenguaje natural;
- registrar ingresos con `Ingreso: concepto monto` o frases como `cobré...`;
- agregar, listar y marcar productos comprados en la lista familiar;
- `/editar ID moneda USD`, que sí existe en el handler por comando;
- que una nota de voz también puede contener preguntas o ediciones conversacionales;
- que editar por comando o lenguaje natural está limitado a gastos propios.

No conviene promover `CambioDolar`: es un adaptador legacy y el flujo natural ya cubre más
pares. Tampoco hace falta explicar en `/ayuda` cada botón que aparece justo cuando se necesita.

### 1.5. Rechazos y fallos

| Estado | Mensaje actual | ¿Dice qué hacer? |
|---|---|---|
| Cupo diario agotado | `⏳ Tu familia alcanzó el límite diario de IA. Se habilita de nuevo mañana a las 00:00. Mientras tanto podés seguir cargando gastos como: Supermercado 15000` | **Sí.** Da reset y alternativa concreta. Ya cumple su trabajo. |
| Chat sin vincular | `👋 Para usar este bot, primero conectá tu cuenta desde el dashboard:` + URL exacta a `/vincular-telegram` | **Sí.** Lleva al punto de resolución. Ya cumple su trabajo. |
| Grupo | `👋 Todavía no admito grupos. Escribime por privado para que cada gasto quede asociado a la persona correcta.` | **Sí.** Indica canal y motivo. Ya cumple su trabajo. |
| Monto no parseable, sin IA | `❓ No pude entender el gasto.` + tres formatos válidos | **Sí.** Da ejemplos concretos. |
| Monto ambiguo, con IA | `🤔 No me quedó claro el monto. ¿Me lo repetís?` | **Parcial.** Pide repetir, pero no enseña el formato que siempre funciona. |
| Monto inválido durante una edición/pago pendiente | `❌ Monto inválido. Ejemplos: 15000, 2500,50` | **Sí.** |
| Ticket ilegible o fallo OCR | `❌ No pude analizar el ticket. Intentá cargar el gasto manualmente. Formato: Comercio monto` | **Sí.** |
| Audio: fallo de transcripción/extracción | `❌ No pude procesar el audio. Intentá de nuevo o cargá el gasto manualmente: Comercio monto`, con `🔄 Reintentar` | **Sí.** Es el mejor fallback de IA del bot. |
| Audio sin montos | muestra lo escuchado y `No pude detectar ningún monto. Cargá los gastos manualmente: Comercio monto` | **Sí.** |
| Capa conversacional falla | `⚠️ Hubo un error interpretando el mensaje. Probá de nuevo.` | **No del todo.** Reintentar el mismo camino dependiente de IA no da una salida si el servicio sigue caído. |
| Interpretación de cambio falla | No hay mensaje específico: el texto sigue hacia el parser de gastos o de voz | **No.** Puede terminar guardado como otra cosa. |
| Excepción no manejada | `handle_bot_error()` registra logs/telemetría, pero no responde al chat | **No.** La persona ve silencio. |
| Link vencido/usado o conflicto de identidad | `⚠️ Este enlace venció o ya fue utilizado.` / `⚠️ Este Telegram ya está conectado a otra cuenta.` | **No.** Describe el problema pero no la recuperación. |
| Comando desconocido, usuario vinculado | No hay mensaje | **No.** Ni siquiera confirma que el comando no existe. |

### 1.6. Miembros que no son owner

Vincular Telegram, registrar gastos e ingresos, usar la lista, gestionar categorías/fijos y
consultar datos familiares está disponible también para miembros. No se encontró un falso
CTA del bot hacia una operación web reservada al owner.

Sí hay un callejón dentro del propio bot: `/hoy`, `/semana` y `/sincat` muestran gastos de
toda la familia con sus IDs. `/sincat` termina diciendo explícitamente:

> Usá /editar ID categoria NOMBRE para corregirlos
> O /recat CONCEPTO CATEGORÍA para reasignar en masa

Pero `/editar` rechaza cualquier gasto de otro miembro con:

> ❌ Solo podés editar tus propios gastos.

Un miembro puede, entonces, seguir literalmente la instrucción pegada a un gasto familiar y
descubrir recién al final que no tiene permiso. `/ayuda` tampoco declara el límite. En cambio,
las consultas conversacionales sí ven a toda la familia y `/recat` actúa sobre gastos
familiares; esa asimetría tampoco está explicada.

---

## 2. Hallazgos, en el orden en que impactan

### 1. El alta enseña una sola acción y esconde el índice de ayuda

- **Dónde:** `cmd_start()`, confirmación de `/start <token>` y `/start` ya vinculado.
- **Texto actual:** `Probá ahora con: Supermercado 15000` / `Probá cargar un gasto así: Supermercado 15000`.
- **Qué queda creyendo:** que el bot sirve para ingresar un gasto con una sintaxis rígida;
  no hay señal de que exista un manual ni de que pueda hacer bastante más.
- **Consecuencia:** **capacidad sin uso** desde el primer minuto.
- **Tipo de fix:** **cambio de mensaje**. El ejemplo actual ya es bueno; sólo falta una línea.

### 2. Un link fallido no ofrece recuperación

- **Dónde:** `cmd_start()` al propagar los `ValueError` de
  `auth.consume_telegram_link_token()`.
- **Texto actual:** `⚠️ Este enlace venció o ya fue utilizado.` o
  `⚠️ Este Telegram ya está conectado a otra cuenta.`
- **Qué queda creyendo:** que el vínculo no funciona y que debe resolverlo fuera del bot sin
  saber dónde.
- **Consecuencia:** **callejón sin salida** antes de poder usar el producto.
- **Tipo de fix:** **cambio de mensaje**; para el conflicto, el flujo también debe indicar que
  primero hay que desconectar la otra cuenta desde Familia.

### 3. Los comandos desconocidos reciben silencio

- **Dónde:** `unknown_command()`; afecta especialmente `/help`, `/ayuuda` y errores de tipeo.
- **Texto actual:** ninguno para un chat ya vinculado.
- **Qué queda creyendo:** que el bot no funciona o no leyó el mensaje. `/help`, la primera
  conjetura natural de mucha gente, no conduce a `/ayuda`.
- **Consecuencia:** **callejón sin salida** y **capacidad sin uso**.
- **Tipo de fix:** **mensaje faltante**.

### 4. La primera aclaración de monto con IA no enseña la salida segura

- **Dónde:** `intent._handle_log()` y `intent._handle_log_income()`.
- **Texto actual:** `🤔 No me quedó claro el monto. ¿Me lo repetís?` y
  `🤔 No me quedó claro el monto del ingreso.`
- **Qué queda creyendo:** que debe insistir con la misma frase; en ingresos ni siquiera hay
  una pregunta o ejemplo.
- **Consecuencia:** **callejón sin salida** si la segunda interpretación vuelve a fallar.
- **Tipo de fix:** **cambio de mensaje** con un ejemplo corto del formato determinístico.

### 5. La categoría inferida parece una decisión del usuario y no se puede corregir ahí

- **Dónde:** `handle_message()`, `_nl_do_log()`, `_register_voice_expense()` y confirmación
  OCR/voz cuando `category_id` ya existe; teclado `_build_edit_only_keyboard()`.
- **Texto actual:** el bloque `✅ Gasto registrado` muestra `{emoji} {categoría}`, sin marcarla
  como automática; el teclado ofrece monto, moneda y gasto fijo, pero no categoría.
- **Qué queda creyendo:** que esa clasificación es definitiva o que el bot no permite
  corregirla. Quien no conoce `/editar` ni el lenguaje natural no tiene salida visible.
- **Consecuencia:** **datos incorrectos** que alimentan totales y reportes.
- **Tipo de fix:** **cambio de flujo**: hacer corregible la categoría desde toda confirmación
  de alta. Una etiqueta breve de “categoría automática” puede acompañar, pero no reemplaza el
  control.

### 6. El vínculo fijo es opt-in, pero oculta sus dos efectos

- **Dónde:** `_maybe_offer_fixed_link()` y callback `fixlink:`.
- **Texto actual:** `💡 ¿Es el pago de tu gasto fijo {fijo}?` →
  `✅ Vinculado a tu gasto fijo {fijo}.`
- **Qué queda creyendo:** que “vincular” agrega una etiqueta. En realidad marca un período
  como pagado y reemplaza categoría/subcategoría por las del fijo.
- **Consecuencia:** **datos incorrectos** o un fijo que parece pagado en un mes inesperado.
- **Tipo de fix:** **cambio de mensaje** en la oferta; no hace falta rediseñar la confirmación.

### 7. El auto-guardado de cambios de moneda no está anunciado ni es recuperable en chat

- **Dónde:** `_handle_exchange_operation()` y `_register_exchange_and_announce()`.
- **Texto actual:** con confianza alta salta directo a `✅ Cambio registrado`; no hay botones.
- **Qué queda creyendo:** que el bot iba a interpretar o confirmar como en otros flujos; la
  operación ya quedó persistida. La ayuda sólo explica auto-save dentro de la sección de gastos
  por voz, no para cambios escritos o hablados.
- **Consecuencia:** **datos incorrectos** y **callejón sin salida** en Telegram.
- **Tipo de fix:** **cambio de flujo**: confirmar siempre los cambios o agregar una acción de
  deshacer/editar. Un mensaje posterior no evita el dato erróneo.

### 8. [BLOQUEA CAPACIDADES] `/ayuda` sigue documentando sólo una parte del bot

- **Dónde:** `cmd_ayuda()` frente a `handle_photo()`, `handle_voice()`,
  `handle_message()` y las herramientas de `intent.py`.
- **Texto actual:** transcripto completo en §1.4.
- **Qué queda creyendo:** que no existen tickets, gastos multimoneda, registro/edición/
  consultas conversacionales, ingresos ni lista de compras; tampoco descubre editar moneda o
  usar voz para preguntas y ediciones.
- **Consecuencia:** **capacidad sin uso**. Ticket OCR, ingresos y lista de compras son áreas
  enteras del producto, no variantes menores del alta de gastos.
- **Tipo de fix:** **cambio de mensaje**. `/ayuda` es el lugar correcto; su contenido es el
  problema.

### 9. Los fallos de IA no comparten el buen fallback del audio/OCR

- **Dónde:** `_handle_intent_message()` y los retornos `kind="error"` de `intent.py`.
- **Texto actual:** `⚠️ Hubo un error interpretando el mensaje. Probá de nuevo.`
- **Qué queda creyendo:** que repetir resolverá el problema, aunque siga caído el proveedor.
- **Consecuencia:** **callejón sin salida**.
- **Tipo de fix:** **cambio de mensaje**, reutilizando el fallback manual que ya usa voz.

### 10. Un fallo interpretando un cambio puede crear un gasto común

- **Dónde:** `handle_message()` y `_process_voice_audio()` después de que
  `exchange.parse_exchange()` devuelve `None` tanto por “no es cambio” como por excepción,
  JSON inválido o datos incompletos.
- **Texto actual:** ninguno sobre el fallo. El mensaje continúa al parser de gastos o al
  extractor de gastos de voz. Por ejemplo, una frase con dos montos como
  `vendí 500 dólares a 1700` conserva números suficientes para que el fast path pueda tratar
  `1700` como monto de un gasto con concepto `Vendí 500 A`.
- **Qué queda creyendo:** que registró o intentó registrar un cambio, mientras puede recibir
  `✅ Gasto registrado` por otra cosa.
- **Consecuencia:** **datos incorrectos**, el hallazgo más grave del recorrido posterior al
  alta.
- **Tipo de fix:** **cambio de flujo**. El parser debe distinguir “no era cambio” de “no pude
  interpretar el cambio” y nunca caer silenciosamente a un alta de gasto después de una señal
  inequívoca de conversión.

### 11. Las excepciones no manejadas dejan al usuario sin respuesta

- **Dónde:** `handle_bot_error()`.
- **Texto actual:** ninguno; sólo log y `system_errors`.
- **Qué queda creyendo:** que el bot ignoró el mensaje o botón.
- **Consecuencia:** **callejón sin salida**.
- **Tipo de fix:** **mensaje faltante** genérico al chat, sin exponer detalles internos.

### 12. Un miembro recibe IDs familiares que después no puede editar

- **Dónde:** `/hoy`, `/semana`, `/sincat`, el pie de `cmd_sincat()` y `cmd_editar()`.
- **Texto actual:** `/sincat` dice `Usá /editar ID categoria NOMBRE para corregirlos`; al
  elegir un gasto de otra persona, `/editar` responde `❌ Solo podés editar tus propios gastos.`
- **Qué queda creyendo:** que cualquier ID presentado por el bot es accionable. El owner
  tampoco puede corregir por bot un gasto ajeno: la restricción es por autor, no por rol.
- **Consecuencia:** **callejón sin salida** específico de familias con más de un miembro.
- **Tipo de fix:** **cambio de mensaje** en `/sincat` y `/ayuda`, o **cambio de flujo** para que
  el CTA sólo acompañe gastos editables por quien consulta.

---

## 3. Cambios propuestos por lotes independientes

El copy siguiente está cerrado en español rioplatense y pensado para pantalla de celular.
Los lotes describen implementaciones futuras; esta auditoría no las aplica.

### Lote 1 — Entrada y recuperación básica

**1a. Hacer `/ayuda` descubrible desde el alta.** Mantener el ejemplo actual y agregar:

> Escribí /ayuda para ver todo lo que podés hacer.

**1b. Recuperar links fallidos.** Para token vencido/usado:

> ⚠️ Este enlace venció o ya se usó. Volvé a Vincular Telegram en el dashboard y generá otro.

Para identidad en conflicto:

> ⚠️ Este Telegram ya está conectado a otra cuenta. Desconectalo desde Familia en esa cuenta y probá de nuevo.

**1c. Responder comandos desconocidos.** Mensaje faltante:

> ❓ No conozco ese comando. Escribí /ayuda para ver los disponibles.

**1d. Dar formato seguro ante monto ambiguo.** Gasto:

> 🤔 No me quedó claro el monto. Probá así: Supermercado 15000.

Ingreso:

> 🤔 No me quedó claro el monto. Probá así: Ingreso: sueldo 1500000.

### Lote 2 — [BLOQUEA CAPACIDADES] Referencia completa

Reescribir `/ayuda` como índice completo, sin convertirlo en manual largo:

> 💰 **GASTOS**
> Supermercado 15000
> Hotel 200 EUR
> También podés decir: “anotame 100 lucas en el súper”.
>
> 📸 **TICKETS**
> Mandá una foto. Te muestro comercio, monto y fecha antes de guardar.
>
> 🎙️ **POR VOZ**
> Mandá gastos, cambios, preguntas o correcciones.
> Si un gasto es claro, se guarda solo; si no, te pido confirmar.
>
> 💵 **INGRESOS**
> Ingreso: sueldo 1500000
> O decime: “cobré 200000 de un trabajo”.
>
> 🛒 **LISTA DE COMPRAS**
> “Falta detergente” · “¿qué falta comprar?” · “compré el detergente”
>
> 💬 **PREGUNTAS Y CAMBIOS**
> “¿Cuánto gastamos en comida este mes?”
> “El último de nafta fueron 30000”
> Podés editar sólo tus gastos; las consultas ven a toda la familia.
>
> 💱 **CAMBIOS DE MONEDA**
> “Vendí 500 dólares a 1700”
> “Cambié 100 reales por 18 euros”
>
> 📊 **ATAJOS**
> /gastos · /semana · /hoy · /sincat · /fijos
> /categorias · /nueva_categoria
>
> ✏️ **EDITAR O BORRAR**
> /editar ID monto 15000
> /editar ID moneda USD
> /editar ID categoria Vehiculos
> /editar ID fecha 15/06/2026
> /borrar ID
> /recat papota Entretenimiento
>
> 🏷️ **ENSEÑAR UNA CATEGORÍA**
> /add_keyword nafta Vehiculos

La versión implementada debería conservar HTML de Telegram; los asteriscos de arriba sólo
marcan jerarquía en este documento.

### Lote 3 — Evitar datos silenciosamente incorrectos

**3a. Categoría automática corregible.** Agregar `🏷️ Cambiar categoría` al teclado que
hoy se llama `_build_edit_only_keyboard()`. Copy de la línea:

> 🧠 Categoría automática: {categoría}

No tocar la confirmación posterior a una elección manual: ya explica correctamente que
aprende el concepto.

**3b. Explicar el vínculo fijo antes de aceptar.** Una coincidencia:

> 💡 ¿Es el pago de tu fijo {fijo}? Si lo vinculás, cuenta para ese mes y usa la categoría del fijo.

Varias coincidencias:

> 💡 Coincide con varios fijos. Si lo vinculás, cuenta para ese mes y usa la categoría del fijo. ¿Cuál es?

**3c. Hacer recuperable el auto-save de cambios.** Recomendación: pedir confirmación para
todo cambio de moneda, usando el preview que ya existe. Si se conserva auto-save, el mínimo
es sumar `↩️ Deshacer` y documentarlo:

> Si el cambio es claro, se registra solo; si no, te pido confirmar.

**3d. Cortar el fallback peligroso de cambios.** Ante una señal de conversión que no pudo
interpretarse:

> ⚠️ No pude interpretar el cambio. No guardé nada. Probá con: vendí 500 USD a 1700 ARS.

Este punto es flujo, no sólo copy: la rama no debe continuar al parser de gastos.

### Lote 4 — Fallos coherentes y permisos familiares

**4a. Reutilizar el fallback manual ante caída conversacional.**

> ⚠️ No pude interpretar el mensaje. Probá de nuevo o cargá el gasto así: Supermercado 15000.

**4b. Responder excepciones no manejadas.**

> ⚠️ Algo falló y no pude completar eso. Probá de nuevo en un rato.

**4c. Evitar el falso CTA de `/sincat`.** Reemplazar el pie por:

> Para corregir uno tuyo: /editar ID categoria NOMBRE
> Para reasignar ese concepto en la familia: /recat CONCEPTO CATEGORÍA

Y sumar a `/ayuda` la regla ya propuesta:

> Podés editar sólo tus gastos; las consultas ven a toda la familia.

---

## 4. Priorización

El orden de implementación recomendado es:

1. **Lote 1**, porque corrige los callejones del primer minuto.
2. **Lote 3d**, porque hoy un fallo de IA puede producir datos incorrectos sin aviso.
3. **Lote 2 [BLOQUEA CAPACIDADES]**, porque OCR, ingresos y lista de compras quedan enteros
   fuera del producto mental que construye `/ayuda`.
4. **Lotes 3a–3c**, porque hacen visibles y reversibles decisiones automáticas.
5. **Lote 4**, para cerrar fallos residuales y la asimetría de miembros.

Los mensajes de cupo, chat sin vincular, grupo, OCR fallido, audio fallido y monto inválido
en flujos determinísticos ya dicen qué ocurrió y qué hacer. No se propone agregarles texto.
