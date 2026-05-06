import { useEffect, useState } from "react";
import { ApiError, postStars, streamPipeline } from "@/lib/api";
import {
  loadAdvisorState,
  saveAfterPipeline,
  saveAfterStars,
  setCareerGoal,
  setCurrentIndex,
} from "@/lib/localStorage";
import { stepFor } from "@/lib/router";
import type { AdvisorState, Step } from "@/types";
import { ScreenUpload } from "@/components/ScreenUpload";
import { ScreenParsing } from "@/components/ScreenParsing";
import { ScreenStudentState } from "@/components/ScreenStudentState";
import { ScreenGoal } from "@/components/ScreenGoal";
import { ScreenGenerating } from "@/components/ScreenGenerating";
import { ScreenSchedule } from "@/components/ScreenSchedule";

export default function App() {
  const [state, setState] = useState<AdvisorState>(loadAdvisorState);
  const [step, setStep] = useState<Step>(() => stepFor(state));
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const [runId, setRunId] = useState(0);

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
      setError(formatApiError(err, "Couldn't read that PDF"));
      setStep("upload");
    }
  }

  function handleGoalSubmit(goal: string) {
    const next = setCareerGoal(goal);
    setState(next);
    setActiveIndex(0);
    setPipelineError(null);
    setStep("generating");
  }

  function handleRetryPipeline() {
    setActiveIndex(0);
    setPipelineError(null);
    setRunId((id) => id + 1);
  }

  function handleSelectVersion(idx: number) {
    const next = setCurrentIndex(idx);
    setState(next);
  }

  // Pipeline driver. Lives here (not in ScreenGenerating) so the AbortController
  // is owned by App and gets cancelled on step change / unmount.
  useEffect(() => {
    if (step !== "generating") return;
    const partial = state.partial_state;
    if (!partial || !partial.career_goal) {
      setPipelineError("Missing career goal — please pick one.");
      return;
    }

    const ac = new AbortController();

    (async () => {
      try {
        const stream = streamPipeline(
          {
            career_goal: partial.career_goal!,
            student_state: partial.student_state,
            requirements_map: partial.requirements_map,
          },
          ac.signal,
        );

        for await (const ev of stream) {
          switch (ev.event) {
            case "started":
              setActiveIndex(0);
              break;
            case "hard_requirements_complete":
              setActiveIndex(1);
              break;
            case "soft_requirements_complete":
              setActiveIndex(2);
              break;
            case "plan_generated":
              setActiveIndex(3);
              break;
            case "complete": {
              setActiveIndex(4);
              const next = saveAfterPipeline(ev.payload, partial);
              setState(next);
              setStep("plan");
              return;
            }
            case "error":
              setPipelineError(ev.message || "Pipeline failed.");
              return;
          }
        }
      } catch (err) {
        if (ac.signal.aborted) return;
        setPipelineError(formatApiError(err, "Couldn't generate your plan"));
      }
    })();

    return () => ac.abort();
  }, [step, runId, state.partial_state]);

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
          onContinue={() => setStep("goal")}
        />
      );

    case "goal":
      if (!state.partial_state) {
        return <ScreenUpload onSelect={handleUpload} />;
      }
      return <ScreenGoal onSubmit={handleGoalSubmit} />;

    case "generating":
      return (
        <ScreenGenerating
          activeIndex={activeIndex}
          errorMessage={pipelineError}
          onRetry={pipelineError ? handleRetryPipeline : undefined}
        />
      );

    case "plan": {
      if (!state.pipeline_state || state.versions.length === 0) {
        return <ScreenUpload onSelect={handleUpload} />;
      }
      const version = state.versions[state.currentIndex] ?? state.versions[0];
      return (
        <ScreenSchedule
          current_plan={version.plan}
          remarks={version.schedule_remarks}
          versions={state.versions}
          currentIndex={state.currentIndex}
          onSelectVersion={handleSelectVersion}
          student_state={state.pipeline_state.student_state}
        />
      );
    }
  }
}

function formatApiError(err: unknown, prefix: string): string {
  if (err instanceof ApiError) {
    return `${prefix} (${err.status}). ${err.message}`;
  }
  if (err instanceof Error) {
    return `Couldn't reach the advisor: ${err.message}`;
  }
  return "Something went wrong.";
}
