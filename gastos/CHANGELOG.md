# Changelog

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