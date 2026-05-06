import { useState } from "react";
import { ApiError, postStars } from "@/lib/api";
import { loadAdvisorState, saveAfterStars } from "@/lib/localStorage";
import { stepFor } from "@/lib/router";
import type { AdvisorState, Step } from "@/types";
import { ScreenUpload } from "@/components/ScreenUpload";
import { ScreenParsing } from "@/components/ScreenParsing";
import { ScreenStudentState } from "@/components/ScreenStudentState";

export default function App() {
  const [state, setState] = useState<AdvisorState>(loadAdvisorState);
  const [step, setStep] = useState<Step>(() => stepFor(state));
  const [error, setError] = useState<string | null>(null);

  async function handleUpload(file: File) {
    setError(null);
    setStep("parsing");
    try {
      const res = await postStars(file);
      const next = saveAfterStars({
        student_state: res.student_state,
        requirements_map: res.requirements_map,
        student_state_summary: res.student_state_summary,
      });
      setState(next);
      setStep("breakdown");
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `Couldn't read that PDF (${err.status}). ${err.message}`
          : err instanceof Error
            ? `Couldn't reach the advisor: ${err.message}`
            : "Something went wrong.";
      setError(message);
      setStep("upload");
    }
  }

  switch (step) {
    case "upload":
      return <ScreenUpload onSelect={handleUpload} initialError={error} />;

    case "parsing":
      return <ScreenParsing />;

    case "breakdown":
      if (!state.partial_state) {
        return <ScreenUpload onSelect={handleUpload} />;
      }
      return (
        <ScreenStudentState
          student_state={state.partial_state.student_state}
        />
      );

    case "goal":
    case "generating":
    case "plan":
      return <Placeholder step={step} />;
  }
}

function Placeholder({ step }: { step: Step }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 32,
        textAlign: "center",
      }}
    >
      <div>
        <h1
          className="h-display"
          style={{ fontSize: 38, margin: "0 0 12px", letterSpacing: "-0.025em" }}
        >
          <em>{step}</em> screen
        </h1>
        <p style={{ color: "var(--ink-2)", margin: 0, fontSize: 15 }}>
          Coming in Phase 3.
        </p>
      </div>
    </div>
  );
}
