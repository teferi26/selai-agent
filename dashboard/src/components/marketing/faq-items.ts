// FAQ data lives in its own module so it's importable from BOTH the FAQ
// component (client) and the page metadata / JSON-LD (server). Putting it in
// the same `'use client'` file would turn server-side imports into a
// client reference object rather than the actual array.

export const FAQ_ITEMS: ReadonlyArray<readonly [string, string]> = [
  [
    "¿Cuánto cuesta de verdad? ¿Hay costes ocultos?",
    "Tres planes: 99, 199 o 399 €/mes, y ese es el importe exacto que se cobra. Sin coste de instalación, sin permanencia y sin sorpresas. Si te acercas a los límites de tu plan te avisamos al 80%, y ampliar de plan es un clic pagando solo la diferencia.",
  ],
  [
    "¿Cuánto tardo en tenerlo funcionando?",
    "Queda operativa el primer día. Un asistente de activación te guía paso a paso desde que te registras: la voz son 2 minutos, el número de tu IA un clic (o menos de 24 h si hay que solicitarlo), los portales 5 minutos, el catálogo 10 y el chat de tu web 2. La única excepción es WhatsApp: si Meta aún no ha verificado tu empresa, su revisión tarda 1-2 días — se hace en paralelo y no te bloquea nada.",
  ],
  [
    "¿Cuánto tarda WhatsApp?",
    "La conexión guiada desde el panel son unos 15 minutos de tu parte. Si tu empresa ya está verificada por Meta, funciona ese mismo día; si no, Meta tarda 1-2 días en verificarla (es su plazo, no el nuestro) y el panel te dice en qué punto está. Mientras tanto, llamadas, portales, email y chat web ya atienden.",
  ],
  [
    "¿Necesito tarjeta para probar?",
    "No. Los 14 días de prueba son gratis y no te pedimos tarjeta. Si al acabar no sigues, no pagas nada.",
  ],
  [
    "¿Puedo cancelar cuando quiera?",
    "Sí. Desde la sección Facturación de tu panel, en un clic y sin llamar a nadie. La cancelación es efectiva al final del ciclo que ya has pagado.",
  ],
  [
    "¿Puedo usar mi propio número?",
    "Sí. Tu IA tiene su propio número, y tu número de siempre sigue siendo tuyo: lo desvías marcando **21*NÚMERO-DE-TU-IA# en tu móvil (o **61*… para desviar solo cuando no contestas) y lo quitas cuando quieras con ##21#. Son códigos estándar de la red móvil: funcionan en Movistar, Vodafone, Orange y el resto de operadores españoles, y si tu tarifa los restringe, tu operador te activa el desvío gratis por teléfono.",
  ],
  [
    "¿Necesito saber de tecnología?",
    "No. El asistente de activación de tu panel es de «siguiente, siguiente»: cada paso es un botón, y cuando hay que copiar algo (una dirección de email, el código del chat), te lo damos ya preparado con su botón de copiar. Si te atascas, te acompañamos por videollamada.",
  ],
  [
    "¿Qué pasa si selai dice algo incorrecto a un cliente?",
    "Cada respuesta pasa dos filtros antes de enviarse: uno de reglas fijas, que bloquea datos personales y promesas que no has autorizado, y otro de IA que revisa el mensaje entero. Además, todo queda registrado: cualquier conversación la puedes leer en tu bandeja, y si hay una frase que no quieres que diga, la añades a «frases prohibidas» y no la volverá a decir.",
  ],
  [
    "¿Cómo trata mis datos? ¿Cumplís RGPD?",
    "Sí — RGPD desde el día 1. Tus datos se alojan en la UE (Frankfurt). Al registrarte aceptas el contrato de protección de datos (el «DPA», te lo enviamos cuando quieras), los datos personales de tus clientes se ocultan en nuestros registros internos y nunca entrenamos modelos de IA con tus datos.",
  ],
  [
    "¿Se integra con mi CRM?",
    "Lo que hay hoy, sin adornos: exportas todos tus leads a CSV en un clic y, si tu CRM admite avisos automáticos, en el panel tienes la dirección de notificaciones lista para copiar — cada lead nuevo le llega solo. Las integraciones nativas con Inmovilla y Witei están en camino.",
  ],
  [
    "¿Cómo llegan los leads de Idealista o Fotocasa?",
    "Por reenvío de email, automático y al instante. En el panel te damos una dirección única para tu agencia; reenvías a ella los avisos de Idealista, Fotocasa o Habitaclia y cada nuevo contacto entra en selai en el momento, ya atendido por la IA. Funciona desde el primer día.",
  ],
  [
    "¿En qué idiomas habla?",
    "Castellano, catalán, inglés, francés y alemán de forma nativa. Detección automática del idioma del lead.",
  ],
  [
    "¿Puedo escalar a un humano?",
    "Sí. Si el cliente pide hablar con una persona (o la IA nota que está molesto), la conversación pasa a tu equipo con un aviso y en tu bandeja la ves con un resumen de 3 líneas. También eliges cuándo atiende la IA: siempre, o solo fuera de tu horario. Y cuando termines, un clic en «Devolver a la IA» y el asistente retoma.",
  ],
  [
    "¿Qué pasa con mis conversaciones cuando me voy?",
    "Te llevas una copia completa de todo (JSON y CSV). Borrado certificado en 30 días. Sin ataduras.",
  ],
  [
    "¿Hay permanencia?",
    "No. Pagas mes a mes (o al año, con un 20% de descuento). Cancelas cuando quieras desde Facturación.",
  ],
  [
    "¿Quién está detrás de selai?",
    "Un equipo pequeño en Alicante. Construido en la Universidad de Alicante. Frustración real con el sector inmobiliario español.",
  ],
  [
    "¿Personalizáis el tono?",
    "Sí, y lo controlas tú desde el panel: eliges el tono (cercano o formal), añades las frases que nunca debe decir y creas tus plantillas de mensajes (recordatorios de visita, seguimientos…). Los cambios se aplican al momento.",
  ],
  [
    "¿Qué pasa si supero el límite de contactos o de minutos de llamada?",
    "Te avisamos al llegar al 80% para que no te pille por sorpresa. Si lo superas, amplías de plan en un clic desde Facturación pagando solo la diferencia prorrateada. Nunca hay cargos automáticos que no hayas contratado.",
  ],
  [
    "¿Soporte humano de verdad?",
    "Sí. Por email en Starter y Pro, prioritario en Agencia. Personas de verdad, en español, en horario laboral.",
  ],
  [
    "¿Qué incluye la prueba gratuita?",
    "14 días con todas las funciones del plan Pro, sin tarjeta. Si no sigues, no pagas nada y tus datos se borran.",
  ],
  [
    "¿Cómo se factura?",
    "Mensual o anual, con tarjeta a través de Stripe. Recibes el justificante de cada cobro por email y los tienes descargables en tu panel; al contratar puedes añadir el NIF de tu agencia para que figure en ellos.",
  ],
  [
    "¿Puedo ver una demo antes?",
    "Claro — 20 minutos por videollamada. Te enseñamos selai con datos sintéticos de inmobiliaria y respondemos lo que sea.",
  ],
];
