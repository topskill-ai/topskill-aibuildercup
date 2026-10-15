/**
 * Authentication state (SPEC §11.5).
 *
 * Identity comes from Firebase Auth (`onIdTokenChanged`) or, when VITE_DEV_AUTH=1, from the
 * developer login stored in `localStorage["topskill.devUser"]`. The profile (`Me`) is always read
 * from `GET /api/me`, which also creates it server-side on first sign-in.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FirebaseError } from "firebase/app";
import {
  createUserWithEmailAndPassword,
  GoogleAuthProvider,
  onIdTokenChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as firebaseSignOut,
  updateProfile,
} from "firebase/auth";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import { hasCode } from "@/api/errors";
import { queryKeys } from "@/api/queryClient";
import type { Me, Role, Wallet } from "@/api/types";
import { config } from "./config";
import { getDevUser, setDevUser, DEV_UID_RE } from "./devUser";
import { getFirebaseAuth } from "./firebase";

export type AuthStatus = "loading" | "signed_out" | "signed_in";

type Identity = { kind: "firebase" | "dev"; uid: string } | null;

export interface AuthContextValue {
  status: AuthStatus;
  /** Profile from `GET /api/me`; null while loading, when signed out, or if the profile failed to load. */
  me: Me | null;
  /** Error from the last `GET /api/me` (signed in but profile unavailable). */
  meError: unknown;
  isCreator: boolean;
  /** True when developer login is enabled in this build (VITE_DEV_AUTH=1). */
  devAuthEnabled: boolean;
  /** True when the Google button should be shown (VITE_GOOGLE_SIGNIN=1). */
  googleEnabled: boolean;
  signIn: (email: string, password: string) => Promise<Me>;
  signUp: (email: string, password: string, name: string) => Promise<Me>;
  signInWithGoogle: () => Promise<Me>;
  signOut: () => Promise<void>;
  devSignIn: (uid: string, role: Role, email?: string) => Promise<Me>;
  /** Replace the cached profile (e.g. after `POST /api/me/become-creator`). */
  setMe: (me: Me) => void;
  refetchMe: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function meKey(uid: string | null) {
  return [...queryKeys.me, uid] as const;
}

function fetchMe(): Promise<Me> {
  return api.get<Me>("/api/me");
}

