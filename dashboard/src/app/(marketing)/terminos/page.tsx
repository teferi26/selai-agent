import LegalDocument from "../legal/LegalDocument";

export const metadata = {
  title: "Términos y Condiciones · selai",
  description: "Términos y condiciones del servicio selai para inmobiliarias.",
};

export default function TermsPage() {
  return <LegalDocument slug="terms_of_service" />;
}
