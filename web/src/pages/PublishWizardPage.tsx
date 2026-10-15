/**
 * Publish wizard (SPEC §11.4, target < 60 s end-to-end): upload a skill → Gemini drafts the
 * listing → avatar → pricing → go live → knowledge files uploaded one per request.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, BookOpen, CheckCircle2, FilePlus2, Rocket, Timer, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useBlocker, useNavigate } from "react-router";
import { hasCode, ErrorCodes } from "@/api/errors";
import { useMeta } from "@/api/meta";
import { queryKeys } from "@/api/queryClient";
import type { Agent, Skill } from "@/api/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";
import { toast } from "@/lib/toast";
import { UploadList } from "@/features/knowledge/UploadList";
import { uploadKnowledgeFile } from "@/features/knowledge/api";
import { progressOf, runUploads, type UploadItem } from "@/features/knowledge/uploadQueue";
import { createAgent, draftAgent, uploadSkill, useSlugStatus, type DraftQuality } from "@/features/publish/api";
import {
  classifyDrop,
  filesFromInput,
  isKnowledgeFile,
  knowledgeFileProblem,
  mergeKnowledge,
  skillProblem,
  KNOWLEDGE_MAX_FILES,
  type PickedFile,
  type SkillUpload,
} from "@/features/publish/files";
import { GoLiveStep } from "@/features/publish/GoLiveStep";
import { DraftingSkeleton, IdentityStep } from "@/features/publish/IdentityStep";
import { PricingFields } from "@/features/publish/PricingFields";
import { SkillDropzone } from "@/features/publish/SkillDropzone";
import {
  STEP_LABELS,
  WIZARD_STEPS,
  buildCreateRequest,
  emptyForm,
  firstBlockingStep,
  formFromDraft,
  formatElapsed,
  identityEdited,
  nextStep,
  prevStep,
  stepErrors,
  templateDraft,
  type IdentityForm,
  type WizardForm,
  type WizardStep,
} from "@/features/publish/wizard";
import { useBeforeUnload, useNow } from "@/features/studio/hooks";

type Phase = "editing" | "creating" | "uploading";

/** Both ways a new draft can overwrite the creator's identity edits, confirmed with one dialog. */
type PendingReplace = { kind: "redraft"; quality: DraftQuality } | { kind: "skill"; upload: SkillUpload };

/** Moves the first invalid control (or step-level error) into view so a blocked Continue is never silent. */
function revealFirstError() {
  const el = document.querySelector<HTMLElement>('[aria-invalid="true"], [data-step-error]');
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.focus({ preventScroll: true });
}

