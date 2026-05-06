import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  postChat,
  postScheduleModify,
  postStars,
  streamPipeline,
} from "@/lib/api";
import {
  clearModifyIntent,
  loadAdvisorState,
  saveAfterModify,
  saveAfterPipeline,
  saveAfterStars,
  setCareerGoal,
  setCurrentIndex,
  setModifyIntent,
} from "@/lib/localStorage";
import { stepFor } from "@/lib/router";
import type { AdvisorState, ChatMessage, ChatRequest, Step } from "@/types";
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

  // Chat state — not persisted (per CLAUDE.md "no chat history persistence")
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatPending, setChatPending] = useState(false);
  const [modifyPending, setModifyPending] = useState(false);
  const chatAbortRef = useRef<AbortController | null>(null);

  // Stale modify_intent cleanup: on first mount, if localStorage carries a
  // pending modify_intent but we have no chat history to anchor it, drop it.
  // (Per CLAUDE.md: "On page reload with a stale modify_intent, frontend
  // silently discards it.")
  const staleCleanupRan = useRef(false);
  useEffect(() => {
    if (staleCleanupRan.current) return;
    staleCleanupRan.current = true;
    if (state.modify_intent && chatMessages.length === 0) {
      setState(clearModifyIntent());
    }
    // run-once on mount; deliberately empty deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Abort any in-flight chat/modify request on unmount.
  useEffect(() => {
    return () => chatAbortRef.current?.abort();
  }, []);

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

  // ============================================================
  // Chat handlers (only relevant on the plan step)
  // ============================================================

  async function handleSend(text: string) {
    const pipeline_state = state.pipeline_state;
    if (!pipeline_state || state.versions.length === 0) return;
    if (chatPending || modifyPending) return;

    // Implicit cancel: any new chat send clears a pending modify_intent
    // BEFORE the request fires. Otherwise the buttons would linger across
    // the request and confuse the user.
    let nextState = state;
    if (state.modify_intent) {
      nextState = clearModifyIntent();
      setState(nextState);
    }

    const priorMessages = chatMessages;
    const userMsg: ChatMessage = { type: "human", content: text };
    setChatMessages([...priorMessages, userMsg]);

    const viewedPlan = state.versions[state.currentIndex].plan;
    const req: ChatRequest = {
      message: text,
      // Wire shape is `{type, content}[]` — see types/index.ts. OpenAPI
      // generator widens it to `{[k]: unknown}[]` (Pydantic list[dict]),
      // so we cast at the boundary.
      messages: priorMessages as unknown as ChatRequest["messages"],
      current_plan: viewedPlan,
      student_state: pipeline_state.student_state,
      requirements_map: pipeline_state.requirements_map,
      career_goal: pipeline_state.career_goal,
      hard_courses: pipeline_state.hard_courses,
      scored_electives: pipeline_state.scored_electives,
      scored_enrichment: pipeline_state.scored_enrichment,
      other_must_reqs: pipeline_state.other_must_reqs,
      ge_placeholders_remaining: pipeline_state.ge_placeholders_remaining,
    };

    chatAbortRef.current?.abort();
    const ac = new AbortController();
    chatAbortRef.current = ac;
    setChatPending(true);
    try {
      const res = await postChat(req, ac.signal);
      if (ac.signal.aborted) return;
      const aiMsg: ChatMessage = { type: "ai", content: res.message };
      setChatMessages((prev) => [...prev, aiMsg]);
      if (res.modify_intent) {
        setState(setModifyIntent(res.modify_intent));
      }
    } catch (err) {
      if (ac.signal.aborted) return;
      const errMsg = formatApiError(err, "Couldn't reach the advisor");
      setChatMessages((prev) => [...prev, { type: "ai", content: errMsg }]);
    } finally {
      if (chatAbortRef.current === ac) chatAbortRef.current = null;
      setChatPending(false);
    }
  }

  async function handleConfirmModify() {
    const pipeline_state = state.pipeline_state;
    const intent = state.modify_intent;
    if (!pipeline_state || !intent) return;
    if (state.versions.length === 0) return;
    if (chatPending || modifyPending) return;

    const viewedPlan = state.versions[state.currentIndex].plan;

    chatAbortRef.current?.abort();
    const ac = new AbortController();
    chatAbortRef.current = ac;
    setModifyPending(true);
    try {
      const res = await postScheduleModify(
        {
          modify_intent: intent,
          current_plan: viewedPlan,
          student_state: pipeline_state.student_state,
          requirements_map: pipeline_state.requirements_map,
          career_goal: pipeline_state.career_goal,
          hard_courses: pipeline_state.hard_courses,
          scored_electives: pipeline_state.scored_electives,
          scored_enrichment: pipeline_state.scored_enrichment,
          other_must_reqs: pipeline_state.other_must_reqs,
          ge_placeholders_remaining: pipeline_state.ge_placeholders_remaining,
        },
        ac.signal,
      );
      if (ac.signal.aborted) return;
      const next = saveAfterModify({
        new_plan: res.new_plan,
        diff_label: res.diff_label,
        schedule_remarks: res.schedule_remarks,
      });
      setState(next);
      const newVersionNumber = next.currentIndex + 1;
      setChatMessages((prev) => [
        ...prev,
        { type: "ai", content: `Updated. Switching you to v${newVersionNumber}.` },
      ]);
    } catch (err) {
      if (ac.signal.aborted) return;
      const errMsg = formatApiError(err, "Couldn't apply that change");
      setChatMessages((prev) => [...prev, { type: "ai", content: errMsg }]);
    } finally {
      if (chatAbortRef.current === ac) chatAbortRef.current = null;
      setModifyPending(false);
    }
  }

  function handleRegenerate() {
    if (!state.modify_intent) return;
    setState(clearModifyIntent());
    setChatMessages((prev) => [
      ...prev,
      { type: "ai", content: "OK, no change made." },
    ]);
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
          chatMessages={chatMessages}
          modify_intent={state.modify_intent}
          chatPending={chatPending}
          modifyPending={modifyPending}
          onSend={handleSend}
          onConfirmModify={handleConfirmModify}
          onRegenerate={handleRegenerate}
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
