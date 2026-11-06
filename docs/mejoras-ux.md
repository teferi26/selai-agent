# Backlog de mejoras UX — selai

Objetivo: que una inmobiliaria de 3 personas sin conocimientos técnicos entienda a la primera **cuánto paga, cuánto tarda y cómo se instala** — y que el cliente activo no encuentre fricción.

Formato: `N. [P0|P1|P2] [área] Descripción — por qué ayuda al cliente final`.
✅ = implementada (83 de 150 a 2026-07-25).

## Prioridad P0 — que un desconocido entienda y compre

1. [P0] [precios/confianza] ✅ Añadir "+ IVA · sin permanencia" bajo el precio de cada plan — nadie descubre el IVA en el checkout y se rompe la confianza.
2. [P0] [precios/confianza] ✅ Franja destacada sobre la parrilla de precios: "14 días gratis · Sin tarjeta · Sin coste de instalación · Cancela cuando quieras" — responde las 4 objeciones antes de que surjan.
3. [P0] [precios/confianza] ✅ Reescribir las features de cada plan en lenguaje llano (fuera "handoff", "routing", "voice cloning", "benchmarking") — un gerente de agencia no tiene por qué saber inglés técnico.
4. [P0] [precios/confianza] ✅ Línea de confianza bajo la parrilla: "Puesta en marcha guiada de ~20 minutos. Sin técnicos, sin obra, con tu número de siempre." — quita el miedo a "instalar un software".
5. [P0] [precios/confianza] ✅ Explicar el único extra posible (minutos de voz por encima del plan a 0,30 €/min) en una frase clara en vez de "overage" — la palabra "overage" es jerga y esconde el coste.
6. [P0] [landing/conversión] ✅ Sub-headline del hero con las 3 respuestas en una línea: "Desde 99 €/mes · listo en 20 min · pruébalo gratis 14 días sin tarjeta" — el visitante decide en 5 segundos si sigue leyendo.
7. [P0] [landing/conversión] ✅ CTA primario del hero "Empieza gratis" → /sign-up y secundario "Ver cómo se instala" → /instalacion — el camino de compra self-service pasa a ser el principal, no la demo.
8. [P0] [soporte/ayuda] ✅ FAQ "¿Cuánto cuesta de verdad? ¿Hay costes ocultos?" con cifras reales (99/199/399 + IVA, sin instalación, sin permanencia) — responde literalmente la pregunta que el visitante teclea en Google.
9. [P0] [soporte/ayuda] ✅ FAQ "¿Cuánto tardo en tenerlo funcionando?" (20 min guiados; WhatsApp 1-2 días por verificación de Meta) — fija expectativas honestas y evita bajas por sorpresa.
10. [P0] [soporte/ayuda] ✅ FAQ "¿Necesito tarjeta para probar?", "¿Puedo cancelar cuando quiera?", "¿Tengo que cambiar de número?" y "¿Necesito saber de informática?" — las 4 dudas que frenan el registro, respondidas con un no/sí rotundo.
11. [P0] [landing/conversión] ✅ Etiqueta de tiempo estimado en cada paso de SetupSteps y HowItWorks + total "~20 minutos" — "rápido" no convence; "2 min + 3 min + 2 min" sí.
12. [P0] [landing/conversión] ✅ Nota de coste "0 €" en cada paso de /instalacion — elimina la sospecha de costes de alta escondidos paso a paso.
13. [P0] [landing/conversión] ✅ Header con "Precios", "Cómo se instala" y CTA "Empieza gratis" — las 3 preguntas del fundador accesibles desde cualquier scroll.
14. [P0] [precios/confianza] ✅ Arreglar enlaces legales del footer que apuntaban a rutas inexistentes (/legal/...) → /terminos y /privacidad — un enlace legal roto en una web que pide confianza es letal.
15. [P0] [soporte/ayuda] ✅ Unificar nombres de planes en la FAQ (había restos de "Growth" y "Scale" que ya no existen) — dos nomenclaturas distintas = sensación de web descuidada.
16. [P0] [precios/confianza] ✅ Mostrar el precio anual desglosado al activar el toggle anual ("159 €/mes, facturados 1.908 €/año") — el cargo anual completo no debe ser una sorpresa en Stripe.
17. [P0] [precios/confianza] ✅ Tabla comparativa de planes lado a lado (filas = qué hace, columnas = plan) con lenguaje llano — el formato 3 tarjetas obliga a leer 24 bullets para comparar.
18. [P0] [precios/confianza] ✅ Añadir "¿Qué plan me toca?" con 2 preguntas (¿cuántos sois? ¿queréis que coja llamadas?) que recomienda plan — la parálisis de elección mata registros.
19. [P0] [landing/conversión] Vídeo de 60-90 segundos mostrando la instalación real de principio a fin — ver a alguien hacerlo en 20 min es la única prueba definitiva de que es fácil.
20. [P0] [landing/conversión] Demo interactiva sin registro: "Llama ahora a este número y habla con la IA de una inmobiliaria de ejemplo" — probar el producto en 30 segundos vende más que toda la landing.
21. [P0] [landing/conversión] Widget de chat en la propia landing atendido por selai — el producto se demuestra a sí mismo mientras responde dudas de precio.
22. [P0] [precios/confianza] ✅ Calculadora ROI precargada con valores típicos de una agencia de 3 personas y resultado en "ventas extra al año" — los números abstractos no convencen; "1 venta extra paga 2 años de selai" sí.
23. [P0] [landing/conversión] ✅ Añadir capturas reales del panel (bandeja, checklist de puesta en marcha) en la landing — enseñar la mercancía reduce el miedo a lo desconocido.
24. [P0] [seguridad percibida] ✅ Bloque visible "Tus datos en la UE · RGPD · Sin entrenar modelos con tus datos" con iconos, cerca del formulario de registro — la objeción legal aparece justo antes de dar el email.
25. [P0] [landing/conversión] Prueba social con cifras: "X agencias · Y llamadas atendidas · Z visitas agendadas este mes" — aunque sean modestas, cifras reales baten a testimonios genéricos.
26. [P0] [onboarding/wizard] ✅ Email de bienvenida inmediato tras el registro con los 7 pasos, su tiempo y el enlace directo al wizard — si el usuario cierra la pestaña, el email es el camino de vuelta.
27. [P0] [onboarding/wizard] ✅ Barra de progreso persistente del wizard ("Paso 3 de 7 · te quedan ~10 min") visible en todo el panel — saber cuánto queda es lo que hace que la gente termine.
28. [P0] [onboarding/wizard] ✅ Poder saltar cualquier paso del wizard y retomarlo después sin perder lo hecho — la rigidez de un wizard lineal expulsa a quien no tiene el CSV a mano.
29. [P0] [landing/conversión] ✅ Sello "Hecho en España · soporte en español" junto al CTA principal — para una agencia local, un proveedor español con soporte en su idioma es diferencial frente a herramientas americanas.
30. [P0] [SEO/contenido] ✅ Página /precios independiente e indexable con la parrilla completa y FAQ de precios — "selai precios" y "cuánto cuesta IA inmobiliaria" deben aterrizar en una URL directa.
31. [P0] [landing/conversión] Sección "Escucha una llamada real" con 2-3 audios de ejemplo (comprador, vendedor, alquiler) — oír la voz elimina la duda nº 1: "¿sonará a robot?".
32. [P0] [precios/confianza] ✅ Aclarar en la parrilla qué pasa al acabar la prueba: "Al día 14 eliges plan o tu cuenta se pausa. No se cobra nada solo" — el miedo al cargo automático frena registros sin tarjeta... y con tarjeta.
33. [P0] [onboarding/wizard] ✅ Detección de errores del CSV con mensajes en llano ("La fila 12 no tiene precio") y opción de arreglar en pantalla — el CSV es el paso donde más gente abandona.
34. [P0] [onboarding/wizard] ✅ Plantilla CSV con 3 propiedades de ejemplo ya rellenas — copiar un ejemplo es infinitamente más fácil que interpretar columnas vacías.
35. [P0] [landing/conversión] Test A/B del headline actual contra uno orientado a resultado ("No pierdas ni un comprador más por no coger el teléfono") — el headline es la palanca de conversión más barata.
36. [P0] [soporte/ayuda] Botón "¿Te ayudamos? Te llamamos en 5 min" en la página de precios en horario laboral — la duda de precios resuelta por teléfono cierra ventas que la web sola pierde.