function Stepper({
  step,
  reachable,
  onSelect,
  disabled,
}: {
  step: WizardStep;
  reachable: (s: WizardStep) => boolean;
  onSelect: (s: WizardStep) => void;
  disabled: boolean;
}) {
  const current = WIZARD_STEPS.indexOf(step);
  return (
    <ol className="mb-6 grid grid-cols-4 gap-2" aria-label="Steps">
      {WIZARD_STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        const can = !disabled && !active && reachable(s);
        return (
          <li key={s} className="min-w-0">
            <button
              type="button"
              disabled={!can}
              aria-current={active ? "step" : undefined}
              onClick={() => onSelect(s)}
              className={cn(
                "flex w-full min-w-0 flex-col gap-1.5 text-left",
                can ? "cursor-pointer" : "cursor-default",
              )}
            >
              <span
                className={cn(
                  "h-1 w-full rounded-chip transition-colors",
                  active || done ? "bg-primary" : "bg-slate-200",
                )}
              />
              <span
                className={cn(
                  "truncate text-xs font-medium sm:text-sm",
                  active ? "text-text" : done ? "text-primary" : "text-subtle",
                )}
              >
                <span className="hidden sm:inline">{i + 1}. </span>
                {STEP_LABELS[s]}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export default function PublishWizardPage() {
  const meta = useMeta();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [step, setStepState] = useState<WizardStep>("upload");
  const setStep = (s: WizardStep) => {
    setStepState(s);
    window.scrollTo({ top: 0 });
  };
  const [form, setForm] = useState<WizardForm>(() => emptyForm(""));
  const [skill, setSkill] = useState<Skill | null>(null);
  const [knowledge, setKnowledge] = useState<File[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [drafted, setDrafted] = useState<IdentityForm | null>(null);
  const [draftQuality, setDraftQuality] = useState<DraftQuality>("fast");
  const [draftFallback, setDraftFallback] = useState<unknown>(null);
  const [confirmReplace, setConfirmReplace] = useState<PendingReplace | null>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const [errorPing, setErrorPing] = useState(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [finishedMs, setFinishedMs] = useState<number | null>(null);
  const [phase, setPhase] = useState<Phase>("editing");
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const knowledgeInput = useRef<HTMLInputElement | null>(null);
  const draftSeq = useRef(0);
  const [draftRev, setDraftRev] = useState(0);

  const now = useNow(startedAt !== null && finishedMs === null);
  const elapsed = finishedMs ?? (startedAt !== null ? now - startedAt : 0);
  const busy = phase !== "editing";
  useBeforeUnload(busy);
  // Keep the creator on the page while the agent is created and knowledge files upload. Leaving
  // anyway aborts the publish run; its callbacks then neither toast nor navigate.
  const leaving = useRef(false);
  const publishAbort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const stopped = () => !mounted.current || publishAbort.current?.signal.aborted === true;
  useEffect(() => {
    if (errorPing > 0) revealFirstError();
  }, [errorPing]);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      busy && !leaving.current && currentLocation.pathname !== nextLocation.pathname,
  );

  // The model defaults to meta.default_chat_model once meta has loaded.
  const effective = useMemo<WizardForm>(
    () => ({ ...form, golive: { ...form.golive, model: form.golive.model || meta.data?.default_chat_model || "" } }),
    [form, meta.data?.default_chat_model],
  );
  const slugStatus = useSlugStatus(form.golive.slug);

  const draft = useMutation({
    mutationFn: ({ skillIds, quality }: { skillIds: string[]; quality: DraftQuality; seq: number }) =>
      draftAgent(skillIds, quality),
    onSuccess: (d, vars) => {
      if (vars.seq !== draftSeq.current) return;
      setDraftFallback(null);
      const next = formFromDraft(d, form);
      setForm(next);
      setDrafted(next.identity);
      setDraftRev((r) => r + 1);
    },
    onError: (err, vars) => {
      if (vars.seq !== draftSeq.current) return;
      // Keep any earlier draft; otherwise prefill a template so the creator can still publish.
      setDraftFallback(err);
      if (!drafted && skill) {
        const next = formFromDraft(templateDraft([skill]), form);
        setForm(next);
        setDrafted(next.identity);
        setDraftRev((r) => r + 1);
      }
    },
  });

  const startDraft = (s: Skill, quality: DraftQuality) => {
    draftSeq.current += 1;
    setDraftQuality(quality);
    draft.mutate({ skillIds: [s.id], quality, seq: draftSeq.current });
  };

  const skillUpload = useMutation({
    mutationFn: (s: SkillUpload) => uploadSkill(s),
    onSuccess: (s) => {
      setSkill(s);
      setDrafted(null);
      setDraftFallback(null);
      setStep("identity");
      startDraft(s, "fast");
    },
  });

  const startSkillUpload = (upload: SkillUpload) => {
    if (startedAt === null) setStartedAt(Date.now());
    skillUpload.mutate(upload);
  };

  const onPick = (picked: PickedFile[]) => {
    const c = classifyDrop(picked);
    const found: string[] = [...c.problems];
    const docs = c.knowledge.filter((f) => {
      const p = knowledgeFileProblem(f);
      if (p) found.push(p);
      return !p;
    });
    if (docs.length > 0) setKnowledge((k) => mergeKnowledge(k, docs).slice(0, KNOWLEDGE_MAX_FILES));
    if (c.skill) {
      const p = skillProblem(c.skill);
      if (p) found.push(p);
      // A different skill starts a fresh draft, so edits to the identity are confirmed first.
      else if (skill && identityEdited(form.identity, drafted)) setConfirmReplace({ kind: "skill", upload: c.skill });
      else startSkillUpload(c.skill);
    }
    setProblems(found);
  };

  const addKnowledge = (files: File[]) => {
    const found: string[] = [];
    const ok = files.filter((f) => {
      const p = knowledgeFileProblem(f);
      if (p) found.push(p);
      return !p;
    });
    setKnowledge((k) => mergeKnowledge(k, ok).slice(0, KNOWLEDGE_MAX_FILES));
    setProblems(found);
  };

  const ctx = { skillIds: skill ? [skill.id] : [], drafting: draft.isPending, slug: slugStatus };
  const errors = stepErrors(step, effective, ctx);
  const reachable = (s: WizardStep) =>
    WIZARD_STEPS.indexOf(s) <= WIZARD_STEPS.indexOf(step) || firstBlockingStep(s, effective, ctx) === null;

  const publish = useMutation({
    mutationFn: async () => {
      const controller = new AbortController();
      publishAbort.current = controller;
      setPhase("creating");
      return createAgent(buildCreateRequest(effective, ctx.skillIds), controller.signal);
    },
    onSuccess: async (agent: Agent) => {
      const signal = publishAbort.current?.signal;
      const took = startedAt !== null ? Date.now() - startedAt : 0;
      setFinishedMs(took);
      qc.setQueryData(queryKeys.agent(agent.id), agent);
      void qc.invalidateQueries({ queryKey: queryKeys.agents });
      void qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      let failed = 0;
      if (knowledge.length > 0) {
        setPhase("uploading");
        const final = await runUploads(
          knowledge,
          (f) => uploadKnowledgeFile(agent.id, f, signal),
          setUploads,
          signal,
        );
        failed = progressOf(final).failed;
        void qc.invalidateQueries({ queryKey: queryKeys.agent(agent.id) });
        void qc.invalidateQueries({ queryKey: queryKeys.knowledge(agent.id) });
      }
      // The creator left mid-publish: do not pull them back or toast over the page they chose.
      if (stopped()) return;
      toast.success(`Published in ${formatElapsed(took)}`, {
        description:
          failed > 0
            ? `${failed} knowledge file${failed === 1 ? "" : "s"} could not be uploaded. Add ${failed === 1 ? "it" : "them"} again in the Knowledge tab.`
            : knowledge.length > 0
              ? "Knowledge files are being indexed."
              : undefined,
      });
      leaving.current = true;
      setPhase("editing");
      navigate(`/studio/agents/${agent.id}?tab=overview`, { replace: true });
    },
    onError: (err) => {
      if (stopped()) return;
      setPhase("editing");
      if (hasCode(err, ErrorCodes.slug_taken, ErrorCodes.slug_invalid, ErrorCodes.slug_reserved, ErrorCodes.model_not_allowed))
        setStep("golive");
      else if (hasCode(err, ErrorCodes.pricing_invalid)) setStep("pricing");
      else if (hasCode(err, ErrorCodes.record_schema_invalid)) setStep("identity");
      else if (hasCode(err, ErrorCodes.skill_not_found)) setStep("upload");
    },
  });

  const goNext = () => {
    if (Object.keys(errors).length > 0) {
      setShowErrors(true);
      setErrorPing((n) => n + 1);
      return;
    }
    setShowErrors(false);
    const n = nextStep(step);
    if (n) setStep(n);
    else publish.mutate();
  };

  const requestRedraft = (quality: DraftQuality) => {
    if (!skill) return;
    if (identityEdited(form.identity, drafted)) setConfirmReplace({ kind: "redraft", quality });
    else startDraft(skill, quality);
  };

  const shownErrors = showErrors ? errors : {};
  const errorSummary = showErrors ? Object.values(errors)[0] : undefined;
  const leaveAnyway = () => {
    publishAbort.current?.abort();
    blocker.proceed?.();
  };
  const isLast = step === "golive";

  return (
    <PageBody className="max-w-4xl">
      <PageHeader
        title="Publish a skill"
        description="Drop a SKILL.md, let Gemini draft the listing, set pricing and go live."
        back={{ to: "/studio", label: "Studio" }}
        actions={
          startedAt !== null ? (
            <div
              className={cn(
                "inline-flex items-center gap-2 rounded-chip border px-3 py-1 text-sm font-medium",
                finishedMs !== null ? "border-success/30 bg-success-soft text-success" : "border-border bg-surface text-muted",
              )}
              aria-live="off"
              data-testid="wizard-timer"
            >
              {finishedMs !== null ? <CheckCircle2 aria-hidden className="h-4 w-4" /> : <Timer aria-hidden className="h-4 w-4" />}
              <span className="font-mono tabular-nums">
                {finishedMs !== null ? `Published in ${formatElapsed(finishedMs)}` : formatElapsed(elapsed)}
              </span>
            </div>
          ) : null
        }
      />

      <Stepper step={step} reachable={reachable} onSelect={(s) => setStep(s)} disabled={busy || draft.isPending} />

      <Card>
        <CardBody className="sm:p-6">
          {busy ? (
            <div className="space-y-4" role="status" aria-live="polite">
              <div className="flex items-center gap-3">
                <Rocket aria-hidden className="h-5 w-5 text-primary" />
                <p className="font-semibold">
                  {phase === "creating" ? "Publishing your agent…" : "Agent is live. Uploading knowledge files…"}
                </p>
              </div>
              {phase === "uploading" ? <UploadList items={uploads} /> : null}
              <p className="text-[13px] text-muted">Keep this page open until the uploads finish.</p>
            </div>
          ) : step === "upload" ? (
            <div className="space-y-5">
              <SkillDropzone onPick={onPick} busy={skillUpload.isPending} disabled={skillUpload.isPending} compact={Boolean(skill)} />
              {skill ? (
                <div className="flex items-center gap-3 rounded-card border border-border p-4">
                  <BookOpen aria-hidden className="h-5 w-5 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{skill.name}</div>
                    <div className="truncate text-[13px] text-muted">
                      {skill.files.length} file{skill.files.length === 1 ? "" : "s"} · {skill.description || "No description"}
                    </div>
                  </div>
                </div>
              ) : null}
              {skillUpload.error ? <ErrorLine error={skillUpload.error} /> : null}
              {problems.length > 0 ? (
                <ul className="list-disc space-y-0.5 pl-5 text-sm text-danger" role="alert">
                  {problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              ) : null}
              <div>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-semibold">Knowledge files (optional)</div>
                    <div className="text-[13px] text-muted">
                      PDF, DOCX, TXT, MD or CSV up to 10 MB each. Uploaded one by one right after you publish.
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    icon={<FilePlus2 className="h-4 w-4" />}
                    onClick={() => knowledgeInput.current?.click()}
                  >
                    Add files
                  </Button>
                  <input
                    ref={knowledgeInput}
                    type="file"
                    multiple
                    accept=".pdf,.docx,.txt,.md,.csv"
                    className="hidden"
                    onChange={(e) => {
                      const files = filesFromInput(e.target.files).map((p) => p.file).filter((f) => isKnowledgeFile(f.name));
                      e.target.value = "";
                      addKnowledge(files);
                    }}
                  />
                </div>
                {knowledge.length > 0 ? (
                  <ul className="divide-y divide-border rounded-card border border-border">
                    {knowledge.map((f) => (
                      <li key={`${f.name}:${f.size}`} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                        <span className="min-w-0 flex-1 truncate">{f.name}</span>
                        <span className="shrink-0 font-mono text-xs text-subtle">{formatBytes(f.size)}</span>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Remove ${f.name}`}
                          onClick={() => setKnowledge((k) => k.filter((x) => x !== f))}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </div>
          ) : step === "identity" ? (
            draft.isPending ? (
              <DraftingSkeleton skillName={skill?.name ?? ""} quality={draftQuality} />
            ) : (
              <div className="space-y-4">
                {draftFallback ? (
                  <ErrorLine
                    error={draftFallback}
                    message={
                      drafted
                        ? "Gemini could not draft the listing this time. We filled in a starting point you can edit, or try Redraft."
                        : undefined
                    }
                  />
                ) : null}
                <IdentityStep
                  key={draftRev}
                  identity={form.identity}
                  records={form.records}
                  errors={shownErrors}
                  onIdentity={(identity) => setForm((f) => ({ ...f, identity }))}
                  onRecords={(records) => setForm((f) => ({ ...f, records }))}
                  onRedraft={requestRedraft}
                  redrafting={draft.isPending}
                  canRedraft={Boolean(skill)}
                  onAvatarBusy={setAvatarBusy}
                />
              </div>
            )
          ) : step === "pricing" ? (
            <PricingFields
              value={form.pricing}
              errors={shownErrors}
              onChange={(pricing) => setForm((f) => ({ ...f, pricing }))}
            />
          ) : (
            <GoLiveStep
              form={effective}
              onChange={setForm}
              slugStatus={slugStatus}
              errors={shownErrors}
              skill={skill}
              knowledgeFiles={knowledge}
              publicBase={window.location.host}
            />
          )}

          {publish.error && !busy ? <ErrorLine className="mt-5" error={publish.error} /> : null}
        </CardBody>
        {!busy ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3 sm:px-6">
            <Button
              variant="ghost"
              icon={<ArrowLeft className="h-4 w-4" />}
              disabled={!prevStep(step) || draft.isPending}
              onClick={() => {
                const p = prevStep(step);
                if (p) setStep(p);
              }}
            >
              Back
            </Button>
            <div className="flex min-w-0 items-center gap-3">
              {errorSummary ? <span className="hidden text-[13px] text-danger sm:inline">{errorSummary}</span> : null}
              <Button
                variant="primary"
                size={isLast ? "lg" : "md"}
                loading={publish.isPending}
                disabled={
                  (step === "upload" && (!skill || skillUpload.isPending)) || draft.isPending || avatarBusy || publish.isPending
                }
                icon={isLast ? <Rocket className="h-4 w-4" /> : undefined}
                onClick={goNext}
              >
                {isLast ? "Publish" : "Continue"}
                {!isLast ? <ArrowRight aria-hidden className="h-4 w-4" /> : null}
              </Button>
            </div>
            {/* Phones: the summary wraps below the buttons instead of being hidden. */}
            {errorSummary ? (
              <p className="w-full text-right text-[13px] text-danger sm:hidden" role="alert">
                {errorSummary}
              </p>
            ) : null}
          </div>
        ) : null}
      </Card>

      <Dialog
        open={blocker.state === "blocked"}
        onOpenChange={(o) => !o && blocker.reset?.()}
        title={phase === "uploading" ? "Uploads are still running" : "Publishing is in progress"}
        description={
          phase === "uploading"
            ? "Your agent is published. Leaving now stops the remaining knowledge uploads; you can add them later in the Knowledge tab."
            : `Your agent is still being created. If you leave now it may still go live${knowledge.length > 0 ? " without its knowledge files" : ""}, so check Studio before publishing again.`
        }
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => blocker.reset?.()}>
              Stay
            </Button>
            <Button variant="danger" onClick={leaveAnyway}>
              Leave anyway
            </Button>
          </>
        }
      />

      <Dialog
        open={confirmReplace !== null}
        onOpenChange={(o) => !o && setConfirmReplace(null)}
        title="Replace your edits?"
        description={
          confirmReplace?.kind === "skill"
            ? "The new skill gets a fresh draft that overwrites the name, persona, greeting, questions, capabilities, pricing and lead capture."
            : "A new draft overwrites the name, persona, greeting, questions, capabilities, pricing and lead capture."
        }
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setConfirmReplace(null)}>
              Keep my edits
            </Button>
            <Button
              onClick={() => {
                const pending = confirmReplace;
                setConfirmReplace(null);
                if (pending?.kind === "skill") startSkillUpload(pending.upload);
                else if (skill && pending) startDraft(skill, pending.quality);
              }}
            >
              {confirmReplace?.kind === "skill" ? "Use the new skill" : "Redraft"}
            </Button>
          </>
        }
      />
    </PageBody>
  );
}
