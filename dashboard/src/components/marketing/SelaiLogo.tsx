// Wordmark for selai. Pure server component.
type Props = {
  className?: string;
  dark?: boolean;
};

export function SelaiLogo({ className = "", dark = false }: Props) {
  return (
    <span
      className={`font-display font-extrabold tracking-[-0.06em] leading-none ${className}`}
    >
      <span className={dark ? "text-selai-50" : "text-selai-900"}>sel</span>
      <span className="text-accent">ai</span>
      <span className="text-accent">.</span>
    </span>
  );
}
