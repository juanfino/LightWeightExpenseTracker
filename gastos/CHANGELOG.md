# Changelog

## 5.0.1
- El acceso con Google muestra siempre el selector de cuenta, para evitar reutilizar silenciosamente una sesión de Google cuando hay varias cuentas abiertas en el dispositivo.

## 5.0.0
- **Fase 3 multi-tenant (implementación):** autenticación web propia con sesiones opacas server-side, cookies seguras, protección CSRF y resolución usuario → familia en un único `before_request`.
- Login y registro mediante Google OAuth o código de seis dígitos por email (Resend); los códigos vencen a los 10 minutos, son de un solo uso, admiten hasta cinco intentos y se guardan hasheados.
- Registro crea usuario, familia y taxonomía inicial; Cloudflare Turnstile y límites por IP/email protegen los endpoints que envían correo o crean cuentas.
- Landing pública, política de privacidad, términos, menú de usuario y cierre de sesión; todas las rutas privadas requieren autenticación y la suite las audita automáticamente.
- El despliegue inicial se verificó detrás de Cloudflare Access; después de probar la autenticación propia en producción se eliminó únicamente la aplicación Access `expenses`, conservando Cloudflare Tunnel y Turnstile.

## 4.0.0
- **Fase 2 multi-tenant:** familias, membresías y aislamiento por PostgreSQL Row-Level Security; los datos existentes se asignan a “Familia Finochietto”.
- Todas las tablas de dominio llevan `family_id`; claves compuestas bloquean referencias entre familias y el SQL generado por IA queda sujeto al mismo RLS.
- `seed.py` crea una taxonomía argentina genérica por familia, sin copiar keywords aprendidos del hogar original.
- Las llamadas Anthropic/OpenAI registran módulo, modelo, uso, costo estimado, latencia, resultado y error en `llm_calls`.
- Nueva suite PostgreSQL de aislamiento con dos familias, SQL hostil, writes cruzados, telemetría y RLS forzado.
- Se corrige la serialización de timestamps PostgreSQL en Dashboard y Movimientos; vuelven a mostrarse como fecha/hora argentina en lugar de `undefined`.

## 3.0.0
- **Fase 1 multi-tenant:** PostgreSQL 17 reemplaza SQLite como único motor. Alembic administra el schema y el migrador SQLite → PostgreSQL verifica conteos y SHA-256 tabla por tabla.
- Bot y dashboard usan pools separados de conexiones; el bot admite updates concurrentes y las consultas SQL generadas por IA se ejecutan con rol de solo lectura y timeout.
- Los backups pasan a ser dumps PostgreSQL comprimidos, subidos diariamente al bucket privado de Cloudflare R2 y verificados después de la carga. R2 conserva 90 días.
- Se elimina la restauración web desde una URL pública. La restauración es una operación administrativa por SSH, documentada y probada de punta a punta en `docs/RUNBOOK.md`.
- Se agrega CI con PostgreSQL real, tests unitarios y smoke tests del esquema, SQL de solo lectura y rutas web.

## 2.6.0
- **Fase 0 del plan de migración multi-tenant** (`docs/MULTITENANT_PLAN.md`): re-sincronización de `PROJECT.md` con el repo real, nuevo `docs/SQL_INVENTORY.md` (cada SQL crudo del código, archivo → función → tablas → read/write), y documentación precisa de la feature de Resúmenes (payload exacto de las dos llamadas a Opus, costo estimado por generación).
- **Seguridad:** el backup diario (21:00 ART) y el botón "Backup ahora" ya no envían `gastos.db` por Telegram a los usuarios configurados — ahora guardan una copia local con timestamp en `<carpeta de la DB>/backups/`, con una retención de 7 días. El envío automático era un riesgo de fuga de datos apenas la app deje de tener una sola familia. El backup real fuera del dispositivo (con restore probado) queda para la Fase 1 del plan.

## 2.5.2
- En el Dashboard, el total en la moneda secundaria ya no lleva el prefijo "También:", queda solo el monto.

## 2.5.1
- En el Dashboard, el total en la moneda secundaria (ej. "También: U\$S...") ahora se ve mucho más grande y destacado en color de acento, en vez de compartir el tamaño chico del texto de tendencia.
- El menú "Historial" pasa a llamarse "Movimientos" para no confundirse con "Fijos".

## 2.5.0
- El modal "Agregar gasto" de Historial incorpora un selector de subcategoría dependiente de la categoría elegida.
- Los selectores de categoría y subcategoría permiten crear una opción nueva en el momento, sin salir del gasto ni perder los datos ya cargados; la opción creada queda seleccionada automáticamente.
- El backend valida que la subcategoría pertenezca a la categoría seleccionada y evita nombres duplicados aunque cambien mayúsculas o acentos.
- Se corrige el error 500 al agregar gastos cuando había gastos fijos configurados: el filtro por moneda trataba filas SQLite como diccionarios al buscar una sugerencia de vínculo. El gasto llegaba a insertarse antes del error, por lo que conviene revisar posibles duplicados de los intentos fallidos previos al fix.

## 2.4.2
- En Historial, la columna "Moneda" desaparece y el monto se muestra combinado con su prefijo (`$100` / `U$S 100`), igual que en Gastos Fijos. La fila de edición inline mantiene el mismo comportamiento (moneda no editable si el gasto está vinculado a un fijo), ahora en una sola celda junto al monto.

