# Changelog

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