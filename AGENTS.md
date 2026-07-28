# AGENTS.md

Reglas de proceso para cualquier agente de IA (Claude Code u otro) que vaya a modificar código en este repositorio. Estas reglas se suman a las de [CLAUDE.md](CLAUDE.md) — CLAUDE.md documenta *qué es y cómo está armado* el proyecto; este archivo documenta *cómo trabajar sobre él*.

No aplican cuando el trabajo es puramente de planeamiento/discusión (no se va a tocar código todavía). Aplican siempre que el agente vaya a **crear o modificar un archivo de código o de configuración** del repo.

---

## 1. Antes de tocar código: sync + branch

Antes de escribir el primer cambio de un pedido nuevo:

1. **Mirar dónde estás parado.** `git status` y `git branch --show-current`. Si hay cambios sin commitear que no son tuyos de esta sesión (trabajo en curso del usuario), no los toques ni los descartes — preguntá antes de hacer nada que pueda pisarlos.
2. **Sincronizar `main` local con `origin/main`.** `git fetch origin main:main` (falla solo, y de forma segura, si `main` local tiene commits propios que `origin/main` no tiene — en ese caso avisar, no forzar).
3. **Decidir si hace falta una branch nueva:**
   - Si ya estás en una branch de feature/fix creada para este mismo pedido, seguí ahí.
   - Si estás en `main`, o en una branch de un pedido distinto (anterior, ya mergeado, o de otro tema), creá una branch nueva **desde el `main` recién sincronizado**: `git checkout -b <tipo>/<slug-corto> main`.
   - Prefijos en uso en este repo: `feat/`, `fix/`, `docs/`, `chore/`, `infra/` (históricamente también aparece `feature/` — usar los cortos de acá en adelante).
4. **Nunca mergear vos mismo.** Push a la branch cuando el usuario lo pida explícitamente; el merge a `main` (o la PR) lo maneja el usuario a mano, salvo que pida explícitamente lo contrario en esa conversación puntual.

Esto es una repetición del mismo hábito en cada pedido de código — no hay que preguntar si corresponde hacerlo, se hace directo como primer paso (después de entender el pedido; ver "Antes de empezar cualquier tarea" en CLAUDE.md sobre aclarar dudas primero si algo es ambiguo).

---

## 2. Después de tocar código: qué documentación actualizar

Ningún cambio de código se da por terminado sin revisar si alguno de estos documentos quedó desactualizado. La tabla de la sección 3 dice qué documento cubre qué información — usarla para saber cuáles tocan según el tipo de cambio. Reglas generales:

- **Bump de versión + changelog:** todo cambio deployable (no aplica a docs-only o tooling interno) bumpea `gastos/config.yaml` (`version`) y agrega una entrada a `gastos/CHANGELOG.md`, como ya indica CLAUDE.md → Versioning. Sigue siendo la señal más simple de "¿esto necesita que actualice algo más?" — si amerita un bump, probablemente amerita revisar PROJECT.md y/o DOCS.md también.
- **Si cambiaste comportamiento visible por el usuario** (comandos de Telegram, pantallas del dashboard, mensajes del bot): revisar `gastos/DOCS.md`.
- **Si cambiaste arquitectura, schema de DB, variables de entorno, módulos o su responsabilidad:** revisar `CLAUDE.md` y `PROJECT.md`.
- **Si cambiaste el flujo de deploy, CI, o variables de entorno del lado de infraestructura:** revisar `README.md` y `PROJECT.md`.
- **Si el cambio es puramente interno** (refactor sin cambio de comportamiento ni de arquitectura visible): alcanza con el changelog; no hace falta tocar el resto.

Ante la duda de si algo quedó desactualizado, mejor pecar de revisar de más — un doc viejo genera más fricción a futuro (para el usuario o para el próximo agente) que el minuto que toma chequearlo.

---

## 3. Mapa de documentación