## 2.4.1
- En desktop, los campos de fecha ahora abren el datepicker nativo al hacer click en cualquier parte del input (antes había que acertarle al ícono del calendario); el fix es un único listener delegado en `base.html` que cubre los 4 campos de fecha del sitio, incluida la fila de edición inline del Historial.
- El modal "Registrar pago" de un gasto fijo ahora muestra la moneda (`$`/`U$S`) como prefijo del campo "Monto pagado", para que quede claro en qué moneda se está cargando el pago sin poder editarla ahí.
- En "Agregar gasto" (Historial), Moneda y Monto pasan a compartir una misma línea, con el select de Moneda angosto a la izquierda.

## 2.4.0
- Gastos y gastos fijos ahora guardan su moneda nativa (`ARS` o `USD`); la migración automática conserva todos los registros históricos como ARS. No hay cotización automática ni se suman monedas distintas.
- Telegram mantiene ARS por defecto y reconoce `USD`, `US$`, `U$S` o “dólares” en texto, voz y lenguaje natural. La moneda se puede corregir con el teclado inline o `/editar ID moneda USD`; OCR empieza en ARS y permite pasarlo a USD antes de confirmar.
- Dashboard: selector ARS/USD para gráficos y totales, total secundario de la otra moneda, filtro y edición de moneda en Historial, y selector de moneda para altas manuales y gastos fijos.
- Los pagos de gastos fijos heredan obligatoriamente la moneda del fijo; la detección, candidatos y vínculo sólo consideran la misma moneda. No se puede cambiar la moneda de un gasto vinculado ni de un fijo con pagos vinculados.
- El resumen mensual y el IPC continúan analizando ARS; los gastos USD quedan en una sección nativa separada sin conversión. Prompts SQL/NL y fingerprint incorporan moneda.

## 2.3.1
- Se corrigen tres problemas de layout en mobile detectados probando la app en un iPhone real: (1) cualquier `input`/`select` con menos de 16px de `font-size` dispara el zoom automático de iOS Safari al enfocarlo, dejando la página con scroll horizontal hasta hacer zoom-out a mano — se sube a 16px en el breakpoint mobile (`base.html`); (2) entre ~640px y ~1024px de ancho (tablets en vertical, celulares grandes en horizontal) la barra de navegación superior no entraba completa pero tampoco bajaba a menú hamburguesa, dejando "Categorías" y "Sistema" directamente inaccesibles (clippeados por `overflow-x: hidden` sin scroll ni wrap) — se sube el breakpoint del hamburguesa de 640px a 1024px; (3) en la tarjeta "Fijos del mes" del dashboard, el nombre de un gasto fijo no pagado desaparecía en mobile porque el monto estimado + los botones "+ Registrar"/"✓ Ya pagué" no dejaban ancho disponible para el `<span>` del concepto — se replica el mismo patrón de wrap que ya usaba `/fijos` (monto y botones bajan a su propia línea en mobile)

## 2.3.0
- **Resumen mensual generado por IA:** nueva pantalla `/resumenes` con un análisis retrospectivo del mes. Toda la aritmética la hace código (nuevo módulo `dossier.py`): total, promedio diario, desglose por categoría, contrastes nominales y reales (mes anterior, promedio 3/6 meses, mismo mes año anterior — cada uno presente o ausente según haya historia suficiente), atribución del delta por categoría, gastos atípicos (con y sin ellos en el total), estado de gastos fijos (incluye cuáles quedaron sin vincular este período), dólares (ambos lados siempre, cobertura de gasto cubierta vendiendo dólares) y "quién registró" (aclarado como uso de la app, no como reparto de gastos)
- El modelo (`claude-opus-4-8` por defecto, configurable con `REPORT_ANTHROPIC_MODEL`, separado del Haiku que usan OCR/voz/lenguaje natural) hace dos llamadas acotadas sobre esos números ya calculados: una clasifica cada gasto variable del mes como recurrente o excepcional (evidencia empírica de recurrencia + clasificaciones de meses previos para consistencia), y la otra redacta el titular, resumen, hallazgos (siempre con una cifra concreta, sin recomendaciones genéricas — no hay presupuestos en la app) y preguntas accionables que enlazan directo a Historial o Fijos con el filtro del período ya aplicado
- Nuevo módulo `inflation.py`: cachea el IPC Nacional (INDEC, vía la API de datos.gob.ar) para deflactar los contrastes. Como INDEC publica un mes con casi un mes de atraso, el mes más reciente se estima proyectando el promedio de los últimos 3 meses publicados — nunca más de un mes hacia adelante — y se pisa con el valor real en cuanto se publica. Si la API no responde, el resumen se genera igual mostrando solo cifras nominales y lo aclara explícitamente
- Los resúmenes son de solo-agregado (nunca se pisan): cada generación o regeneración inserta una fila nueva; la última es la que se muestra pero el historial completo queda auditable, incluidas las clasificaciones por gasto. Cada resumen guarda además una huella (hash) de los hechos locales del período (gastos y operaciones de dólar, sin valores derivados como promedios) — no se usa todavía, pero queda calculada para el badge de "puede haber cambiado" de una próxima entrega
- Si la llamada de clasificación o de análisis falla, el resumen igual se genera y se guarda con las secciones fijas (los números) completas; solo falta la narrativa, y la pantalla lo indica en vez de mostrar una página vacía
- `/fijos` ahora acepta `?year=&month=` para que un enlace externo (como el de una pregunta del resumen) abra directamente el período que corresponde, en vez de siempre el mes actual

