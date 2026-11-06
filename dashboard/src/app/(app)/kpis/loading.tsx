// Mismo número y disposición de tarjetas que la página real, para que al
// llegar los datos no salte el contenido.
import { Skeleton, SkeletonMetrics } from "@/components/ui";

export default function KpisLoading() {
  return (
    <div>
      <header className="mb-6">
        <Skeleton width={260} height={26} />
      </header>
      <SkeletonMetrics cards={8} columns={4} />
    </div>
  );
}
