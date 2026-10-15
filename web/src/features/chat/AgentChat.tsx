import { SquarePen } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ErrorCodes, hasCode } from "@/api/errors";
import type { PublicAgent } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { SkeletonText } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { useBillingStatus } from "./api";
import { ColdStart } from "./AgentInfo";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { Paywall } from "./Paywall";
import { BillingHint, UnavailableState } from "./StateViews";
import { useChat } from "./useChat";
import { VoiceButton, VoicePanel } from "@/features/voice/VoicePanel";
import { useVoice, useVoiceConfig } from "@/features/voice/useVoice";

/**
 * Scroll the window to the bottom while new content streams in, unless the reader scrolled up.
 * An empty transcript never scrolls (the agent header stays in view on first load); a new
 * message re-pins so the visitor always sees the turn they just started.
 */
function useStickToBottom(count: number, dep: unknown) {
  const pinned = useRef(true);
  const lastCount = useRef(0);
  useEffect(() => {
    const onScroll = () => {
      const el = document.scrollingElement ?? document.documentElement;
      pinned.current = el.scrollHeight - (window.scrollY + window.innerHeight) < 160;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    const grew = count > lastCount.current;
    lastCount.current = count;
    if (count === 0) return;
    if (grew) pinned.current = true;
    if (!pinned.current) return;
    const el = document.scrollingElement ?? document.documentElement;
    if (el.scrollHeight > window.innerHeight) window.scrollTo({ top: el.scrollHeight });
  }, [count, dep]);
}

/**
 * Chat column of the agent page: transcript or cold start, billing hint, composer, paywall and
 * the temporarily-unavailable state (SPEC §11.4).
 */
export function AgentChat({ agent }: { agent: PublicAgent }) {
  const auth = useAuth();
  const signedIn = auth.status === "signed_in";
  const chat = useChat(agent.slug, agent);
  const billing = useBillingStatus(agent.slug, signedIn);
  const status = signedIn ? billing.data : null;
  const [draft, setDraft] = useState("");
  const voiceConfig = useVoiceConfig();
  const voice = useVoice({
    slug: agent.slug,
    agentTitle: agent.title,
    conversationId: chat.conversationId,
    onRound: chat.adoptConversation,
    onEnded: (cid) => {
      if (cid) chat.adoptConversation(cid);
    },
    onPaywall: chat.openTopUp,
    onStaleConversation: chat.newConversation,
  });
  const voiceOpen = voice.state.phase !== "idle";
  const voiceAvailable = voiceConfig.data?.enabled === true;

  const lastMessage = chat.messages[chat.messages.length - 1];
  useStickToBottom(chat.messages.length, `${chat.messages.length}:${lastMessage?.text.length ?? 0}:${lastMessage?.tools.length ?? 0}`);

  const billingUnavailable =
    hasCode(billing.error, ErrorCodes.agent_offline, ErrorCodes.agent_not_found) ||
    (status != null && !status.owner_available);
  const unavailableError = chat.gate?.kind === "unavailable" ? chat.gate.error : billingUnavailable ? billing.error : null;
  if (chat.gate?.kind === "unavailable" || billingUnavailable) {
    return <UnavailableState error={unavailableError} />;
  }

  const paywall = chat.gate?.kind === "paywall" ? chat.gate : null;
  const hasMessages = chat.messages.length > 0;

  return (
    <div className="flex min-w-0 flex-col">
      <div className="min-w-0 rounded-card border border-border bg-surface p-4 sm:p-6">
        {hasMessages ? (
          <>
            <div className="mb-4 flex items-center justify-between gap-2 border-b border-border pb-3">
              <span className="text-sm font-medium text-muted">Conversation</span>
              <Button
                variant="ghost"
                size="sm"
                disabled={chat.streaming || voiceOpen}
                onClick={chat.newConversation}
                icon={<SquarePen aria-hidden className="h-3.5 w-3.5" />}
              >
                New conversation
              </Button>
            </div>
            <MessageList
              messages={chat.messages}
              ctx={{ pricingMode: agent.pricing.mode, freeRounds: agent.pricing.free_rounds }}
            />
          </>
        ) : chat.restoring ? (
          <div role="status" aria-label="Restoring conversation">
            <SkeletonText lines={4} />
          </div>
        ) : (
          <ColdStart agent={agent} onStarter={(prompt) => void chat.send(prompt)} disabled={chat.streaming || voiceOpen} />
        )}
      </div>

      <div
        className={cn(
          "sticky bottom-0 z-10 -mx-4 mt-3 border-t border-border bg-bg/95 px-4 pt-3 pb-[max(12px,env(safe-area-inset-bottom))]",
          "backdrop-blur supports-[backdrop-filter]:bg-bg/80 sm:mx-0 sm:rounded-card sm:border sm:bg-surface sm:px-4",
        )}
      >
        <BillingHint
          status={status}
          pricing={agent.pricing}
          signedIn={signedIn}
          loading={signedIn && billing.isPending}
          error={signedIn ? billing.error : null}
          onRetry={() => void billing.refetch()}
          onTopUp={chat.openTopUp}
          className="mb-2"
        />
        {voiceOpen ? (
          <VoicePanel voice={voice} agentTitle={agent.title} />
        ) : (
          <div className="flex min-w-0 items-end gap-2">
            <Composer
              className="flex-1"
              placeholder={agent.input_placeholder || `Ask ${agent.title} anything`}
              draft={draft}
              onDraftChange={setDraft}
              streaming={chat.streaming}
              onSend={chat.send}
              onStop={chat.stop}
            />
            {voiceAvailable ? (
              <VoiceButton onClick={() => void voice.start()} disabled={chat.streaming || chat.restoring} />
            ) : null}
          </div>
        )}
      </div>

      <Paywall
        open={paywall !== null}
        onClose={chat.closeGate}
        error={paywall?.error ?? null}
        pendingText={paywall?.text ?? null}
        billing={status}
        billingUpdatedAt={billing.dataUpdatedAt}
        onResend={(text) => {
          chat.closeGate();
          // The blocked text is still in the composer; clear it once the resend is accepted.
          setDraft((current) => (current.trim() === text.trim() ? "" : current));
          void chat.send(text).then((accepted) => {
            if (!accepted) setDraft((current) => current || text);
          });
        }}
      />
    </div>
  );
}
