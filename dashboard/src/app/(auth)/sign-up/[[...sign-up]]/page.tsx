import { SignUp } from "@clerk/nextjs";
import { parsePurchaseIntent, withPurchaseIntent } from "@/lib/purchase-intent";

export const metadata = { title: "Crear cuenta" };

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ plan?: string | string[]; interval?: string | string[] }>;
}) {
  const intent = parsePurchaseIntent(await searchParams);
  return (
    <main className="flex min-h-screen items-center justify-center bg-paper">
      <SignUp
        signInUrl={withPurchaseIntent("/sign-in", intent)}
        fallbackRedirectUrl={withPurchaseIntent("/onboarding", intent)}
      />
    </main>
  );
}
