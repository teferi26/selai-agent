# Realtor AI — 100 mejoras para ser el SaaS inmobiliario más útil del mundo

Tags:
- **Q** = quick win (poco esfuerzo, mucho valor — para los primeros sprints).
- **D** = diferenciador (pocos o ningún competidor lo tiene — defensible).
- **C** = compliance crítico (no opcional para vender en EU).

---

## A. Captación multi-canal (no perder ni un lead)

1. **WhatsApp Business Cloud API** con respuesta < 30 s, 24/7. (Q)
2. **Email entrante** (Resend para envío + Postmark/SES inbound webhook) con threading correcto y firma profesional. (Q)
3. **Voz entrante** (Vapi o LiveKit + Deepgram + Cartesia) en ES/EN/FR, latencia < 800 ms.
4. **Voz saliente proactiva**: el agente IA llama a leads dormidos en horario civilizado, identificándose como IA. (D)
5. **Llamada perdida → mensaje WhatsApp automático** ("Te vimos llamar, ¿qué necesitas?"). (Q, D)
6. **SMS bidireccional** como fallback cuando WhatsApp no entrega.
7. **Instagram DM + Facebook Messenger** (Meta Conversations API). (Q)
8. **TikTok lead forms** y respuestas vía DM cuando lo abran al público.
9. **Widget de chat web embebible** con SDK ligero, instalable en 2 minutos.
10. **QR en escaparate físico / folletos** que abren WhatsApp con `ref=` (atribución). (Q, D)
11. **Click-to-WhatsApp ads** (Meta) preconectados, con plantillas de bienvenida que cualifican.
12. **Ingesta automática de leads de Idealista/Fotocasa/Habitaclia** vía email/API y respuesta inmediata. (D)
13. **NFC en placas físicas** de pisos en venta — el cliente acerca el móvil y empieza la conversación.
14. **Captura de leads desde Google Business Profile** (mensajes y reseñas).
15. **Detección de leads duplicados entre canales** (mismo teléfono entra por WA y email = un solo lead). (Q)

---

## B. Cualificación: el setter más listo del mercado

16. **Detección automática de idioma** del lead y respuesta en su idioma desde el primer mensaje. (Q)
17. **Análisis de sentimiento en tiempo real** (frustrado, ilusionado, indeciso) → adapta tono.
18. **Lead scoring 0–100** con desglose (presupuesto, urgencia, encaje, comportamiento). Visible al agente humano. (D)
19. **Predicción de probabilidad de cierre** (modelo entrenado con histórico de la agencia). (D)
20. **Detección de timewasters** (preguntas vagas sin avance) → desescala suave en 2-3 mensajes.
21. **Detección de "agente competencia espiando"** (preguntas anómalas, IP del competidor) → respuesta neutra. (D)
22. **Cualificación financiera**: presupuesto, ahorros, ingresos, financiación pre-aprobada — preguntado con tacto.
23. **Detección de tipología de cliente**: inversor, primera vivienda, familia que crece, mudanza laboral, expat. Cada uno necesita un pitch distinto. (D)
24. **Detección de urgencia** ("me caso en 3 meses", "embarazada"): bumpea prioridad al agente humano.
25. **Memoria persistente del lead entre conversaciones**: nunca pregunta dos veces lo mismo. (Q, D)
26. **Cualificación implícita por foto/video** que mande el lead (mascota, niños, accesibilidad).
27. **Verificación ligera de identidad** opt-in (DNI/NIE) para leads serios → reduce no-shows.
28. **Histórico cross-channel**: si el lead entró por web hace 6 meses, lo recuerda al volver por WhatsApp. (D)
29. **Detección de banderas rojas legales**: cliente sin papeles para alquiler, etc. → escala humano sin discriminar.
30. **Onboarding del lead conversacional**: 4-5 preguntas en 2 minutos en vez de un formulario que abandona.

---

## C. Catálogo y matching de propiedades

