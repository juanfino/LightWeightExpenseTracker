# Runbook operativo

## Cutover de autenticación propia (Fase 3)

**Completado el 27 de julio de 2026 con la versión 5.0.1.** La autenticación
propia quedó expuesta en producción y la aplicación de Cloudflare Access
`expenses` fue eliminada después de verificar el cutover. El Cloudflare
Tunnel y Turnstile siguen activos: no deben eliminarse al operar Access.

Para una instalación nueva, Cloudflare Access debe permanecer activo durante
todo el despliegue inicial. Antes de levantar la imagen, completar en `~/.env`:

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
2. Crear Turnstile para `juampifinochietto.com` (cubre el hostname canónico `mangoteca.juampifinochietto.com`).
3. Publicar el consentimiento OAuth de Google con las URLs `/`, `/privacy`
   y `/terms`, y callback
   `https://mangoteca.juampifinochietto.com/auth/google/callback`.
4. Desplegar con Cloudflare Access todavía activo.
5. En incógnito y desde un teléfono, probar registro/login Google y email OTP,
   logout, expiración/reutilización del código y acceso a una ruta privada.
6. Confirmar en logs que Alembic quedó en `0003`.
7. Recién entonces desactivar Cloudflare Access.
8. Repetir desde una sesión anónima: `/` debe ser público, una API privada debe
   responder `401` y una página privada debe redirigir a `/login`.

### Resultado y particularidades del cutover de producción

- Resend verificó `juampifinochietto.com` por DNS y el remitente configurado
  quedó operativo.
- Google Auth Platform quedó como `External` e `In production`, con las URLs
  públicas `/`, `/privacy` y `/terms`, el callback documentado arriba y sólo
  los scopes básicos `openid`, `email` y `profile`.
- Google reutilizaba silenciosamente la cuenta ya abierta en el navegador.
  La versión 5.0.1 agregó `prompt=select_account`, por lo que ahora siempre
  muestra el selector de cuenta.
- En incógnito apareció correctamente Cloudflare Access durante la primera
  prueba: todavía era la capa de protección temporal. Una vez verificada la
  autenticación propia, se eliminó únicamente la aplicación Access `expenses`;
  no se modificaron DNS, Tunnel ni Turnstile.
- El único check de GitHub se llama `postgres`, pero agrupa toda la validación:
  tests unitarios/integración, smoke del schema PostgreSQL y smoke de rutas
  web. El primer run falló porque un test de registro dependía implícitamente
  de `TESTING=1` para saltear Turnstile; se aisló Turnstile con un mock en ese
  test, mientras su contrato `siteverify` continúa cubierto por un test
  específico. El cierre terminó con 22 tests, schema smoke y 33 rutas web en
  verde.

## Cutover de familia y superadmin (Fase 4)

**Completado el 27 de julio de 2026 con la versión 6.0.0.** Antes de recrear
el contenedor, `~/.env` debe incluir:

```text
SUPERADMIN_EMAIL
USERS_JSON
```

`SUPERADMIN_EMAIL` debe coincidir exactamente con el email ya asociado al
owner; para la familia migrada, debe usar el mismo valor que
`AUTH_BOOTSTRAP_EMAIL`. En `USERS_JSON`, el campo opcional `email` se agrega al
objeto Telegram histórico correspondiente. El arranque sólo completa emails
NULL y falla ante conflictos: no crea una segunda identidad ni sobrescribe un
email existente.

Despliegue:

1. Actualizar `SUPERADMIN_EMAIL` y los emails opcionales de `USERS_JSON`.
2. Descargar la imagen: `docker compose pull gastos`.
3. Recrear para releer `~/.env`:
   `docker compose up -d --force-recreate gastos`.
4. Confirmar `docker compose ps gastos`: debe quedar `healthy`, sin reinicios.
5. Confirmar Alembic `0004`, login del owner y del miembro migrado, y
   `/familia`.

Durante el primer cutover, `SUPERADMIN_EMAIL` tenía un valor distinto del
email persistido del owner. `_bootstrap_superadmin()` falló deliberadamente,
el contenedor entró en restart loop y Cloudflare mostró `502 Host Error`
porque nada escuchaba en el puerto 8090. Diagnóstico:

```bash
docker compose ps gastos
docker logs --tail 100 gastos
curl -I http://127.0.0.1:8090/
```

El log característico es
`SUPERADMIN_EMAIL no corresponde a un usuario existente`. Corregir la variable
para que coincida exactamente con `AUTH_BOOTSTRAP_EMAIL` y volver a recrear el
servicio. No hace falta modificar la base manualmente.

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
