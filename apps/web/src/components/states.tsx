import { cn } from "@/lib/cn";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="card flex flex-col gap-3 p-6" aria-busy="true" aria-live="polite">
      <div className="skeleton h-4 w-40" />
      <div className="skeleton h-4 w-full" />
      <div className="skeleton h-4 w-3/4" />
      <div className="skeleton h-4 w-1/2" />
      <p className="sr-only">{label}</p>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="card border-[#fecaca] bg-[#fef2f2] p-6">
      <p className="font-semibold text-[#b91c1c]">Something went wrong</p>
      <p className="mt-1 text-sm text-[#7f1d1d]">{message}</p>
      {onRetry ? (
        <button type="button" className="btn btn-secondary mt-4" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("card flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <p className="text-base font-semibold text-foreground">{title}</p>
      {description ? <p className="mt-1 max-w-md text-sm text-text-secondary">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function SuccessBanner({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-[#bbf7d0] bg-[#f0fdf4] px-4 py-3 text-sm text-[#15803d]">
      {message}
    </div>
  );
}