## 2.2.0
- Historial: se agregan filtros de búsqueda por concepto (substring, sin distinguir acentos/mayúsculas — reutiliza `categorizer.normalize`), subcategoría (acotada a la categoría elegida, o todas si no hay ninguna seleccionada), estado de gasto fijo (todos/solo fijos/solo variables) y usuario. Este último ya estaba soportado por el backend (`/api/expenses?user_id=`) pero nunca se había expuesto en la UI — `PROJECT.md` decía que sí, era documentación desactualizada
- Mes y año pasan a aceptar "Todos" como opción explícita — antes mes/año por defecto quedaban fijos en el mes actual, así que una búsqueda de texto que existía en otro mes devolvía cero resultados sin ninguna pista de por qué. Ahora los filtros activos se muestran como chips removibles junto al contador de resultados, para que quede visible qué está acotando la lista y se pueda sacar una restricción a la vez
- Todos los filtros se aplican al toque (sin botón "Filtrar" — el buscador de texto usa debounce de 300ms) y quedan reflejados en la URL, así el botón atrás y los bookmarks respetan el estado de los filtros
- Backend: `db.get_expenses_by_month` se reemplaza por `db.get_expenses_filtered(year, month)` con ambos parámetros opcionales; nuevo `db.get_expense_years()` para poblar el selector de año solo con años que tienen datos

## 2.1.0
- La fecha de un gasto ahora es editable en todos los lugares donde ya se editan los demás campos: fila del Historial en el dashboard (selector de fecha nativo), `/editar ID fecha DD/MM/AAAA` en Telegram, y edición en lenguaje natural (`"el gasto 124 fue el 15 de junio"`), reutilizando el mismo mecanismo (`update_expense`/`update_expense_fields`, `date_str` → se guarda como 03:00 UTC = medianoche ART) que ya usaban la carga manual y el registro por lenguaje natural. Cambiar solo la fecha no toca el vínculo a gasto fijo ni su período — son campos independientes a propósito, porque el período de un gasto fijo sirve para otra cosa (ver más abajo)
- Se corrige que registrar el pago de un gasto fijo de un período pasado (botón "+ Registrar pago" o "✓ Ya lo pagué" sin candidatos, navegando a un mes distinto del actual en `/fijos`) creara el gasto con la fecha de hoy en vez de una fecha dentro del período que se estaba viendo. El modal de pago ahora pide la fecha (por defecto: hoy si el período es el mes actual, el día 1 si es un período pasado — no se inventa más precisión de la que se tiene), y el selector queda acotado a los días de ese mes; el backend valida lo mismo del lado del servidor
- Se verificó que el período del gasto fijo (`fixed_expense_year`/`month`) en ese mismo flujo ya se tomaba del período que se estaba viendo (no de la fecha del gasto recién creado) — no había un segundo bug ahí, se deja documentado en `PROJECT.md`

## 2.0.0
- **Breaking cambio de modelo de datos:** los pagos de gastos fijos dejan de vivir en una tabla separada (`fixed_expense_payments`) y pasan a ser un atributo del gasto mismo (`expenses.fixed_expense_id` + período). Un gasto fijo pagado dos veces en el mismo mes (ej. un error de facturación de un servicio) ahora se representa naturalmente como dos gastos vinculados, en vez de estar limitado a un pago por mes
- Migración automática e idempotente al iniciar: los pagos con un gasto vinculado se convierten al nuevo modelo conservando el año/mes original del pago; los pagos sin gasto vinculado (creados por el viejo botón "✓ Ya lo pagué", que no guardaba monto) se descartan — no había plata que migrar y no se inventa un gasto a partir del monto estimado. Los conteos convertidos/descartados quedan en el log de arranque
- La detección de gastos fijos ya no depende de cómo se cargó el gasto: antes solo el camino rápido de texto plano la disparaba (y lo hacía *antes* de guardar, bloqueando el guardado con una confirmación); ahora **todo** camino de creación (texto plano, lenguaje natural, voz, OCR, alta manual desde el dashboard) guarda el gasto normalmente y después ofrece vincularlo si coincide con un gasto fijo activo (nuevo módulo compartido `fixed_matcher.py`). Nunca vincula solo — siempre pregunta
- "✓ Ya lo pagué" pasa de ser un simple flag (que dejaba el mes en $0 porque no guardaba monto) a una búsqueda de candidatos entre los gastos ya cargados y sin vincular ese mes (por concepto, categoría y cercanía al monto estimado); si ninguno coincide, se puede seguir cargando el monto directamente
- Se corrige que un mismo gasto fijo terminara en distinta categoría según el camino de carga (el camino rápido de texto usaba la categoría que adivinaba el categorizador por keyword, en vez de la propia del gasto fijo). Ahora vincular un gasto a un gasto fijo siempre fuerza su categoría/subcategoría a la del gasto fijo (`db.link_expense_to_fixed`), sea cual sea el camino
- El vínculo a un gasto fijo (y su período) ahora es un campo más del gasto, editable donde ya se editan los demás (fila del Historial en el dashboard, teclado inline de Telegram, y edición en lenguaje natural con `"poné que esto es el pago de <gasto fijo>"` / `"ninguno"` para desvincular)
- Dashboard: nuevo selector "Gasto fijo" en la fila de edición del Historial (fuerza categoría/subcategoría al vincular, igual que el bot); alta manual de un gasto ofrece vincular si el concepto coincide con un gasto fijo; "Ya lo pagué" en Fijos muestra los mismos candidatos que el bot en vez de solo marcar como pagado
- `/fijos` y el panel "Estado del mes" ahora muestran el total pagado sumando todos los pagos del período (no solo el primero), y mantienen una acción secundaria "+ Otro pago" incluso sobre gastos ya pagados, para el caso legítimo de un segundo pago en el mismo mes

