import { initializeApp, type FirebaseApp } from "firebase/app";
import { connectAuthEmulator, getAuth, type Auth } from "firebase/auth";
import { config } from "./config";

/**
 * Firebase is used for authentication only (SPEC §11.5). Initialised lazily so modules that
 * merely import the API client (and tests) do not touch the SDK.
 */

let app: FirebaseApp | null = null;
let auth: Auth | null = null;

export function getFirebaseAuth(): Auth {
  if (auth) return auth;
  app = initializeApp(config.firebase);
  auth = getAuth(app);
  if (config.useAuthEmulator) {
    connectAuthEmulator(auth, config.authEmulatorUrl, { disableWarnings: true });
  }
  return auth;
}

/**
 * Current user's Firebase ID token, or null when signed out. Waits for the persisted session to
 * be restored first, so the very first request after a reload still carries the token.
 * `forceRefresh` is used for the single 401 retry (SPEC §11.6).
 */
export async function getIdToken(forceRefresh = false): Promise<string | null> {
  const a = getFirebaseAuth();
  await a.authStateReady();
  const user = a.currentUser;
  if (!user) return null;
  return user.getIdToken(forceRefresh);
}
