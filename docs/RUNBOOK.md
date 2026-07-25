# Runbook operativo

## Backup PostgreSQL → Cloudflare R2

La aplicación ejecuta `pg_dump --format=custom` todos los días a las 21:00 ART,
sube el archivo a `daily/` en el bucket privado
`lightweight-expense-tracker-backups` y verifica su tamaño con `HeadObject`.
Cloudflare elimina automáticamente los objetos de más de 90 días.

Variables requeridas en `/home/juanfino/.env`:

```text
DATABASE_URL
R2_ENDPOINT
R2_BUCKET
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
```

La credencial R2 debe ser un Account API Token con permiso `Object Read &
Write` aplicado solamente a ese bucket. Nunca registrar sus valores en Git,
logs o documentación.

## Restauración verificada

Una restauración nunca se inicia directamente sobre producción:

1. Descargar el dump elegido desde el bucket privado.
2. Crear una base temporal vacía.
3. Ejecutar `pg_restore --exit-on-error --no-owner --no-acl`.
4. Comparar tablas y conteos críticos con la base origen.
5. Detener la aplicación antes de reemplazar producción y conservar un dump
   del estado anterior.
6. Restaurar, ejecutar Alembic, iniciar la aplicación y validar dashboard, bot
   y logs.

El 25 de julio de 2026 se verificó el circuito completo desde la Raspberry:
dump, upload, `HeadObject`, download y restore en `r2_restore_check`.

## Rotación de credenciales R2

Crear primero la credencial nueva, instalarla en la Raspberry y completar una
restauración de prueba. Recién entonces revocar la anterior. `Access Key ID`
tiene 32 caracteres y `Secret Access Key`, 64.