## 1.17.0
- Rediseño visual (identidad ámbar) extendido a Dólares, Categorías y Sistema, completando las 6 pantallas
- Dólares: tira de stats reemplaza las 3 tarjetas sueltas; el tipo de cambio (Venta/Compra) se muestra como texto coloreado en vez de badge; colores de los gráficos alineados a la paleta nueva
- Categorías: se agrega título de página; el resto de la funcionalidad (alta/edición/borrado de categorías, subcategorías y keywords) queda sin cambios
- Sistema: tarjetas sin borde con radio más grande, se quitan los emojis de los encabezados, la tarjeta de backup separa el estado/botón de la descripción con un divisor, y el botón "Restaurar" pasa a un rojo sólido distintivo para remarcar que es una acción destructiva

## 1.16.0
- Rediseño visual completo del dashboard web: nueva identidad ámbar/naranja (antes violeta), tipografía Plus Jakarta Sans, tarjetas sin borde con radios más grandes y layout más espaciado, aplicado a las 6 pantallas (Dashboard, Historial, Fijos, Dólares, Categorías, Sistema) en modo claro y oscuro
- Dashboard: el total del mes pasa a ser el elemento principal del encabezado (con la variación vs. mes anterior como badge con flecha), y las 4 tarjetas KPI se reemplazan por una tira compacta (Gastos / Promedio diario / Top del mes); se elimina el gráfico chico de tendencia (sparkline) de cada tarjeta
- Dashboard, Historial y Fijos: las categorías y usuarios ahora se identifican con un punto de color (paleta nueva, consistente entre gráficos, listas y tablas) en lugar de íconos/emoji
- Fijos: se agrega una barra de progreso visual al listado de "Estado del mes" (antes solo texto)
- Los gráficos (Chart.js) leen los colores desde las variables CSS del tema, así se mantienen sincronizados entre modo claro/oscuro sin duplicar la paleta en JS
- Se mantiene sin cambios toda la funcionalidad existente: comparación con el mes anterior en "Por semana", vista Anual, filtro por usuario, modal de registrar pago y el menú mobile

## 1.15.0
- Dashboard: la tarjeta "Categoría top" (arriba a la derecha) se reemplazó por "Top 3 del mes", una mini-lista con los 3 gastos individuales más grandes del mes (concepto + monto). Aporta info nueva en vez de repetir lo que ya muestra el gráfico "Por categoría"
- Dashboard: nuevo gráfico "Últimos 6 meses" (total mensual, mes actual resaltado) debajo de "Por semana", rellenando el espacio que quedaba vacío. Reutiliza los datos de las sparklines (queda anclado a los últimos 6 meses reales, igual que las tendencias)
- Dashboard: el gráfico "Por semana" ahora superpone una línea punteada con el total de cada semana del mes anterior, como referencia, manteniendo el desglose por usuario en las barras. Las semanas se alinean por número de semana del mes; "Sem 5" es una semana parcial (días 29–fin), así que su comparación es más ruidosa
- Historial: al editar un gasto, los selects de categoría/subcategoría y los inputs ahora ocupan el ancho de su columna en vez de forzar un ancho fijo, evitando el scroll horizontal que dejaba el botón "Cancelar" fuera de vista

## 1.14.0
- Bot: la capa de intención en lenguaje natural ahora tiene memoria conversacional de corto plazo — una ventana deslizante de hasta 5 minutos o los últimos 10 mensajes (lo que ocurra primero), por chat. Permite resolver preguntas de seguimiento como "dame el desglose de esos gastos" o "sí, por persona" sin tener que repetir la consulta completa. Pasado ese límite (o si el mensaje depende de algo dicho antes), el modelo aclara que no tiene memoria en vez de inventar una respuesta
- La memoria vive solo en proceso (se pierde si el bot reinicia, igual que el resto de los estados `pending_*`) y no se persiste en la base de datos

## 1.13.0
- Bot: nueva capa de intención en lenguaje natural. Además del formato clásico `concepto monto`, ahora se le puede hablar al bot de forma conversacional y entiende cuatro tipos de intención: registrar gastos ("anotame 100 lucas en el súper"), editar gastos existentes ("che, me equivoqué, el último gasto fueron 90000"; "el gasto 124: total 40000 y categoría nafta"), administrar la taxonomía ("agregá la categoría Niños"; "en Casa agregá la subcategoría Productos de limpieza") y responder consultas de solo lectura ("cuánto gasté esta semana"; "cuánto gastó Cele en comida en marzo")
- Bot: ruteo híbrido — el parser determinista sigue siendo el camino rápido instantáneo para el simple `concepto monto`; solo las frases con señales de intención (ediciones, taxonomía, consultas, slang como "lucas") escalan al modelo, vía tool use / function calling de Claude (nuevo módulo `intent.py`, primer uso de function calling del proyecto)
- Bot: el logueo conversacional se auto-guarda (con teclado de editar/categoría, nada irreversible); las ediciones y la creación de categorías/subcategorías siempre piden confirmación con botones inline. Las ediciones con varios candidatos muestran un selector
- Seguridad: las consultas/reportes se responden con SQL generado por el modelo pero ejecutado bajo guardrails estrictos (nuevo módulo `sqlro.py`): solo `SELECT`/`WITH`, una sola sentencia, conexión de solo lectura (`mode=ro`) y timeout de statement. Vía Telegram un usuario solo puede editar sus propios gastos (el SQL de targeting filtra por usuario y se re-chequea antes del UPDATE); el dashboard web queda sin cambios. La creación de categorías/subcategorías está protegida contra duplicados sin acentos/mayúsculas
- Fix: la capa de intención se activaba de más — el heurístico de ruteo incluía "gasté"/"gastó" para detectar preguntas de reportes, pero esas palabras también aparecen en frases normales de logueo ("gasté 5000 en nafta"), haciendo que gastos comunes salten el camino rápido determinista sin necesidad. Se sacó ese disparador (las preguntas de reporte del spec ya quedan cubiertas por "cuánto"/"categoría"/"trimestre")
- Bot: la capa de intención en lenguaje natural ahora también se activa por voz — antes un mensaje de voz con una pregunta o edición ("¿cuánto gasté en nafta?") caía en el extractor de gastos y respondía "no pude detectar ningún monto"
- Fix: la descarga del archivo de audio en `handle_voice` no tenía manejo de errores; con mala señal, un timeout de Telegram quedaba sin capturar (no hay error handler global) y el mensaje "Procesando audio..." quedaba colgado para siempre sin avisar al usuario. Ahora se captura y se pide reenviar el audio