31. **Búsqueda semántica con embeddings** (Voyage o Cohere) sobre descripción + barrio + features. Pgvector. (Q)
32. **Matching inverso**: cuando se da de alta un piso nuevo, notifica automáticamente a los leads que lo buscaban. (D)
33. **Listas de espera por criterios** ("avísame si baja un 3 hab en Chamberí < 600 K").
34. **Recomendación tipo Spotify**: "porque viste el piso de Malasaña, igual te gusta este de Universidad".
35. **Tour virtual integrado** (Matterport / Kuula / 360º) servido directo en chat.
36. **Generación automática de ficha PDF personalizada** ("para Juan, 2 hab, parking imprescindible"). (D)
37. **"Por qué te encajaría"** generado por IA por cada piso enviado — más conversión que pegar la URL.
38. **Comparador IA** entre 2-3 pisos del catálogo cuando el cliente duda.
39. **Calculadora de hipoteca embebida** con tipos reales (BdE / Idealista hipotecas).
40. **Calculadora de gastos totales** (ITP/IVA, notario, registro, gestoría, comunidad) por CCAA. (Q)
41. **Mapa POI personalizado**: para cada lead muestra colegios, metro, super, gimnasio según sus prioridades.
42. **Estimación de revalorización** (datos catastrales + tendencias del barrio). (D)
43. **Análisis de barrio**: ruido, demografía, evolución de precios, tipología vecinal.
44. **Disponibilidad real-time sincronizada** con el CRM interno y portales — nunca enseñar un piso ya vendido. (Q)
45. **Alertas de bajada de precio** automáticas a leads que mostraron interés. (Q)
46. **Detección de duplicados en el catálogo** (mismo piso subido dos veces con datos distintos).
47. **Auditoría automática de calidad de listing**: avisa al agente cuando faltan fotos, descripción pobre, EPC...

---

## D. Visitas: maximizar conversión a oferta

48. **Google/Outlook Calendar sync bidireccional**. (Q)
49. **Routing geográfico**: asigna al agente humano más cercano al piso de la visita. (D)
50. **Optimización de ruta diaria** del agente (varias visitas en orden óptimo). (D)
51. **Recordatorios automáticos** 24h y 1h antes por WhatsApp con confirmación in-line.
52. **Reagendamiento conversacional** ("muévela al viernes 17h") sin tocar la web.
53. **Briefing pre-visita IA** al agente humano: qué busca, qué dijo, objeciones, presupuesto, leads similares. (D)
54. **Captura post-visita al cliente** por WhatsApp ("¿qué te pareció?") en menos de 2 horas.
55. **Captura post-visita al agente** vía nota de voz → transcrita y estructurada. (Q, D)
56. **Detección de no-show** → seguimiento inmediato y reasignación de slot al siguiente lead.
57. **Visitas virtuales asistidas en vivo** (videollamada con agente recorriendo el piso) para clientes lejanos. (D)
58. **Co-visitas** (varios leads en el mismo piso, mismo día) cuando aplica para no quemar tiempo del agente.
59. **Visitas en días no laborables** gestionadas por la IA + agente de guardia.

---

## E. Cierre, nurturing y reactivación

60. **Drip campaigns personalizados** según etapa del lead, sin marketing genérico de spam.
61. **Reactivación de leads dormidos** > 30/90/180 días con mensaje personalizado a cada uno. (D)
62. **Cross-sell compra ↔ alquiler** cuando el lead no encaja en su operación inicial.
63. **Negociación asistida**: la IA sugiere al humano qué decir cuando el cliente regatea. (D)
64. **Biblioteca de objeciones + respuestas** que mejor convirtieron históricamente, ranqueada por éxito.
65. **Generación de ofertas formales en PDF** firmable con click (FirmaFy / Signaturit). (D)
66. **Tracking de apertura** de la oferta enviada (igual que Mailchimp) — el agente sabe cuándo el cliente la mira.
67. **Predicción "cliente a punto de irse a la competencia"** → alerta urgente al humano. (D)
68. **Win/loss analysis automático** tras cerrar/perder un lead → aprendizaje para futuros prompts.
69. **Solicitud automática de reseña Google** al cierre — sube el ranking local. (Q)
70. **Programa de referidos automatizado**: a los 30 días post-cierre la IA pregunta si conoce a alguien.

---

## F. Productividad del agente humano

71. **Bandeja unificada** (WhatsApp + email + voz + web chat) con priorización por temperatura. (Q)
72. **Modo "asistente en vivo"**: la IA sugiere respuestas mientras el humano teclea. (D)
73. **Asignación automática** por experto en zona/tipología/idioma.
74. **Transferencia "warm" a humano** con resumen del contexto + última intención del lead. (Q)
75. **Plantillas con relleno automático** ("hola {nombre}, sobre el piso de {barrio}...").
76. **Notas de voz del agente → estructuradas** automáticamente en el lead. (Q, D)
77. **Búsqueda full-text** en histórico de conversaciones de la agencia.
78. **@menciones a compañeros** dentro de un hilo, con handoff de propiedad.
79. **Modo vacaciones / fuera de horario**: la IA toma todo y deja resumen al volver.
80. **App móvil del agente** (Expo/React Native) con bandeja, leads, agenda y notas de voz. (D)

