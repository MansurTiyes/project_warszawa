import { useState } from "react";

type Props = {
  onSubmit: (career_goal: string) => void;
};

const CAREERS = [
  "Frontend engineer",
  "Backend engineer",
  "Machine learning",
  "Systems / infra",
  "Quant / finance",
  "Research / PhD",
  "Unsure",
] as const;

export function ScreenGoal({ onSubmit }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [freeform, setFreeform] = useState("");

  const trimmed = freeform.trim();
  const canSubmit = !!selected || trimmed.length > 0;

  function handleSubmit() {
    if (!canSubmit) return;
    const goal = selected
      ? trimmed
        ? `${selected}: ${trimmed}`
        : selected
      : trimmed;
    onSubmit(goal);
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "0 80px",
      }}
    >
      <div style={{ width: 600 }}>
        <h1
          className="h-display"
          style={{
            fontSize: 42,
            margin: "0 0 36px",
            textAlign: "center",
            letterSpacing: "-0.025em",
          }}
        >
          Where are you <em>headed?</em>
        </h1>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            justifyContent: "center",
            marginBottom: 28,
          }}
        >
          {CAREERS.map((c) => {
            const isSel = c === selected;
            return (
              <button
                key={c}
                type="button"
                className="btn"
                onClick={() => setSelected(isSel ? null : c)}
                style={{
                  padding: "8px 16px",
                  fontSize: 13.5,
                  borderRadius: 999,
                  background: isSel ? "var(--cardinal-bright)" : "transparent",
                  borderColor: isSel ? "var(--cardinal-bright)" : "var(--line-2)",
                  color: isSel ? "white" : "var(--ink-1)",
                  fontWeight: isSel ? 500 : 400,
                }}
              >
                {c}
              </button>
            );
          })}
        </div>

        <textarea
          value={freeform}
          onChange={(e) => setFreeform(e.target.value)}
          placeholder="Anything more specific? E.g. recommender systems, distributed training, low-latency trading…"
          rows={4}
          style={{
            width: "100%",
            background: "var(--bg-1)",
            border: "1px solid var(--line-2)",
            borderRadius: 12,
            padding: "16px 18px",
            minHeight: 96,
            fontSize: 15,
            lineHeight: 1.5,
            color: "var(--ink-0)",
            fontFamily: "var(--font-sans)",
            marginBottom: 32,
            resize: "vertical",
            outline: "none",
            boxSizing: "border-box",
          }}
        />

        <div style={{ display: "flex", justifyContent: "center" }}>
          <button
            type="button"
            className="btn btn--primary btn--lg"
            onClick={handleSubmit}
            disabled={!canSubmit}
            style={{
              opacity: canSubmit ? 1 : 0.5,
              cursor: canSubmit ? "pointer" : "default",
            }}
          >
            Generate schedule <span className="btn__arrow">→</span>
          </button>
        </div>
      </div>
    </div>
  );
}
