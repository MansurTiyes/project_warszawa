import { Spinner } from "@/components/Spinner";

type Props = {
  activeIndex: number;
  errorMessage?: string | null;
  onRetry?: () => void;
};

const EVENTS = [
  { event: "started",                    message: "Analyzing your requirements…" },
  { event: "hard_requirements_complete", message: "Identified remaining requirements" },
  { event: "soft_requirements_complete", message: "Ranked electives for your career goal" },
  { event: "plan_generated",             message: "Building your schedule…" },
  { event: "complete",                   message: "Done" },
] as const;

export function ScreenGenerating({ activeIndex, errorMessage, onRetry }: Props) {
  const hasError = !!errorMessage;

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
      }}
    >
      <div
        style={{
          width: 460,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <div
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 32,
            fontStyle: "italic",
            color: "var(--ink-0)",
            letterSpacing: "-0.02em",
            marginBottom: 40,
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}
        >
          {!hasError && <Spinner size={22} />}
          {hasError ? "Stalled." : "Cooking…"}
        </div>

        {!hasError && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 8,
              alignItems: "center",
            }}
          >
            {EVENTS.map((e, i) => {
              const isDone = i < activeIndex;
              const isCurrent = i === activeIndex;
              const isFuture = i > activeIndex;
              return (
                <div
                  key={e.event}
                  style={{
                    fontFamily: "var(--font-sans)",
                    fontSize: 14,
                    color: isDone
                      ? "var(--ink-4)"
                      : isCurrent
                        ? "var(--ink-0)"
                        : "var(--ink-4)",
                    opacity: isFuture ? 0.28 : 1,
                    transition: "color 0.4s ease, opacity 0.4s ease",
                  }}
                >
                  {e.message}
                </div>
              );
            })}
          </div>
        )}

        {hasError && (
          <div
            role="alert"
            style={{
              width: "100%",
              padding: "16px 18px",
              border: "1px solid var(--line-2)",
              background: "var(--bg-1)",
              borderRadius: 12,
              display: "flex",
              flexDirection: "column",
              gap: 14,
              alignItems: "center",
              textAlign: "center",
            }}
          >
            <div
              style={{
                fontSize: 14,
                color: "var(--ink-1)",
                lineHeight: 1.5,
              }}
            >
              {errorMessage}
            </div>
            {onRetry && (
              <button
                type="button"
                className="btn btn--primary"
                onClick={onRetry}
                style={{ padding: "8px 16px", fontSize: 13.5 }}
              >
                Try again
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
