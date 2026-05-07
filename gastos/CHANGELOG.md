# Changelog

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