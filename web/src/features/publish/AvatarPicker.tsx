/**
 * Avatar choice for an agent: generate with Gemini from the drafted `avatar_prompt`, upload an
 * image, or fall back to the monogram (SPEC §11.4 Step 2, §8.7 Avatar).
 */
import { ImagePlus, WandSparkles, X } from "lucide-react";
import { useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import type { AccentColor } from "@/api/types";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { ErrorLine } from "@/components/ui/ErrorLine";
import { generateAvatar, uploadAvatar } from "./api";
import type { AvatarRef } from "./wizard";

const AVATAR_TYPES = ["image/png", "image/jpeg", "image/webp"];
const AVATAR_MAX_BYTES = 5 * 1024 * 1024;

export function AvatarPicker({
  value,
  title,
  accent,
  prompt,
  onChange,
  disabled,
  onBusyChange,
}: {
  value: AvatarRef | null;
  title: string;
  accent: AccentColor;
  /** `avatar_prompt` from the draft (falls back to the title). */
  prompt: string;
  onChange: (next: AvatarRef | null) => void;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
}) {
  const input = useRef<HTMLInputElement | null>(null);

  const generate = useMutation({
    mutationFn: () => generateAvatar((prompt.trim() || `An expert assistant called ${title || "Agent"}`).slice(0, 600), accent),
    onMutate: () => onBusyChange?.(true),
    onSettled: () => onBusyChange?.(false),
    onSuccess: (ref) => onChange(ref),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      if (!AVATAR_TYPES.includes(file.type)) throw new Error("Use a PNG, JPEG or WebP image.");
      if (file.size > AVATAR_MAX_BYTES) throw new Error("Images are limited to 5 MB.");
      return uploadAvatar(file);
    },
    onMutate: () => onBusyChange?.(true),
    onSettled: () => onBusyChange?.(false),
    onSuccess: (ref) => onChange(ref),
  });

  const busy = generate.isPending || upload.isPending;
  const error = generate.error ?? upload.error;

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-4">
        <div className="relative">
          <Avatar src={value?.avatar_url} name={title || "Agent"} accent={accent} size={72} ring />
          {generate.isPending ? (
            <span className="skeleton-shimmer absolute inset-0 rounded-full opacity-80" aria-hidden />
          ) : null}
        </div>
        <div className="flex min-w-0 flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            icon={<WandSparkles className="h-4 w-4" />}
            loading={generate.isPending}
            disabled={disabled || busy}
            onClick={() => generate.mutate()}
          >
            {generate.isPending ? "Painting…" : value ? "Regenerate" : "Generate avatar"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            icon={<ImagePlus className="h-4 w-4" />}
            loading={upload.isPending}
            disabled={disabled || busy}
            onClick={() => input.current?.click()}
          >
            Upload
          </Button>
          {value ? (
            <Button
              variant="ghost"
              size="sm"
              icon={<X className="h-4 w-4" />}
              disabled={disabled || busy}
              onClick={() => onChange(null)}
            >
              Use monogram
            </Button>
          ) : null}
          <input
            ref={input}
            type="file"
            accept={AVATAR_TYPES.join(",")}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) upload.mutate(file);
            }}
          />
        </div>
      </div>
      {generate.isPending ? <p className="mt-2 text-[13px] text-muted">Gemini is painting an avatar (about 8 s)…</p> : null}
      {error ? (
        <ErrorLine
          className="mt-2"
          error={error}
          message={generate.error ? "The avatar could not be generated. Upload one or keep the monogram." : undefined}
        />
      ) : null}
    </div>
  );
}