## 1.12.2
- Fix: un gasto con un salto de línea en el concepto (ej. generado por una extracción de voz) rompía el atributo `onclick` del botón "Borrar" en el Historial, dejándolo inerte sin ningún error visible para el usuario. Ahora se escapan correctamente saltos de línea y backslashes, y `db.py` normaliza espacios/saltos de línea al crear o editar un gasto para que no vuelva a ocurrir.

## 1.12.1
- Fix: voice message processing could hang the entire bot for several minutes with no user feedback (missing timeout + blocking call on event loop). Added 15s timeout, disabled SDK auto-retry, and added a retry button so failures are fast and recoverable without re-recording.

## 1.12.0
- Dashboard mobile: nuevo menú tipo drawer que se desliza desde la derecha con fondo oscurecido (scrim) y botón de cierre, en reemplazo del dropdown de ancho completo. El botón hamburguesa ahora queda anclado a la derecha del topbar
- Dashboard mobile: las tablas de Historial, Dólares (historial de cambios), Categorías, Keywords y Fijos (administrar) se muestran como tarjetas apiladas con etiquetas por columna, eliminando el scroll horizontal (nueva clase reutilizable `.rtable` en `base.html`)
- Fijos mobile: en "Estado del mes" el monto y los botones de pago bajan a su propia línea de ancho completo, evitando que se superpongan con el concepto
- Dashboard mobile: menos padding lateral en topbar, contenedor y cards para ganar ancho útil en pantallas chicas

## 1.11.0
- Dashboard: paleta de colores distinta por usuario — cada usuario recibe un color bien diferenciado de `USER_COLOR_PALETTE` en `_sync_users`, para que se distingan claro en el gráfico "Por semana" y en los tags de gastos (antes todos compartían el mismo violeta por defecto)
- Bot: gastos por voz de alta confianza se registran automáticamente sin pedir confirmación — `audio.py` ahora devuelve un `confidence` (0–1) por gasto y solo se piden confirmar los dudosos (umbral `AUTOSAVE_CONFIDENCE = 0.9`)
- Audio: `transcribe_and_extract` se dividió en `transcribe` (Whisper) y `extract_expenses` (Claude) para permitir rutear audios de dólar antes de extraer gastos
- Bot: operaciones de dólar en lenguaje natural por texto y por voz (ej: "vendí 500 dólares a 1700", "compré 1000 dólares a 1550 cada uno") — nuevo módulo `dolar.py` interpreta tipo, monto y cotización con Claude; alta confianza registra directo, baja confianza pide confirmación inline
- DB/Dashboard: la tabla `cambios_dolar` ahora tiene columna `tipo` (venta/compra) con migración; el historial de `/dolares` muestra el tipo y permite editarlo. El comando legacy `CambioDolar <usd> <cotizacion>` sigue funcionando (registra venta)

## 1.10.1
- Config: puerto del dashboard movido de 5000 a 8090 para liberar el puerto para Frigate; configurable vía env var `DASHBOARD_PORT`

## 1.10.0
- Bot: mensajes de voz ahora soportan múltiples gastos en un solo audio (ej: "mil en la verdulería, tres mil en la ferretería y quinientos en nafta")
- Audio: `transcribe_and_extract` retorna lista de gastos `[{concept, amount}]`; Claude extrae todos los gastos mencionados en la transcripción
- Bot: flujo de confirmación por cola — cada gasto válido se muestra de a uno con botones inline "✅ Sí, guardar" / "❌ Cancelar"; gastos sin monto detectable son avisados y salteados automáticamente
- Bot: helper `_send_next_voice_confirmation` gestiona la cola y muestra contador "Gasto X de Y" cuando hay más de uno

## 1.9.1
- Bot: confirmación de gastos de voz cambiada a botones inline ("✅ Sí, guardar" / "❌ Cancelar"), igual que el flujo OCR; se elimina el handler de texto `/si` / `/no` para voz

## 1.9.0
- Bot: soporte de mensajes de voz — el usuario puede enviar un audio describiendo un gasto (ej: "ferretería diez mil pesos") y el bot lo transcribe con Whisper y extrae concepto y monto con Claude
- Audio: nuevo módulo `audio.py` — transcripción con OpenAI Whisper (`whisper-1`, idioma `es`) y extracción estructurada con `claude-haiku-4-5-20251001`
- Bot: flujo de confirmación para gastos de voz con `pending_voice`, análogo al flujo OCR con `/si` / `/no`
- Config: nueva variable de entorno opcional `OPENAI_API_KEY` — habilita el procesamiento de audio; si ausente, el bot responde con aviso y registra warning

## 1.8.3
- Fix: los handlers de `pending_fixed_direct` y `pending_amount_edit` en `bot.py` ahora intentan extraer el monto via `parse_message()` cuando `_normalize_amount()` falla — evita "Monto inválido" si el usuario escribe `"Doméstica 35000"` mientras hay un estado pendiente activo

