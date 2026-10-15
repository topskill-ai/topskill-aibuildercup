import { clsx, type ClassValue } from "clsx";

/** Join class names conditionally (thin wrapper over clsx so every lane imports one helper). */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