## Prioridad P1 — fricción del usuario activo

37. [P1] [onboarding/wizard] ✅ Checklist de puesta en marcha con estado persistente (hecho/pendiente) y celebración al completarla — el refuerzo visual empuja a terminar la configuración.
38. [P1] [onboarding/wizard] Llamada de prueba guiada desde el propio panel ("Llama a tu IA ahora y pregúntale por el piso de ejemplo") — la primera experiencia de éxito debe ocurrir en los primeros 20 minutos.
39. [P1] [onboarding/wizard] Verificar el desvío de llamadas automáticamente y mostrar "✓ Desvío activo" en el panel — el usuario nunca sabe si marcó bien el código *21*.
40. [P1] [onboarding/wizard] ✅ Asistente de importación desde Idealista con instrucciones por CRM (Inmovilla, Witei, Excel) con capturas — cada CRM exporta distinto y el usuario no sabe cuál es su caso.
41. [P1] [onboarding/wizard] ✅ Estado visible de la activación de WhatsApp ("Verificación de Meta en curso · suele tardar 1-2 días") con aviso por email al completarse — la espera opaca genera tickets y ansiedad.
42. [P1] [dashboard diario] ✅ Resumen diario en la home del panel: "Hoy: 12 conversaciones, 3 leads calientes, 2 visitas agendadas" — el gerente necesita ver valor en 5 segundos cada mañana.
43. [P1] [dashboard diario] ✅ Distintivo "lead caliente" con criterio visible (presupuesto + zona + urgencia) en la bandeja — prioriza el tiempo del comercial donde hay dinero.
44. [P1] [dashboard diario] ✅ Búsqueda de conversaciones por nombre, teléfono o dirección de propiedad — encontrar "el señor del ático de Benalúa" no puede requerir scroll infinito.
45. [P1] [dashboard diario] ✅ Filtros de bandeja por canal, estado (nuevo/cualificado/visita) y agente asignado — a partir de 20 conversaciones/día la bandeja sin filtros es inusable.
46. [P1] [dashboard diario] ✅ Resumen de 3 líneas al abrir cada conversación (quién es, qué busca, qué falta) — nadie relee 40 mensajes antes de devolver una llamada.
47. [P1] [dashboard diario] ✅ Botón "Llamar" y "WhatsApp" con un clic desde la ficha del lead (tel: y wa.me) — cada fricción entre ver el lead y contactarlo cuesta ventas.
48. [P1] [dashboard diario] ✅ Notificación push/email inmediata cuando la IA escala a humano — un cliente que pidió hablar contigo y espera horas es un cliente perdido.
49. [P1] [voz/llamadas] ✅ Grabación y transcripción de cada llamada accesible desde la conversación — el gerente quiere oír cómo vende su IA, sobre todo la primera semana.
50. [P1] [voz/llamadas] ✅ Aviso claro de grabación al inicio de la llamada configurable por agencia — obligación legal y tranquilidad para el dueño.
51. [P1] [voz/llamadas] ✅ Contador de minutos de voz consumidos con barra visual en Facturación ("430 de 600 min") — nadie debe enterarse del extra al ver la factura.
52. [P1] [voz/llamadas] ✅ Horario configurable: IA siempre / solo fuera de horario / solo si no contestas — cada agencia tiene su manera; imponer una genera cancelaciones.
53. [P1] [voz/llamadas] ✅ Lista de frases prohibidas y "no-go zones" editable con ejemplos precargados del sector — el dueño necesita sentir control sobre lo que dice su IA.
54. [P1] [voz/llamadas] ✅ Probar la voz elegida con un texto propio antes de activarla ("Escríbelo y escúchalo") — elegir voz a ciegas produce arrepentimiento y tickets.
55. [P1] [WhatsApp/canales] Vista previa del perfil de WhatsApp Business (foto, nombre, descripción) editable desde el panel — la imagen que ven los clientes debe controlarse sin ir a Meta.
56. [P1] [WhatsApp/canales] Respuestas de la IA en WhatsApp con "escribiendo..." y demora humana configurable — responder en 0,2 segundos delata al robot y asusta a algunos clientes.
57. [P1] [WhatsApp/canales] ✅ Plantillas de mensaje aprobadas por Meta pre-redactadas (recordatorio de visita, seguimiento) — redactar plantillas que Meta apruebe es un arte que el cliente no domina.
58. [P1] [WhatsApp/canales] Etiquetar conversaciones (comprador/vendedor/alquiler/curioso) automáticamente y a mano — la bandeja se vuelve un CRM ligero, que es lo que la agencia pequeña quiere.
59. [P1] [catálogo/Idealista] Sincronización automática con Idealista vía API de partners (adiós CSV) — el catálogo desactualizado hace que la IA hable de pisos ya vendidos.
60. [P1] [catálogo/Idealista] ✅ Marcar propiedad como "vendida/alquilada" con un clic y que la IA deje de ofrecerla al instante — ofrecer un piso vendido es el error que más daña la credibilidad.
61. [P1] [catálogo/Idealista] ✅ Ficha de propiedad con foto principal en el panel — reconocer pisos por foto es como piensa un agente inmobiliario, no por referencia.
62. [P1] [catálogo/Idealista] ✅ Avisar de campos vacíos que degradan a la IA ("A este piso le falta el nº de baños: la IA no podrá responderlo") — el usuario no sabe qué datos usa la IA hasta que falla.
63. [P1] [catálogo/Idealista] ✅ Importación por arrastrar-y-soltar con vista previa de las 5 primeras filas mapeadas — ver "esto se importará así" antes de confirmar elimina el miedo al botón.
64. [P1] [emails transaccionales] ✅ Email "Tu primera semana con selai" con cifras reales (llamadas atendidas, leads captados) al día 7 — el valor demostrado por email decide la conversión de prueba a pago.
65. [P1] [emails transaccionales] ✅ Email día 10 de la prueba: "Te quedan 4 días · esto es lo que selai ha hecho por ti" con CTA a elegir plan — el fin de prueba sin aviso es dinero quemado.
66. [P1] [emails transaccionales] ✅ Aviso por email al 80% de los minutos de voz con enlace directo a subir de plan — coherente con la promesa "nunca cortamos sin avisar".
67. [P1] [emails transaccionales] Factura mensual por email con desglose en llano (plan + minutos extra si los hubo) y PDF adjunto — la contabilidad de la agencia lo pide cada mes.
68. [P1] [soporte/ayuda] ✅ Centro de ayuda con 10-15 artículos con capturas (desvío por operador, CSV, WhatsApp) — cada artículo bueno son decenas de tickets menos.
69. [P1] [soporte/ayuda] ✅ Guía de desvío de llamadas por operador (Movistar, Vodafone, Orange, Yoigo, MásMóvil, Digi) — los códigos varían y "no me funciona el desvío" será el ticket nº 1.
70. [P1] [soporte/ayuda] Widget de soporte dentro del panel con respuesta de la propia IA y escalado a humano — comer nuestra propia comida y resolver el 70% al instante.
71. [P1] [retención/éxito] Informe semanal por email: conversaciones, leads, visitas y la mejor llamada de la semana — el gerente que ve valor cada lunes no cancela.
72. [P1] [retención/éxito] ✅ Alerta inmediata de lead caliente por WhatsApp/SMS al comercial de guardia — un lead con presupuesto y prisa no puede esperar al login de mañana.
73. [P1] [retención/éxito] ✅ Flujo de cancelación con encuesta de motivo + oferta de pausa de 1-2 meses — una pausa retiene a quien cancela "por vacaciones" o "temporada baja".
74. [P1] [retención/éxito] ✅ Detectar inactividad (7 días sin entrar al panel) y enviar email con lo que la IA hizo sola — el churn silencioso empieza cuando dejan de mirar.
75. [P1] [dashboard diario] ✅ Modo "traspaso": tomar el control de una conversación de WhatsApp desde el panel y devolverlo a la IA — el comercial quiere intervenir sin sacar su móvil personal.
76. [P1] [voz/llamadas] Resumen de cada llamada por email/WhatsApp al terminar ("Llamó María, busca 3 hab. en Playa San Juan, hasta 250k, visita el jueves") — el valor de selai cabe en un mensaje que llega solo.
77. [P1] [onboarding/wizard] ✅ Selector de tono de la IA (cercano/formal) con ejemplo de respuesta en vivo al cambiarlo — ver el efecto del ajuste da confianza para tocar la configuración.
78. [P1] [dashboard diario] Vista de agenda con las visitas de la semana y sincronización con Google Calendar — si las visitas no aparecen en su calendario de siempre, no existen.
79. [P1] [emails transaccionales] Recordatorio de visita automático al cliente final (24h y 2h antes) por WhatsApp — reduce plantones, que es dolor diario de cualquier agencia.
80. [P1] [soporte/ayuda] ✅ Estado del sistema (status page) enlazado desde el footer del panel — cuando algo falle, "¿soy yo o son ellos?" debe responderse sin escribir a soporte.
81. [P1] [WhatsApp/canales] Detección de idioma con respuesta automática en el idioma del cliente + aviso en bandeja ("conversación en inglés") — las zonas de costa viven de compradores extranjeros.
82. [P1] [dashboard diario] ✅ Exportar leads a Excel/CSV con un botón — la agencia quiere "sus" datos en su herramienta de siempre sin pedir permiso.
83. [P1] [voz/llamadas] Fallback claro si la IA no entiende tras 2 intentos: tomar recado y avisar al humano — un robot que da vueltas en bucle es la peor imagen posible.
84. [P1] [retención/éxito] ✅ Onboarding proactivo: si a las 48h del registro no ha completado el wizard, email + oferta de videollamada — la mitad de las pruebas mueren en el paso 4; rescatarlas es la palanca de crecimiento más barata.

