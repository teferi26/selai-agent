---
slug: dpa
version: v1
effective_date: 2026-05-03
title: Acuerdo de Encargo de Tratamiento (DPA)
language: es
---

> **BORRADOR — pendiente de revisión por asesoría jurídica.** Hasta su aprobación legal definitiva, este documento se entrega como base operativa.

# Acuerdo de Encargo de Tratamiento de Datos (DPA)

**Versión:** v1 — efectiva desde 2026-05-03

De conformidad con el artículo 28 del Reglamento (UE) 2016/679 (RGPD) y la Ley Orgánica 3/2018 de Protección de Datos Personales y garantía de los derechos digitales (LOPDGDD), las partes suscriben el presente Acuerdo de Encargo de Tratamiento.

## 1. Partes

- **Responsable del tratamiento:** el Cliente que contrata el Servicio (en adelante, "el Responsable").
- **Encargado del tratamiento:** **{{COMPANY_NAME}}** ({{COMPANY_VAT}}), con domicilio en {{ADDRESS}} (en adelante, "el Encargado").

## 2. Objeto

Mediante este DPA el Responsable encarga al Encargado el tratamiento de los datos personales necesarios para la prestación del Servicio **selai**, consistente en un asistente conversacional de IA que atiende y cualifica leads de la actividad inmobiliaria del Responsable.

## 3. Categorías de interesados

- Leads (clientes potenciales) que se comunican con el Responsable a través de cualquier canal conectado a selai.
- Empleados / agentes del Responsable que utilizan la plataforma.

## 4. Categorías de datos personales

- Datos identificativos: nombre, apellidos, alias.
- Datos de contacto: email, teléfono, perfiles de mensajería.
- Datos de transacción: presupuesto declarado, zonas de interés, preferencias.
- Datos de comunicación: mensajes de texto, transcripciones de voz, notas de voz.
- Metadatos técnicos: dirección IP, agente de usuario, identificadores de canal.

No se tratan categorías especiales de datos (art. 9 RGPD) salvo que el lead las facilite voluntariamente.

## 5. Finalidades

El Encargado tratará los datos exclusivamente para:

- Atender, cualificar y agendar visitas con los leads del Responsable.
- Generar resúmenes y briefings que se entregan al Responsable.
- Mejorar el Servicio mediante datos anonimizados y agregados.
- Cumplir obligaciones legales aplicables al Encargado.

## 6. Duración

Este DPA tiene la duración del contrato principal de prestación del Servicio. A su finalización, el Encargado devolverá o suprimirá los datos personales conforme a la cláusula 12.

## 7. Obligaciones del Encargado

El Encargado se compromete a:

- Tratar los datos siguiendo instrucciones documentadas del Responsable.
- Garantizar que el personal autorizado se haya comprometido a respetar la confidencialidad.
- Implementar las medidas técnicas y organizativas del Anexo I.
- Asistir al Responsable en la atención de derechos de los interesados (acceso, rectificación, supresión, portabilidad, oposición, limitación).
- Notificar al Responsable las violaciones de seguridad sin dilación indebida (en plazo no superior a 48 horas desde su conocimiento).
- Asistir al Responsable en la realización de evaluaciones de impacto (DPIA) cuando proceda.

## 8. Subencargados (procesadores de terceros)

El Responsable autoriza con carácter general la subcontratación a los proveedores listados a continuación, comprometiéndose el Encargado a notificar la adición o sustitución de cualquier subencargado con un preaviso de 30 días para permitir oposición razonada.

| Proveedor | Función | Ubicación de datos |
|---|---|---|
| Anthropic | Modelos de IA (Claude) | EE.UU. (cláusulas tipo + DPA propio) |
| Meta WhatsApp Business API | Mensajería | UE (Irlanda) |
| Resend | Email saliente | EE.UU. (cláusulas tipo) |
| Vapi / Deepgram / ElevenLabs | Voz (STT/TTS) | EE.UU. (cláusulas tipo) |
| Twilio | SMS / telefonía | UE / EE.UU. |
| Google (Calendar API) | Sincronización agenda | UE / EE.UU. |
| Stripe | Facturación | UE / EE.UU. |
| Vercel | Hosting frontend | UE (Frankfurt) |
| Railway / Fly.io / Render | Hosting backend | UE (a elección) |

## 9. Transferencias internacionales

Cuando los subencargados procesen datos fuera del Espacio Económico Europeo, el Encargado garantiza la existencia de cláusulas contractuales tipo aprobadas por la Comisión Europea o de un mecanismo equivalente de garantía.

## 10. Auditoría

El Responsable podrá auditar el cumplimiento del presente DPA, con preaviso razonable, una vez por año natural y a su cargo, salvo en caso de incidente.

## 11. Confidencialidad

Las partes se obligan a guardar la confidencialidad de los datos personales tratados, incluso después de la finalización del Acuerdo.

## 12. Devolución y supresión

A la finalización del Servicio, el Encargado:

1. Devolverá al Responsable los datos personales en formato estructurado y de uso común (export GDPR).
2. Suprimirá los datos en sus sistemas en un plazo máximo de **30 días** desde la solicitud, salvo obligación legal de conservación.
3. Certificará por escrito la supresión cuando el Responsable lo solicite.

## 13. Responsabilidad

Cada parte responde de los daños causados por el incumplimiento de sus obligaciones bajo el RGPD, sin perjuicio del régimen de responsabilidad solidaria del art. 82.4 RGPD frente a los interesados.

## 14. Datos de contacto del DPO

- **Delegado de Protección de Datos del Encargado:** {{DPO_EMAIL}}

## Anexo I — Medidas técnicas y organizativas

- **Cifrado en tránsito:** TLS 1.2+ obligatorio para todas las comunicaciones.
- **Cifrado en reposo:** Postgres con cifrado a nivel de disco proporcionado por el proveedor de hosting.
- **Control de acceso:** autenticación multifactor para personal con acceso a producción; principio de mínimo privilegio.
- **Aislamiento multi-tenant:** todas las consultas filtradas por `agency_id`; tests automáticos verifican el aislamiento.
- **Redacción de PII en logs:** los registros del modelo (`agent_runs`) se redactan automáticamente antes de persistirse.
- **Guardrail de salida:** todas las respuestas del modelo pasan por un validador antes de enviarse al lead.
- **Backups:** copias diarias retenidas 30 días; restauración periódicamente probada.
- **Logging y trazabilidad:** auditoría inmutable de consentimientos en `consent_logs`.

---

*Acuerdo aceptado el [fecha], por [nombre del firmante] del Responsable, desde la dirección IP [ip].*
