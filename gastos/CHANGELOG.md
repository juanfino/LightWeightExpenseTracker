# Changelog

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