| Archivo | Contenido | Audiencia | Cuándo tocarlo |
|---|---|---|---|
| [CLAUDE.md](CLAUDE.md) | Guía técnica para agentes de IA: qué es la app, setup local, responsabilidad de cada módulo (`bot.py`, `intent.py`, `db.py`, etc.), schema de DB, config por env vars, proceso de deploy y versionado, convenciones clave. | Agentes de IA (Claude Code la carga automáticamente como contexto de proyecto). | Cambios de arquitectura, nuevo módulo, cambio de responsabilidad de un módulo existente, nueva convención o gotcha no obvia. |
| [AGENTS.md](AGENTS.md) (este archivo) | Proceso de trabajo: ritual de sync/branch antes de codear, y qué documentación actualizar después. No describe el producto. | Cualquier agente de IA. | Cuando cambie el proceso de trabajo en sí (no cuando cambie el producto). |
| [PROJECT.md](PROJECT.md) | Estado actual y canónico del proyecto: versión, infraestructura del Pi, arquitectura, stack, schema de DB completo, variables de entorno, **gotchas conocidos**. Es el doc más detallado y el que se mantiene más al día. | Desarrolladores/agentes que necesitan el estado real del sistema. | Cualquier cambio de arquitectura, schema, infraestructura, o un gotcha nuevo que valga la pena dejar anotado. Ante discrepancia con Blueprint.md, **PROJECT.md manda**. |
| [Blueprint.md](Blueprint.md) | **Deprecado** — versión anterior del mismo contenido que PROJECT.md (arquitectura, schema, endpoints), en español y con más detalle de código. Quedó desactualizado (todavía describe la tabla `fixed_expense_payments`, eliminada en la migración 2.0.0, y no cubre el sistema de reportes IA de 2.3.0). | — | No se actualiza más. No hace falta tocarlo al hacer cambios de código; si en algún momento se decide reemplazarlo o borrarlo del todo, es una decisión aparte, no algo a hacer de paso. |
| [README.md](README.md) | Cara pública del repo en GitHub: qué es la app, cómo hacer un cambio y deployarlo (push a `main` → CI → pull manual en el Pi), variables de entorno, dónde vive la data persistente. En inglés. | Cualquiera que entre al repo desde GitHub. | Cambios al flujo de deploy/CI, a las variables de entorno, o a dónde/cómo se persiste la data. |
| [gastos/DOCS.md](gastos/DOCS.md) | Manual de uso para los usuarios finales de la app (la familia): cómo cargar un gasto, modo conversacional, voz, dólares, lista de comandos de Telegram, pantallas del dashboard. En español. | Usuarios finales del bot/dashboard (no desarrolladores). | Cualquier cambio a comandos, a cómo se interpreta un mensaje (texto/voz/NL), o a las pantallas del dashboard. |
| [gastos/CHANGELOG.md](gastos/CHANGELOG.md) | Historial de versiones, una entrada por versión bumpeada en `config.yaml`, en español, con el detalle de qué cambió y por qué. | Cualquiera reconstruyendo la historia del proyecto. | Todo cambio deployable, junto con el bump de versión (ver CLAUDE.md → Versioning). |
| [gastos/config.yaml](gastos/config.yaml) | Fuente canónica del número de versión de la app (`version`), más metadata legacy de Home Assistant add-on (arch, puertos, schema de opciones). | Build/versionado. | Al bumpear versión. |

**Nota aparte — `Telegram.md`:** existe un archivo en la raíz con ese nombre que contiene un token vivo de bot de Telegram en texto plano. No está trackeado por git (confirmado con `git ls-files`), así que no es parte de este mapa de documentación ni se sube al repo — es un archivo de scratch local del usuario. No editarlo ni commitearlo como si fuera doc del proyecto.

---

## 4. Credenciales de GitHub dentro del sandbox

El keyring del host no siempre es visible desde el sandbox. Si `gh auth status`,
`git push` u otro comando de GitHub informa credenciales ausentes, vencidas o
inválidas dentro del sandbox, **ese resultado no es concluyente**: repetir el
comando con permisos escalados antes de pedirle al usuario que vuelva a iniciar
sesión. Sólo solicitar `gh auth login` si la comprobación fuera del sandbox
también falla.