## 1.8.2
- Parser: agregados tests para mensajes con conceptos acentuados (`Doméstica 35000`) y separador de miles argentino (`35.000`); el parser ya manejaba estos casos correctamente

## 1.8.1
- Infra: DNS explícito (`8.8.8.8`, `1.1.1.1`) en el servicio Docker para evitar que un `resolv.conf` desactualizado rompa la conectividad del bot silenciosamente
- Infra: healthcheck que verifica conectividad saliente a `api.telegram.org` cada 2 minutos; el contenedor pasa a `unhealthy` si falla 3 veces consecutivas

## 1.8.0
- Bot: selección de subcategoría después de asignar categoría manualmente — si la categoría elegida tiene subcategorías, se muestra un teclado inline con las opciones + botón "Sin subcategoría"
- Bot: al elegir una subcategoría, se actualizan tanto el gasto como el binding de la keyword con `subcategory_id`
- Bot: si el usuario envía un nuevo gasto mientras hay una selección de subcategoría pendiente, el flujo pendiente se cancela silenciosamente y el nuevo mensaje se procesa normalmente
- DB: `add_keyword()` acepta parámetro opcional `subcategory_id` y lo incluye en el upsert

## 1.7.5
- Seed: nueva función `seed_keyword_subcategories(conn)` — migración idempotente que asigna `subcategory_id` a keywords existentes donde la subcategoría puede inferirse del keyword; solo actualiza filas con `subcategory_id IS NULL`; llamada desde `seed()` en cada arranque
- Bot: confirmación de gasto ahora muestra `{icono} {categoría} › {subcategoría}` cuando el gasto tiene subcategoría asignada; aplica a todos los flujos (texto, OCR texto, OCR botón inline, gasto fijo confirmado, gasto fijo normal)

## 1.7.4
- Fix: `POST /api/fixed-expenses/pay` usaba día 15 hardcodeado al crear el gasto; ahora usa la fecha real en zona horaria Argentina

## 1.7.3
- Dashboard: nuevo endpoint `POST /api/subcategories/add` — crea una subcategoría para la categoría dada
- Dashboard: nuevo endpoint `POST /api/subcategories/delete` — elimina subcategoría con guard (bloquea si hay gastos asociados)
- Dashboard: nuevo endpoint `PUT /api/keywords/<id>` — actualiza keyword, categoría y subcategoría de una keyword existente
- DB: nuevas funciones `get_expense_count_by_subcategory()` y `update_keyword()`
- Configuración — pestaña Categorías: panel expandible de subcategorías por categoría con botones agregar/eliminar inline (sin recarga)
- Configuración — pestaña Keywords: nueva columna Subcategoría; botón Editar abre fila inline con campo keyword, dropdown categoría y dropdown subcategoría (se repopula al cambiar categoría); guarda via PUT sin recarga

## 1.7.2
- Dashboard: `POST /api/expenses/update` acepta `subcategory_id` opcional y lo pasa a `update_expense()`
- Dashboard: nuevo endpoint `GET /api/subcategories?category_id=X` — retorna subcategorías de la categoría dada; sin parámetro retorna todas con `category_id`
- Dashboard: nuevo endpoint `POST /api/expenses/<id>/subcategory` — asigna o limpia `subcategory_id` de un gasto
- Historial: nueva columna "Subcategoría" entre Categoría y Monto; oculta en mobile (`hide-mobile`)
- Historial: badge de subcategoría con estilo outlined/muted; celda vacía si no hay subcategoría
- Historial: modal de edición inline agrega dropdown "Subcategoría (opcional)"; se repopula al cambiar categoría vía `GET /api/subcategories?category_id=X`; se guarda junto al gasto

## 1.7.1
- `categorizer.categorize()` ahora retorna `(category_id, subcategory_id)` en lugar de solo `category_id`; keywords de la DB incluyen `subcategory_id`
- `db.create_expense()` y `db.create_expense_full()` aceptan parámetro opcional `subcategory_id=None` e incluyen el campo en el INSERT
- Bot: todos los flujos de creación de gasto (texto, OCR por texto, OCR por botón inline, gasto fijo confirmado, gasto fijo normal) pasan `subcategory_id` al guardar
- Bot: `pending_fixed_match` incluye `subcategory_id` para que se conserve hasta la confirmación del usuario

## 1.7.0
- DB: nueva tabla `subcategories` (id, category_id, name) con FK a categories en CASCADE
- DB: columna `subcategory_id` agregada a `expenses` y `keywords`; migraciones automáticas para DBs existentes
- DB: nuevas funciones `get_subcategories()`, `get_all_subcategories()`, `get_subcategory_by_id()`, `add_subcategory()`, `delete_subcategory()`, `update_expense_subcategory()`, `update_keyword_subcategory()`
- DB: `get_recent_expenses()`, `get_expenses_by_month()`, `get_expense_by_id()`, `get_all_keywords()` incluyen `subcategory_id` y `subcategory_name` en el resultado
- DB: `update_expense()` acepta parámetro opcional `subcategory_id`
- Seed: reescrito con función idempotente `seed(conn)`; nuevas categorías padre (Hogar, Hijos, Gastos Generales, Trabajo) con subcategorías
- Seed: migración de datos — reasigna gastos y keywords de categorías antiguas (Alimentación, Educación, Ropa, etc.) a la nueva jerarquía con subcategoría correspondiente

## 1.6.5
- Dólares: historial de cambios con cabeceras ordenables (Fecha, Usuario, USD, Cotización, ARS obtenidos); orden por defecto Fecha DESC
- Dólares: botón eliminar (ícono papelera) con modal de confirmación; elimina la fila sin recargar la página (`DELETE /api/cambios/<id>`)
- Dólares: botón editar (ícono lápiz) abre modal con campos fecha, monto USD y cotización; ARS se recalcula en tiempo real; guarda sin recargar (`PUT /api/cambios/<id>`)
- DB: nuevas funciones `delete_cambio()` y `update_cambio()`