## Prioridad P2 — pulido

85. [P2] [landing/conversión] Testimonios con foto, nombre, agencia y cifra concreta ("recuperamos 2 ventas el primer mes") — la especificidad es lo que hace creíble un testimonio.
86. [P2] [landing/conversión] Logos de portales e integraciones (Idealista, Fotocasa, HubSpot...) en la LogoStrip con enlace a "integraciones" — el reconocimiento de marcas conocidas presta credibilidad.
87. [P2] [landing/conversión] ✅ Sticky CTA en móvil ("Empieza gratis") al hacer scroll pasado el hero — en móvil el CTA del hero desaparece a los 2 segundos.
88. [P2] [landing/conversión] ✅ Animación del hero desactivable y más corta: el texto completo visible en <1,5 s — quien llega con prisa no espera a que las palabras aparezcan.
89. [P2] [landing/conversión] Página /casos con 2-3 historias de cliente en formato problema→solución→cifras — el contenido de fondo que cierra a los indecisos.
90. [P2] [precios/confianza] ✅ FAQ específica de facturación bajo la parrilla de precios (factura con CIF, recargo de equivalencia, autónomos) — las dudas fiscales españolas merecen respuesta donde surge la duda.
91. [P2] [precios/confianza] ✅ Mostrar el ahorro anual en euros al activar el toggle anual ("ahorras 480 €/año") — el porcentaje no se siente; los euros sí.
92. [P2] [precios/confianza] Garantía explícita: "Si en 30 días no te ha conseguido ni un lead, te devolvemos el dinero" — la garantía traslada el riesgo del comprador al vendedor.
93. [P2] [onboarding/wizard] Tour de 30 segundos del panel al entrar por primera vez (5 tooltips máximo) — orientarse rápido reduce la sensación de "esto me queda grande".
94. [P2] [onboarding/wizard] Vídeos de 30-60 s incrustados en cada paso del wizard — hay quien lee y hay quien solo mira.
95. [P2] [onboarding/wizard] Modo demo con datos sintéticos ("Explora con una agencia de ejemplo") antes de configurar nada — jugar sin miedo a romper acelera la comprensión.
96. [P2] [dashboard diario] Modo oscuro del panel — pedido recurrente de quien pasa el día delante de la pantalla.
97. [P2] [dashboard diario] Atajos de teclado en la bandeja (siguiente conversación, marcar leído, asignar) — los power users de la agencia grande lo agradecen.
98. [P2] [dashboard diario] Gráfica de evolución semanal de leads y conversaciones en la home — la tendencia ("cada vez más") retiene mejor que la foto fija.
99. [P2] [voz/llamadas] Métricas de llamada: duración media, % resueltas sin humano, % con visita agendada — el gerente analítico quiere KPIs, no solo transcripciones.
100. [P2] [voz/llamadas] Música/mensaje de espera personalizable para transferencias a humano — detalle de imagen de marca que se nota.
101. [P2] [voz/llamadas] Vocabulario local configurable (nombres de barrios, urbanizaciones) para mejorar el reconocimiento — que la IA entienda "Cabo de las Huertas" a la primera.
102. [P2] [WhatsApp/canales] Enviar fichas de propiedad como tarjeta con foto + precio + enlace en WhatsApp — una tarjeta visual convierte más que un párrafo de texto.
103. [P2] [WhatsApp/canales] Código QR "Chatea con nosotros" descargable para escaparate y cartelería — lleva el canal digital al tráfico físico de la oficina.
104. [P2] [WhatsApp/canales] Encuesta post-conversación de 1 pregunta ("¿Te hemos ayudado?") — mide la calidad de la IA con datos del cliente final.
105. [P2] [catálogo/Idealista] Historial de cambios del catálogo (quién subió qué y cuándo) — en agencias con varias personas evita el "¿quién ha borrado esto?".
106. [P2] [catálogo/Idealista] Detección de duplicados al importar (misma dirección/referencia) con propuesta de fusión — los duplicados hacen que la IA repita el mismo piso dos veces.
107. [P2] [catálogo/Idealista] Soporte de obra nueva con promociones y unidades — segmento con presupuesto alto y necesidades propias.
108. [P2] [app móvil] Notificaciones push de lead caliente y escalado a humano en la app — el móvil es donde vive el comercial; el panel web es secundario.
109. [P2] [app móvil] Bandeja de conversaciones completa en la app con respuesta manual — contestar desde el sofá es el caso de uso real de las 21h.
110. [P2] [app móvil] Escuchar grabaciones de llamadas desde la app — el gerente revisa las llamadas del día en el coche.
111. [P2] [app móvil] Widget de pantalla de inicio con el contador de leads del día — presencia diaria sin abrir la app.
112. [P2] [app móvil] Modo "no molestar" configurable por horario para las notificaciones — el respeto al descanso también fideliza.
113. [P2] [emails transaccionales] Plantillas de email con la marca de la agencia (logo y color) de cara al cliente final — los emails que ve el comprador deben parecer de la agencia, no de selai.
114. [P2] [emails transaccionales] Email de "visita confirmada" al cliente final con mapa, foto del piso y datos del agente — reduce plantones y da imagen profesional.
115. [P2] [emails transaccionales] Asuntos de email A/B testeados en los transaccionales clave (bienvenida, fin de prueba) — cada punto de apertura extra es dinero.
116. [P2] [soporte/ayuda] ✅ Glosario en llano dentro de la ayuda (lead, CSV, desvío, CRM...) — nivelar el vocabulario evita que nadie se sienta tonto.
117. [P2] [soporte/ayuda] "¿Te ha servido este artículo?" con pulgar arriba/abajo en el centro de ayuda — para saber qué documentación reescribir.
118. [P2] [soporte/ayuda] ✅ Horario y tiempo de respuesta de soporte visibles en la página de contacto ("respondemos en <4h laborables") — la expectativa clara evita la frustración de la espera.
119. [P2] [rendimiento/accesibilidad] ✅ Auditoría Lighthouse de la landing con objetivo >90 en móvil (LCP del hero, imágenes, fuentes) — cada segundo de carga en móvil son visitantes que se van.
120. [P2] [rendimiento/accesibilidad] ✅ Lazy-load de las animaciones SVG y del chat demo bajo el fold — el JS de adorno no debe pagar peaje en la primera carga.
121. [P2] [rendimiento/accesibilidad] ✅ Revisar contraste AA de textos secundarios (ink-300/ink-500 sobre blanco) — hay usuarios de 60 años decidiendo la compra.
122. [P2] [rendimiento/accesibilidad] ✅ Navegación completa por teclado y focus visible en toda la landing y el panel — accesibilidad básica y requisito para clientes institucionales.
123. [P2] [rendimiento/accesibilidad] ✅ Respetar prefers-reduced-motion también en las tarjetas 3D con tilt de Precios — el mareo por movimiento existe y el tilt es prescindible.
124. [P2] [rendimiento/accesibilidad] ✅ Textos alternativos en todas las imágenes y aria-labels en botones de icono — lectores de pantalla y SEO a la vez.
125. [P2] [SEO/contenido] ✅ Datos estructurados FAQPage y Product/Offer (precios) en JSON-LD — aparecer con precio y FAQ desplegada en Google multiplica el CTR.
126. [P2] [SEO/contenido] Blog con 2 artículos/mes orientados a búsqueda ("cómo responder leads de Idealista más rápido") — el tráfico orgánico es el canal de adquisición más barato a 12 meses.
127. [P2] [SEO/contenido] Páginas por ciudad ("IA para inmobiliarias en Alicante/Valencia/Murcia") con testimonio local — la búsqueda inmobiliaria es local; la captación también.
128. [P2] [SEO/contenido] Comparativas honestas ("selai vs contestador", "selai vs contratar un administrativo") — capturan búsquedas de decisión con intención de compra alta.
129. [P2] [SEO/contenido] ✅ Meta descriptions con precio y tiempo ("Desde 99 €/mes, funcionando en 20 min") en todas las páginas públicas — la transparencia empieza en el resultado de Google.
130. [P2] [SEO/contenido] Sitemap.xml y og:image de marca para compartir en WhatsApp/LinkedIn con buena pinta — la landing se compartirá por WhatsApp entre agencias; la tarjeta debe lucir.
131. [P2] [internacionalización] Versión de la landing en catalán — Cataluña y Baleares son mercado natural y el gesto genera cercanía.
132. [P2] [internacionalización] Página en inglés orientada a agencias de costa con clientela extranjera — "your Spanish agency, answering in English 24/7" es un gancho propio.
133. [P2] [internacionalización] Selector de idioma discreto en el footer (ES/CA/EN) — sin molestar al 90% que navega en castellano.
134. [P2] [internacionalización] Formato de moneda y fecha localizado en el panel (1.908,00 €, DD/MM/AAAA) — los detalles de formato delatan software extranjero.
135. [P2] [seguridad percibida] ✅ Página /seguridad con hosting UE, cifrado, backups y subprocesadores listados — el cuñado informático de la agencia la va a buscar; que la encuentre.
136. [P2] [seguridad percibida] ✅ Badge "Datos alojados en la UE 🇪🇺" en footer y checkout — recordatorio visual constante del cumplimiento.
137. [P2] [seguridad percibida] DPA descargable en PDF desde el panel sin pedirlo por email — el trámite legal autoservicio quita fricción a agencias con asesoría.
138. [P2] [seguridad percibida] ✅ Banner de cookies mínimo y honesto (solo lo imprescindible, rechazo en 1 clic) — coherencia entre el discurso de confianza y la primera interacción real.
139. [P2] [seguridad percibida] Autenticación en dos pasos opcional para el panel — las agencias manejan datos personales de clientes y algunas lo exigirán.
140. [P2] [retención/éxito] Comparativa mensual "tu agencia vs. media del sector" (anónima) en el informe — la curiosidad competitiva engancha al gerente.
141. [P2] [retención/éxito] Celebrar hitos dentro del panel ("selai ha atendido tu llamada nº 100") — los pequeños momentos de orgullo construyen apego al producto.
142. [P2] [retención/éxito] Programa "recomienda y gana un mes gratis" con enlace propio por agencia — el sector inmobiliario local funciona por boca a boca entre conocidos.
143. [P2] [retención/éxito] Llamada de éxito a los 30 días de convertir a pago (15 min, agenda automática) — la revisión humana temprana detecta cancelaciones antes de que ocurran.
144. [P2] [retención/éxito] Encuesta NPS trimestral de 1 clic dentro del panel — saber quién te recomendaría (y quién no) antes de que cancele.
145. [P2] [dashboard diario] Notas internas por lead ("le llamé, no cogió") visibles para todo el equipo — la coordinación de 3 personas hoy vive en un grupo de WhatsApp; tráela al panel.
146. [P2] [landing/conversión] ✅ Página 404 con buscador y enlaces a precios/instalación — hasta el error debe reconducir a la venta.
147. [P2] [voz/llamadas] ✅ Simulador de conversación por texto en el panel ("chatea con tu IA como si fueras un cliente") — probar cambios de configuración sin gastar minutos de voz.
148. [P2] [WhatsApp/canales] Autorespuesta configurable para el primer contacto fuera de horario con expectativa clara ("te llamamos mañana a las 9") — gestionar expectativas es atención al cliente barata.
149. [P2] [catálogo/Idealista] ✅ Búsqueda por referencia y dirección en el catálogo del panel — encontrar un piso concreto en 2.000 propiedades no puede requerir paginación manual.
150. [P2] [app móvil] Inicio de sesión con huella/Face ID en la app — el comercial entra 10 veces al día; la contraseña 10 veces al día mata la app.

---

Total: 150 mejoras · 36 P0 · 48 P1 · 66 P2 · 66 implementadas (✅) a 2026-07-25.