---

## G. Inteligencia de negocio para el dueño

81. **Dashboard de KPIs** con tasa de respuesta, conversión por etapa, coste por lead, % cierre. (Q)
82. **Funnel visual** lead → cualificado → visita → oferta → cierre por canal y agente.
83. **Atribución multi-touch**: qué canal y qué campaña realmente convirtió. (D)
84. **Recomendador de captación**: "estos leads buscan X y no tienes inventario — capta más así". (D)
85. **Heatmap de demanda por barrio** generado de las búsquedas reales de los leads. (D)
86. **Análisis de objeciones recurrentes** ("el 40% pide parking y no lo tienes en cartera").
87. **Coste por lead y por cierre** incluyendo coste de IA + portales + ads.
88. **ROI por agente humano** y por canal.
89. **A/B testing de prompts y mensajes** con significancia estadística. (D)
90. **Forecast de ventas** 30/60/90 días basado en pipeline + temperatura.
91. **Benchmarking anónimo entre agencias del SaaS**: "tu tasa de respuesta está en el percentil 60". (D)

---

## H. Compliance, seguridad y confianza (no negociable en EU)

92. **GDPR completo**: consentimiento granular, derecho a olvido, export de datos en 1 clic. (C)
93. **Disclaimer "soy IA"** automático si el cliente pregunta — honestidad por defecto. (C)
94. **Audit log inmutable** de cada decisión del agente y cada acceso de humano. (C)
95. **Cifrado AES-256 en reposo + TLS 1.3 en tránsito**. Secrets en KMS. (C)
96. **Detección de datos sensibles** (DNI, cuenta, salud) → enmascarado en logs. (C)
97. **Política de retención configurable** por agencia (ej. borrar conversaciones de leads perdidos a los 12 meses). (C)
98. **Modo "human-in-the-loop obligatorio"** para acciones críticas (ofertas, cancelaciones, cambios de precio). (C)
99. **SOC 2 Type II + ISO 27001** roadmap (necesario para vender a grupos grandes).
100. **DPA y contrato de encargado del tratamiento** firmable digitalmente con cada agencia. (C)

---

## Bonus: 10 features "wow" que nadie en España tiene hoy

101. **Voice cloning del top performer** de la agencia para llamadas IA con su voz, con consentimiento. (D)
102. **Staging virtual** automático: mostrar el piso vacío "amueblado" con IA generativa.
103. **Generación automática de descripción de piso** desde 5 fotos con multimodal. (Q, D)
104. **Detección de calidad de fotos** del listing → sugerencia de re-fotografía profesional.
105. **Marketplace inter-agencias**: cuando una agencia no puede atender un lead (zona, tipo) lo cede a otra del SaaS por comisión. (D)
106. **Integración con bancos** (Idealista Hipotecas / Trioteca / Hipoo): pre-aprobación de hipoteca en chat. (D)
107. **Integración con notarías y gestorías**: agendar firma con un click cuando llega el cierre.
108. **White-label opcional** ("Agente IA de Inmobiliaria Pérez") para agencias premium. (D)
109. **Modo guiri**: detecta cliente extranjero comprando en España → activa flujo especializado (NIE, traducción de documentos, banco para no residentes). (D)
110. **Modelo entrenado por agencia** con su histórico — el agente IA habla "como" la agencia, no genérico. (D)

---

## Sugerencia de orden por impacto / esfuerzo

**Sprint 1 (semanas 1–4) — pilotable**: 1, 2, 16, 31, 44, 48, 51, 53, 71, 74, 81, 92.

**Sprint 2 (semanas 5–8) — diferenciación inicial**: 18, 25, 36, 37, 39, 40, 45, 55, 65, 69, 75, 82.

**Sprint 3 (semanas 9–12) — voz y multi-canal real**: 3, 4, 5, 6, 9, 12, 14, 56.

**Sprint 4+ (mes 4 en adelante) — defensa competitiva**: 4, 19, 21, 23, 32, 47, 49, 50, 53, 67, 80, 89, 101–110.
