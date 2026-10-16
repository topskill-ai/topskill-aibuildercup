/**
 * Step 1 drop target: a skill folder, a .zip of it, or a loose SKILL.md — plus optional knowledge
 * documents dropped in the same gesture (SPEC §11.4).
 */
import { FileText, FolderUp, Sparkles, Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { filesFromDataTransfer, filesFromInput, type PickedFile } from "./files";
import { SAMPLE_SKILL_MD_URL } from "./sample";

export function SkillDropzone({
  onPick,
  disabled,
  busy,
  compact,
  onSample,
  sampleLoading,
}: {
  onPick: (files: PickedFile[]) => void;
  /** Load the bundled sample skill (career coach + guide PDF) instead of the creator's own files. */
  onSample?: () => void;
  sampleLoading?: boolean;
  disabled?: boolean;
  busy?: boolean;
  /** Smaller variant once a skill is loaded ("Replace skill"). */
  compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const folderInput = useRef<HTMLInputElement | null>(null);
  const filesInput = useRef<HTMLInputElement | null>(null);

  const onDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    if (disabled) return;
    const picked = await filesFromDataTransfer(e.dataTransfer);
    if (picked.length > 0) onPick(picked);
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      data-testid="skill-dropzone"
      className={cn(
        "flex flex-col items-center justify-center rounded-card border-2 border-dashed text-center transition-colors",
        compact ? "px-4 py-6" : "px-6 py-12",
        over ? "border-primary bg-primary-soft" : "border-border bg-sunken",
        disabled && "opacity-60",
      )}
    >
      {!compact ? (
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-primary-soft text-primary">
          <Upload aria-hidden className="h-5 w-5" />
        </div>
      ) : null}
      <p className="text-[15px] font-semibold">
        {busy ? "Uploading your skill…" : compact ? "Drop another skill to replace it" : "Drop your skill here"}
      </p>
      {!compact ? (
        <p className="mt-1 max-w-md text-sm text-muted">
          A skill folder, a .zip of it, or a single SKILL.md. Add PDFs or DOCX files in the same drop to use them as
          knowledge.
        </p>
      ) : null}
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        <Button
          variant="outline"
          size="sm"
          icon={<FolderUp className="h-4 w-4" />}
          disabled={disabled}
          onClick={() => folderInput.current?.click()}
        >
          Choose folder
        </Button>
        <Button
          variant="outline"
          size="sm"
          icon={<FileText className="h-4 w-4" />}
          disabled={disabled}
          onClick={() => filesInput.current?.click()}
        >
          Choose files
        </Button>
      </div>
      {onSample && !compact ? (
        <div className="mt-5 flex max-w-md flex-col items-center gap-1 border-t border-border pt-4">
          <p className="text-[13px] text-muted">No skill at hand?</p>
          <Button
            variant="ghost"
            size="sm"
            icon={<Sparkles className="h-4 w-4" />}
            disabled={disabled}
            loading={sampleLoading}
            onClick={onSample}
          >
            Try with a sample skill
          </Button>
          <p className="text-xs text-subtle">
            A career coach skill with a short coaching guide PDF.{" "}
            <a href={SAMPLE_SKILL_MD_URL} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">
              View its SKILL.md
            </a>
          </p>
        </div>
      ) : null}
      <input
        ref={folderInput}
        type="file"
        className="hidden"
        multiple
        // Non-standard attribute for folder pickers; React passes it through as a string.
        {...{ webkitdirectory: "" }}
        onChange={(e) => {
          const picked = filesFromInput(e.target.files);
          e.target.value = "";
          if (picked.length > 0) onPick(picked);
        }}
      />
      <input
        ref={filesInput}
        type="file"
        className="hidden"
        multiple
        accept=".md,.zip,.pdf,.txt,.csv,.docx"
        onChange={(e) => {
          const picked = filesFromInput(e.target.files);
          e.target.value = "";
          if (picked.length > 0) onPick(picked);
        }}
      />
    </div>
  );
}
