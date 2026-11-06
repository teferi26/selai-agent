# Seguridad de selai

## Cómo comunicar una vulnerabilidad

No abras una incidencia pública con credenciales, datos personales, pasos de
explotación o capturas de clientes. Envía el informe a **hola@selai.es** con el
asunto `Seguridad — divulgación responsable` e incluye:

- superficie y versión afectadas;
- impacto observado o posible;
- pasos mínimos para reproducirlo con datos sintéticos;
- cualquier medida temporal que recomiendes.

Confirmaremos la recepción en un máximo de dos días laborables. La prioridad,
la frecuencia de las actualizaciones y la fecha de corrección dependerán del
impacto. No se pedirá probar el hallazgo sobre cuentas o datos de terceros.

## Versiones cubiertas

Se corrige la versión desplegada en producción y la rama `main`. Las ramas de
trabajo y versiones antiguas no reciben parches independientes.

## Alcance

Son especialmente relevantes el acceso entre agencias, autenticación y roles,
webhooks, cargas de archivos, exportación o borrado RGPD, facturación y cualquier
filtración de conversaciones, contactos o credenciales.
