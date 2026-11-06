import LegalDocument from "../legal/LegalDocument";

export const metadata = {
  title: "Política de Privacidad · selai",
  description:
    "Cómo selai trata los datos personales de agencias inmobiliarias y sus leads.",
};

export default function PrivacyPage() {
  return <LegalDocument slug="privacy_policy" />;
}
