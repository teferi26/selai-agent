// La bandeja se pinta en servidor y espera al backend. Sin esto el usuario ve
// la pantalla anterior congelada; con esto ve enseguida la forma de la lista.
import { Skeleton, SkeletonList } from "@/components/ui";

export default function InboxLoading() {
  return (
    <div>
      <header className="mb-6 space-y-2">
        <Skeleton width={180} height={26} />
        <Skeleton width={280} height={12} />
      </header>
      <SkeletonList rows={6} />
    </div>
  );
}
