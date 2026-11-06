# Credenciales tenant: despliegue y rotación

Las credenciales de WhatsApp, Twilio y Google Calendar guardadas en
`agency.settings`, y las claves que firman los webhooks salientes hacia el CRM
del cliente, usan sobres autenticados con este formato:

- `enc:v1:...`: formato legacy derivado de `SECRET_KEY`; solo lectura.
- `enc:v2:<key-id>:...`: formato actual, cifrado con el keyring tenant.

`SECRET_KEY` sigue firmando integraciones globales, pero no cifra escrituras
nuevas. No debe retirarse ni rotarse mientras quede algún sobre `enc:v1`.

## Configuración

Genera cada clave Fernet fuera del repositorio y guárdala en el gestor de
secretos del entorno:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Configura un identificador no secreto y el JSON del keyring:

```text
TENANT_CREDENTIAL_ACTIVE_KEY_ID=2026-09
TENANT_CREDENTIAL_KEYS={"2026-09":"<fernet-key>"}
TENANT_CREDENTIAL_WRITE_MODE=v2
TENANT_CREDENTIAL_PLAINTEXT_WRITE_ACK=false
```

Producción no arranca si falta la clave activa, el identificador es inválido,
la clave no es Fernet fuerte o el keyring repite material. El comando de datos
exige un keyring explícito incluso en desarrollo; nunca usa el fallback local.

## Primer despliegue

1. Añade el keyring al gestor de secretos sin cambiar aún `SECRET_KEY`. Para el
   primer rolling deploy configura temporalmente:

   ```text
   TENANT_CREDENTIAL_WRITE_MODE=plaintext
   TENANT_CREDENTIAL_PLAINTEXT_WRITE_ACK=true
   ```

2. Despliega esta versión en todas las API y workers. Durante esta fase los
   lectores ya aceptan plaintext, `enc:v1` y `enc:v2`, pero las escrituras
   siguen en plaintext para que cualquier proceso de la versión anterior pueda
   consumirlas.
3. Ejecuta Alembic normalmente. La revisión `20260831_0016` no modifica datos
   y `20260831_0017` solo amplía la columna de firma de webhooks; ambas funcionan
   con `alembic upgrade head --sql`.
4. Cuando todas las instancias ejecuten el lector nuevo, cambia a modo v2 y
   vuelve a desplegar. Las instancias con la configuración anterior ya saben
   leer v2, por lo que este segundo rolling deploy es compatible:

   ```text
   TENANT_CREDENTIAL_WRITE_MODE=v2
   TENANT_CREDENTIAL_PLAINTEXT_WRITE_ACK=false
   ```

5. Pausa temporalmente las pantallas de conexión/reconexión de canales y haz
   el preflight de solo lectura:

   ```powershell
   python -m app.scripts.rewrap_tenant_credentials
   ```

   Salida `pending=0` y código 0 significan que no hay trabajo. Código 1 solo
   indica que existen valores por recifrar. Código 2 indica clave/configuración
   inválida, valor ilegible o carrera y no realiza cambios.
6. Aplica el recifrado y repite el check:

   ```powershell
   python -m app.scripts.rewrap_tenant_credentials --apply
   python -m app.scripts.rewrap_tenant_credentials
   ```

El comando preflighta todos los secretos antes de escribir, actualiza solo la
clave concreta del JSON o la fila concreta de la suscripción y usa
compare-and-swap. Todas las actualizaciones se confirman juntas; una credencial
ilegible o cambiada concurrentemente revierte la transacción completa. No
imprime plaintext ni ciphertext.

## Rotación

1. Añade la clave nueva al keyring conservando todas las anteriores.
2. Cambia `TENANT_CREDENTIAL_ACTIVE_KEY_ID` al identificador nuevo.
3. Despliega el keyring ampliado en todas las API y workers.
4. Ejecuta el preflight, `--apply` y un segundo preflight hasta `pending=0`.
5. Mantén la clave anterior durante al menos un ciclo de despliegue y confirma
   que no quedan instancias antiguas ni jobs demorados.
6. Retira la clave anterior del keyring y vuelve a desplegar.

Si se pierde una clave anterior antes del recifrado, sus sobres fallan cerrados
y no existe recuperación criptográfica. Restaura la clave desde el gestor de
secretos antes de volver a ejecutar el comando.

## Rollback

El downgrade de Alembic no descifra datos. Mientras el keyring conserve claves
anteriores, esta versión puede leer todos los sobres. No reviertas a una versión
que desconozca `enc:v2`: interpretaría el ciphertext como credencial del
proveedor. Ante un rollback de aplicación, restaura primero una versión lectora
de v2 o detén los canales hasta completar la recuperación.
