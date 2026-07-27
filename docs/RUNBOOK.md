# Runbook operativo

## Cutover de autenticación propia (Fase 3)

Cloudflare Access debe permanecer activo durante todo el despliegue inicial.
Antes de levantar la imagen 5.0.0, completar en `~/.env`:

```text
AUTH_SECRET_KEY
AUTH_BOOTSTRAP_EMAIL
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
RESEND_API_KEY
RESEND_FROM_EMAIL
TURNSTILE_SECRET
```

`AUTH_SECRET_KEY` se genera con `openssl rand -hex 32`.
`AUTH_BOOTSTRAP_EMAIL` debe ser el email de Google/email OTP del owner actual
de la familia 1; el bootstrap sólo completa un email NULL y falla si luego se
intenta cambiar por variable de entorno.

Orden obligatorio:

1. Verificar el dominio/remitente de Resend mediante sus registros DNS.
2. Crear Turnstile para `expenses.juampifinochietto.com`.
3. Publicar el consentimiento OAuth de Google con las URLs `/`, `/privacy`
   y `/terms`, y callback
   `https://expenses.juampifinochietto.com/auth/google/callback`.
4. Desplegar con Cloudflare Access todavía activo.
5. En incógnito y desde un teléfono, probar registro/login Google y email OTP,
   logout, expiración/reutilización del código y acceso a una ruta privada.
6. Confirmar en logs que Alembic quedó en `0003`.
7. Recién entonces desactivar Cloudflare Access.
8. Repetir desde una sesión anónima: `/` debe ser público, una API privada debe
   responder `401` y una página privada debe redirigir a `/login`.

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
