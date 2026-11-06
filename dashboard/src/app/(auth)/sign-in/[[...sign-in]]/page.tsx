import { SignIn } from "@clerk/nextjs";
import { parsePurchaseIntent, withPurchaseIntent } from "@/lib/purchase-intent";

export const metadata = { title: "Iniciar sesión" };

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ plan?: string | string[]; interval?: string | string[] }>;
}) {
  const intent = parsePurchaseIntent(await searchParams);
  return (
    <main className="flex min-h-screen items-center justify-center bg-paper">
      <SignIn
        signUpUrl={withPurchaseIntent("/sign-up", intent)}
        fallbackRedirectUrl={intent ? withPurchaseIntent("/billing", intent) : "/inbox"}
      />
    </main>
  );
}