## 1.6.4
- Dólares: nuevo gráfico de barras "USD cambiado por mes" (últimos 6 meses) a ancho completo, entre las tarjetas de resumen y los gráficos existentes; barras en azul (`#38bdf8`) para diferenciarlo visualmente del gráfico de ARS (verde)

## 1.6.3
- Fix: comando `CambioDolar` ahora tiene su propio MessageHandler con filtro `Regex` registrado antes del handler genérico de gastos, evitando que mensajes como `CambioDolar 1000 1400` sean interceptados y den error de monto inválido

## 1.6.2
- **Dólares**: nueva sección para registrar operaciones de cambio de divisas
- Nueva tabla `cambios_dolar` en la DB con campos `fecha`, `monto_usd`, `cotizacion`, `monto_ars`, `usuario`
- Bot: nuevo comando `CambioDolar <monto_usd> <cotizacion>` (case-insensitive); soporta formato argentino (`1.000`, `1.400,50`); responde con confirmación formateada
- Dashboard: nueva página `/dolares` con 3 tarjetas de resumen del mes, gráfico de línea (evolución de la cotización), gráfico de barras (ARS por mes) y tabla de historial de las últimas 50 operaciones
- Dashboard: 4 nuevos endpoints — `GET /api/cambios/resumen`, `GET /api/cambios/historial`, `GET /api/cambios/por_mes`, `GET /api/cambios/cotizacion_historica`
- Navegación: "Dólares" agregado entre "Fijos" y "Categorías" en el topbar

## 1.6.1
- Gastos Fijos: botón "Registrar pago" reemplazado por dos acciones distintas — "**+ Registrar pago**" (abre modal, crea gasto y marca como pagado) y "**✓ Ya lo pagué**" (marca como pagado sin crear gasto ni abrir modal)
- Nuevo endpoint `POST /api/fixed-expenses/mark-paid` y función `db.create_fixed_payment_without_expense()`
- `expense_id` en `fixed_expense_payments` ahora es nullable; migración automática para DBs existentes

## 1.6.0
- **Gastos Fijos**: nueva funcionalidad completa para registrar y hacer seguimiento de gastos recurrentes
- Dos tablas nuevas en la DB: `fixed_expenses` y `fixed_expense_payments` (con UNIQUE por fijo+mes)
- Bot: al registrar un gasto, detecta automáticamente si coincide con un gasto fijo (matching por palabras ≥3 chars); ofrece registrarlo como fijo o normal con botones inline
- Bot: comando `/fijos` muestra el estado del mes con ✅/⬜ por ítem y botones "Registrar pago" para los pendientes
- Dashboard: nueva página `/fijos` con dos secciones — estado del mes con selector de período y tabla de administración (agregar, editar, desactivar)
- Dashboard: widget "Fijos del mes" en el dashboard principal con barra de progreso, lista de items y botón de pago rápido
- Dashboard: 6 endpoints nuevos — `GET /api/fixed-expenses`, `GET /api/fixed-expenses/status`, `POST /api/fixed-expenses/add`, `POST /api/fixed-expenses/update`, `POST /api/fixed-expenses/deactivate`, `POST /api/fixed-expenses/pay`
- Navegación: "Fijos" agregado entre "Historial" y "Categorías" en el topbar
- Historial: cabeceras de columna ordenables (Fecha, Concepto, Categoría, Monto, Usuario); orden por defecto: Fecha descendente
- Configuración: cabeceras ordenables en tabla de categorías (Nombre, Gastos) y tabla de keywords (Keyword, Categoría)
- Historial: botón "Agregar gasto" abre un modal para crear gastos manualmente (concepto, monto, categoría, usuario, fecha)
- Backup diario cambiado de las 03:00 ART a las 21:00 ART (00:00 UTC)

## 1.5.4
- Pinned `anthropic==0.101.0` en requirements.txt
- Columna `color` (TEXT, default `#6366f1`) agregada a la tabla `users`; migración automática para DBs existentes via `ALTER TABLE`
- `/api/users` ahora expone el campo `color` de cada usuario
- Dashboard: colores de chips, avatar, tags de usuario y barras semanales ahora se leen dinámicamente desde la DB; eliminado el chequeo hardcodeado `isAltUser`/`isAlt`
- `/api/weekly` documentado como endpoint standalone (no expuesto en la UI)
- `get_monthly_totals` reemplazado por una única query SQL con `GROUP BY strftime('%Y-%m', ...)`
- `seed.py` migrado a `db.get_conn()` eliminando el `sqlite3.connect()` directo

## 1.5.1
- Nueva página "Sistema" (`/config`) en la navegación con sección de base de datos
- Tarjeta de backup: estado del último backup y botón "Backup ahora" con feedback visual
- Tarjeta de restauración: pegar URL pública HTTPS de un `gastos.db`, guarda `.bak` y reinicia la app automáticamente
- Endpoint `POST /admin/restore-db-url` reemplaza el anterior endpoint de upload de archivo
- Eliminado endpoint `POST /admin/restore-db` (upload de archivo)

## 1.5.0
- Backup automático diario de la DB a las 03:00 ART vía Telegram (se envía gastos.db como documento a todos los usuarios configurados)
- Endpoint `POST /admin/backup-now` para disparar el backup manualmente desde el dashboard
- Endpoint `POST /admin/restore-db` para restaurar la DB subiendo un archivo (guarda backup .bak antes de sobreescribir)
- Tarjeta de estado de backup en el dashboard: muestra "Último backup: hace X hs" y botón "Backup ahora"

