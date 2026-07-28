# Runbook operativo

## Verificación de la versión final del roadmap (7.5.1)

El roadmap multitenant quedó completo hasta la Fase 8. La versión `7.5.0`
incorporó la consola operativa de superadmin y `7.5.1`, versión final del
roadmap, corrigió el recorte visual del popup de administración del avatar.

Después de desplegar esta versión:

1. Confirmar que `/health` y `/health/db` respondan `200`.
2. Confirmar que la base esté en el head de Alembic `0008`.
3. Ingresar como el superadmin configurado y verificar que `/superadmin`
   muestre adopción y actividad, telemetría LLM de 30 días, overrides de cuota,
   supuestos manuales de infraestructura y errores recientes persistidos.
4. Ingresar como un admin normal de familia y confirmar que `/superadmin`
   responda `403`.
5. Confirmar que el popup del avatar se muestre por encima del contenido y
   contenga las acciones de administración, sistema, exportación y cierre de
   sesión que correspondan al usuario.
6. Confirmar que borrar un override restaure los defaults configurados:
   `100` llamadas rutinarias por día y `15` generaciones de resumen por mes.
   El límite de concurrencia sigue siendo de dos llamadas LLM por familia.
7. Confirmar que `family_quota_overrides` y `system_errors` tengan RLS forzado,
   y que el rol dedicado `gastos_superadmin` sea el único camino operativo con
   `BYPASSRLS`.

La Fase 8 no agregó variables de entorno obligatorias. Los costos de
infraestructura cargados en la consola son supuestos manuales; los costos LLM
provienen de la telemetría medida en `llm_calls`. La consola no incluye
impersonación, billing ni edición transversal de datos de negocio familiares.

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
