import { ArrowUp, Square } from "lucide-react";
import { useRef, type FormEvent, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  hint?: string;
}

const MAX_CHARS = 4000;

/** Task box: Enter sends, Shift+Enter adds a line, Stop while the assistant works. */
export function Composer({ value, onChange, onSubmit, onStop, streaming, hint }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const canSend = value.trim().length > 0 && !streaming;

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!canSend) return;
    onSubmit(value.trim());
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form onSubmit={submit} className="rounded-card border border-border bg-surface p-2 shadow-pop focus-within:border-primary">
      <label htmlFor="assistant-task" className="sr-only">
        Task for the Enterprise Assistant
      </label>
      <div className="flex items-end gap-2">
        <Textarea
          id="assistant-task"
          ref={ref}
          autoResize
          rows={1}
          maxRows={8}
          maxLength={MAX_CHARS}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Describe a task, e.g. our new hire needs a work visa plan and a resume review…"
          className="border-0 bg-transparent shadow-none hover:border-0 focus:ring-0"
        />
        {streaming ? (
          <Button type="button" variant="outline" size="icon" aria-label="Stop" onClick={onStop}>
            <Square aria-hidden className="h-4 w-4 fill-current" />
          </Button>
        ) : (
          <Button type="submit" size="icon" aria-label="Send" disabled={!canSend}>
            <ArrowUp aria-hidden className="h-4 w-4" />
          </Button>
        )}
      </div>
      {hint ? <p className="px-3 pb-1 text-2xs text-subtle">{hint}</p> : null}
    </form>
  );
}