## 1.4.1
- Fix: filtro "Sin categoría" en Historial ahora trae los gastos sin categoría asignada
- Favicon para el dashboard: ícono de ticket en color violeta
- OCR: confirmación de ticket con botones inline "✅ Sí, guardar / ❌ Cancelar" (ya no hay que escribir)
- OCR: al confirmar un ticket sin categoría, aparece el teclado para asignar categoría, igual que en carga manual

## 1.4.0
- Vista Anual en el dashboard: toggle "Mensual | Anual" junto al navegador de período
- Tarjetas KPI anuales: total del año, promedio mensual, mes más caro, categoría del año
- Gráfico de barras apiladas "Evolución mensual": una barra por mes, apilada por categoría, leyenda HTML debajo
- Gráfico de líneas "Tendencia por categoría": top 5 categorías por monto anual, sin leyenda propia
- Navegador de período cambia entre meses (vista mensual) y años (vista anual)
- Nuevo endpoint `/api/annual/<year>` con desglose mensual por categoría y usuario
- Función `formatARS`: valores abreviados para ejes de gráficos ($Xk / $X.XM)
- Mapa de colores de categorías (`CAT_COLORS`) compartido entre todos los gráficos

## 1.3.0
- Rediseño visual completo del dashboard: tipografía DM Sans, paleta de colores con soporte dark/light mode via `prefers-color-scheme`
- Topbar nueva: sticky, con navegación por pills, chips de filtro por usuario y avatar de iniciales
- Tarjetas KPI rediseñadas: tendencia vs mes anterior (↑/↓ %) y sparkline de los últimos 6 meses
- Leyenda de categorías: cuadrado de color, barra de progreso proporcional y monto alineado a la derecha
- Lista de gastos recientes: flex rows con ícono circular por categoría, tag de usuario y sin tabla
- Gráfico semanal ahora es stacked bar por usuario (Juampi vs Cele)
- Nuevos endpoints: `/api/users`, `/api/sparklines`; `/api/monthly` incluye desglose semanal por usuario

## 1.2.2
- Al editar el monto por botón inline, muestra tarjeta completa "Gasto actualizado" con concepto, monto nuevo, categoría y usuario en lugar del mensaje de confirmación simple

## 1.2.1
- Fix: al seleccionar categoría por botón inline, el mensaje ahora refleja el ícono y nombre de la categoría elegida en lugar de seguir mostrando "Sin categoría"

## 1.2.0
- Inline keyboards al registrar un gasto: botones de categoría paginados por frecuencia de uso cuando el gasto queda sin categoría
- Botón "✏️ Editar monto" siempre visible tras registrar un gasto
- Al seleccionar categoría por botón, aprende el concepto como keyword automáticamente
- /semana: nuevo layout con ícono de categoría, fecha dd/mm y #ID (igual que /hoy)

## 1.1.2
- Fix /hoy: comparaba date('now') UTC contra timestamps UTC, fallando cuando la fecha BA (UTC-3) difería de la UTC
- Fix /semana: usaba strftime('%W') que no es ISO week y calculaba inicio de semana en lunes; en Argentina la semana arranca el domingo
- /semana ahora filtra por rango domingo–sábado en hora Buenos Aires

## 1.1.1
- Fix: parseo de montos en formato argentino (punto como miles, coma como decimal)

## 1.1.0
- OCR de tickets via Claude Vision: mandá una foto por Telegram y el bot extrae comercio, monto y fecha automáticamente
- Flujo de confirmación antes de guardar: el bot muestra el resumen detectado y espera "sí" o "no"
- Nuevo módulo `ocr.py` con `extract_ticket_data()` usando claude-haiku-4-5-20251001
- Nueva opción `anthropic_api_key` en config del add-on
- Handler `handle_photo` acepta fotos comprimidas y documentos de imagen

## 1.0.10
- Fix: gráficos del dashboard se rompen al navegar entre meses
- Canvas nunca se elimina del DOM; empty-state usa show/hide en elemento separado

## 1.0.9
- Actualización de documentación

## 1.0.8
- Fix timezone: timestamps se muestran en America/Argentina/Buenos_Aires (UTC-3)
- _to_baires_str() en dashboard convierte created_at UTC → BA en todas las respuestas JSON
- create_expense() usa datetime.now(UTC) explícito en lugar de CURRENT_TIMESTAMP

## 1.0.7
- Edición inline de gastos en historial (concepto, monto, categoría)
- POST /api/expenses/update en dashboard
- db.update_expense() + e.category_id/e.user_id en queries get_recent/get_by_month
- Fix: api_keywords_add ahora maneja correctamente el retorno string de add_keyword

## 1.0.6
- /editar categoria aprende el concepto como keyword de la categoría elegida
- add_keyword usa INSERT ON CONFLICT DO UPDATE (upsert); retorna new/remapped/unchanged
- /add_keyword informa si el keyword fue agregado, remapeado o ya estaba asignado
- Fix settings.html: datos Jinja separados del bloque JS (elimina errores Pylance)

## 1.0.5
- /editar ahora mapea automáticamente el keyword a la categoría elegida
- keyword remapeado avisa la categoría anterior

## 1.0.2
- Comandos /editar, /recat, /sincat, /ayuda
- Gestión de categorías en web y /nueva_categoria por Telegram

## 1.0.1
- Fix: lectura de config desde options.json
- Fix: Dockerfile sin dependencia de bashio

## 1.0.0
- Versión inicial
- Bot Telegram con categorización automática
- Dashboard web con gráficos mensuales y semanales
