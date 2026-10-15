import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { RouterProvider } from "react-router/dom";
import { queryClient } from "./api/queryClient";
import { AuthProvider } from "./lib/auth";
import { createAppRouter } from "./router";

export function App() {
  const [router] = useState(createAppRouter);
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  );
}
