import { SendHorizontal, Square } from "lucide-react";
import { useRef, useState, type Dispatch, type KeyboardEvent, type SetStateAction } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/cn";

export const MAX_MESSAGE_CHARS = 8000;

export interface ComposerProps {
  placeholder: string;
  /** Draft text, owned by the parent so a paywall Resend can clear it. */
  draft: string;
  onDraftChange: Dispatch<SetStateAction<string>>;
  streaming: boolean;
  disabled?: boolean;
  /** Resolves true when the draft may be cleared. */
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
  className?: string;
}

/** Autosizing textarea: Enter sends, Shift+Enter adds a newline; Stop while streaming (SPEC §11.4). */
export function Composer({
  placeholder,
  draft,
  onDraftChange,
  streaming,
  disabled,
  onSend,
  onStop,
  className,
}: ComposerProps) {
  const [sending, setSending] = useState(false);
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const canSend = draft.trim().length > 0 && !streaming && !sending && !disabled;

  const submit = async () => {
    if (!canSend) return;
    const text = draft;
    onDraftChange("");
    setSending(true);
    try {
      // `onSend` resolves when the turn ends; a refused send (sign-in cancelled, paywall, error)
      // puts the text back unless the visitor already typed something new.
      const accepted = await onSend(text);
      if (!accepted) onDraftChange((current) => current || text);
    } finally {
      setSending(false);
      ref.current?.focus();
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <form
      className={cn("flex min-w-0 items-end gap-2", className)}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <Textarea
        ref={ref}
        value={draft}
        onChange={(e) => onDraftChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-label="Message"
        autoResize
        rows={1}
        maxRows={6}
        maxLength={MAX_MESSAGE_CHARS}
        disabled={disabled}
        className="min-h-11 flex-1"
      />
      {streaming ? (
        <Button
          variant="outline"
          className="h-11"
          onClick={onStop}
          icon={<Square aria-hidden className="h-3.5 w-3.5 fill-current" />}
        >
          Stop
        </Button>
      ) : (
        <Button
          type="submit"
          className="h-11"
          disabled={!canSend}
          loading={sending}
          aria-label="Send message"
          icon={<SendHorizontal aria-hidden className="h-4 w-4" />}
        >
          <span className="hidden sm:inline">Send</span>
        </Button>
      )}
    </form>
  );
}