/** Friendly English message for Firebase Auth errors (UI shows `err.message`). */
// eslint-disable-next-line react-refresh/only-export-components
export function authErrorMessage(err: unknown): string {
  if (err instanceof FirebaseError) {
    switch (err.code) {
      case "auth/invalid-credential":
      case "auth/wrong-password":
      case "auth/user-not-found":
      case "auth/invalid-login-credentials":
        return "Incorrect email or password.";
      case "auth/email-already-in-use":
        return "An account with this email already exists. Sign in instead.";
      case "auth/invalid-email":
        return "Enter a valid email address.";
      case "auth/weak-password":
      case "auth/password-does-not-meet-requirements":
        return "Choose a stronger password (at least 6 characters).";
      case "auth/too-many-requests":
        return "Too many attempts. Wait a moment and try again.";
      case "auth/network-request-failed":
        return "Network error. Check your connection and try again.";
      case "auth/popup-closed-by-user":
      case "auth/cancelled-popup-request":
        return "Sign-in was cancelled.";
      case "auth/popup-blocked":
        return "The sign-in popup was blocked by the browser.";
      case "auth/operation-not-allowed":
        return "This sign-in method is not enabled.";
      default:
        return "Sign-in failed. Please try again.";
    }
  }
  if (err instanceof Error && err.message) return err.message;
  return "Sign-in failed. Please try again.";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const initialDev = getDevUser();
  // undefined = not known yet (Firebase still restoring the session).
  const [identity, setIdentity] = useState<Identity | undefined>(
    initialDev ? { kind: "dev", uid: initialDev.uid } : undefined,
  );

  useEffect(() => {
    const auth = getFirebaseAuth();
    return onIdTokenChanged(auth, (user) => {
      setIdentity((prev) => {
        const dev = getDevUser();
        if (dev) return prev ?? { kind: "dev", uid: dev.uid };
        if (!user) return null;
        if (prev && prev.kind === "firebase" && prev.uid === user.uid) return prev;
        return { kind: "firebase", uid: user.uid };
      });
    });
  }, []);

  const uid = identity?.uid ?? null;
  const meQuery = useQuery({
    queryKey: meKey(uid),
    queryFn: fetchMe,
    enabled: Boolean(uid),
    staleTime: 60_000,
    retry: (count, err) => !hasCode(err, "unauthenticated") && count < 2,
  });

  const prefetchWallet = useCallback(() => {
    void queryClient.prefetchQuery({ queryKey: queryKeys.wallet, queryFn: () => api.get<Wallet>("/api/wallet") });
  }, [queryClient]);

  /** Load the profile for a freshly signed-in identity and warm the wallet (opens it server-side). */
  const loadProfile = useCallback(
    async (nextUid: string): Promise<Me> => {
      const me = await queryClient.fetchQuery({ queryKey: meKey(nextUid), queryFn: fetchMe, staleTime: 0 });
      prefetchWallet();
      return me;
    },
    [queryClient, prefetchWallet],
  );

  const signIn = useCallback(
    async (email: string, password: string) => {
      setDevUser(null);
      try {
        const cred = await signInWithEmailAndPassword(getFirebaseAuth(), email.trim(), password);
        setIdentity({ kind: "firebase", uid: cred.user.uid });
        return await loadProfile(cred.user.uid);
      } catch (err) {
        if (err instanceof FirebaseError) throw new Error(authErrorMessage(err), { cause: err });
        throw err;
      }
    },
    [loadProfile],
  );

  const signUp = useCallback(
    async (email: string, password: string, name: string) => {
      setDevUser(null);
      try {
        const cred = await createUserWithEmailAndPassword(getFirebaseAuth(), email.trim(), password);
        const displayName = name.trim();
        if (displayName) {
          await updateProfile(cred.user, { displayName });
          await cred.user.getIdToken(true); // new token carries the `name` claim
        }
        setIdentity({ kind: "firebase", uid: cred.user.uid });
        let me = await loadProfile(cred.user.uid);
        if (displayName && me.display_name !== displayName) {
          me = await api.patch<Me>("/api/me", { display_name: displayName.slice(0, 60) });
          queryClient.setQueryData(meKey(cred.user.uid), me);
        }
        return me;
      } catch (err) {
        if (err instanceof FirebaseError) throw new Error(authErrorMessage(err), { cause: err });
        throw err;
      }
    },
    [loadProfile, queryClient],
  );

  const signInWithGoogle = useCallback(async () => {
    setDevUser(null);
    try {
      const cred = await signInWithPopup(getFirebaseAuth(), new GoogleAuthProvider());
      setIdentity({ kind: "firebase", uid: cred.user.uid });
      return await loadProfile(cred.user.uid);
    } catch (err) {
      if (err instanceof FirebaseError) throw new Error(authErrorMessage(err), { cause: err });
      throw err;
    }
  }, [loadProfile]);

  const devSignIn = useCallback(
    async (devUid: string, role: Role, email?: string) => {
      if (!config.devAuth) throw new Error("Developer login is disabled in this build.");
      const trimmed = devUid.trim();
      if (!DEV_UID_RE.test(trimmed)) {
        throw new Error("User id must be 3–64 characters: letters, digits, '-' or '_'.");
      }
      await firebaseSignOut(getFirebaseAuth()).catch(() => undefined);
      queryClient.clear();
      setDevUser({ uid: trimmed, role, email: email?.trim() || undefined });
      setIdentity({ kind: "dev", uid: trimmed });
      return loadProfile(trimmed);
    },
    [loadProfile, queryClient],
  );

  const signOut = useCallback(async () => {
    setDevUser(null);
    await firebaseSignOut(getFirebaseAuth()).catch(() => undefined);
    setIdentity(null);
    queryClient.clear();
  }, [queryClient]);

  const setMe = useCallback(
    (me: Me) => {
      queryClient.setQueryData(meKey(me.uid), me);
    },
    [queryClient],
  );

  const { refetch } = meQuery;
  const refetchMe = useCallback(() => {
    void refetch();
  }, [refetch]);

  const value = useMemo<AuthContextValue>(() => {
    let status: AuthStatus;
    if (identity === undefined) status = "loading";
    else if (identity === null) status = "signed_out";
    else if (meQuery.isPending && meQuery.fetchStatus !== "idle") status = "loading";
    else status = "signed_in";
    const me = identity ? (meQuery.data ?? null) : null;
    return {
      status,
      me,
      meError: identity ? meQuery.error : null,
      isCreator: me?.role === "creator",
      devAuthEnabled: config.devAuth,
      googleEnabled: config.googleSignIn,
      signIn,
      signUp,
      signInWithGoogle,
      signOut,
      devSignIn,
      setMe,
      refetchMe,
    };
  }, [
    identity,
    meQuery.isPending,
    meQuery.fetchStatus,
    meQuery.data,
    meQuery.error,
    signIn,
    signUp,
    signInWithGoogle,
    signOut,
    devSignIn,
    setMe,
    refetchMe,
  ]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
